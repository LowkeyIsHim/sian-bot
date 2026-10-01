"""
/news - configurable auto-posting news feed for a group. Pick categories
(or "all"), set the posting interval in hours, toggle on/off, or post
right now with /news now. Runs on RSS feeds - the legitimate way to pull
headlines.

Posts are plain text only: no links, no emojis, no markdown. Each section
is labelled with its category and source, e.g. "WORLD · BBC".

A background loop (started from bot.py's post_init) checks every 5
minutes whether any enabled group's interval has elapsed.
"""

import asyncio
import html
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import CommandHandler, ContextTypes

import access
from branding import LINE

logger = logging.getLogger(__name__)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PERSISTENT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))
SETTINGS_FILE = os.path.join(_PERSISTENT_DIR, "news_settings.json")
SEEN_FILE = os.path.join(_PERSISTENT_DIR, "news_seen.json")

CHECK_INTERVAL_SECONDS = 300  # background loop wakes up every 5 min
MAX_ITEMS_PER_POST = 3        # cap headlines per category per post
SEEN_CAP_PER_CATEGORY = 50
MAX_TITLE_CHARS = 120

CATEGORIES = {
    "world": {"label": "WORLD", "source": "BBC", "feed": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    "political": {"label": "POLITICS", "source": "BBC", "feed": "https://feeds.bbci.co.uk/news/politics/rss.xml"},
    "crypto": {"label": "CRYPTO & MARKETS", "source": "CoinDesk", "feed": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    "tech": {"label": "TECH", "source": "TechCrunch", "feed": "https://techcrunch.com/feed/"},
    "sports": {"label": "SPORTS", "source": "BBC Sport", "feed": "https://feeds.bbci.co.uk/sport/rss.xml?edition=uk"},
    "entertainment": {"label": "ENTERTAINMENT", "source": "BBC", "feed": "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml"},
}


# ---------- storage ----------
def _load(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        logger.warning(f"{path} was unreadable, starting fresh")
        return {}


def _save(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)  # atomic - a crash can't leave a half-written file


# ---------- fetching & formatting ----------
def _clean(title: str) -> str:
    title = html.unescape(title)
    title = re.sub(r"\s+", " ", title).strip()
    if len(title) > MAX_TITLE_CHARS:
        title = title[: MAX_TITLE_CHARS - 1].rstrip() + "…"
    return title


def _fetch_feed_items(feed_url: str, limit: int = 15) -> list[dict]:
    try:
        resp = requests.get(
            feed_url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (compatible; GoddessBot/1.0)"}
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        items = []
        for item in root.iter("item"):
            title_el, link_el = item.find("title"), item.find("link")
            if title_el is None or link_el is None or not title_el.text or not link_el.text:
                continue
            items.append({"title": title_el.text.strip(), "link": link_el.text.strip()})
            if len(items) >= limit:
                break
        return items
    except Exception as e:
        logger.warning(f"Could not fetch feed {feed_url}: {e}")
        return []


def _categories_for(conf: dict) -> list[str]:
    cats = conf.get("categories", [])
    if "all" in cats:
        return list(CATEGORIES.keys())
    return [c for c in cats if c in CATEGORIES]


async def _build_post(chat_key: str, conf: dict, seen: dict):
    """Returns (text, additions). additions = {category: [links]} that must
    only be marked as seen AFTER the post is actually sent."""
    cats = _categories_for(conf)
    if not cats:
        return None, {}

    # fetch every feed at once instead of one after another
    feeds = await asyncio.gather(
        *(asyncio.to_thread(_fetch_feed_items, CATEGORIES[c]["feed"]) for c in cats)
    )

    # a story already posted (or appearing in two feeds) is never repeated
    already = {link for links in seen.get(chat_key, {}).values() for link in links}

    sections, additions = [], {}
    for cat, items in zip(cats, feeds):
        fresh = []
        for it in items:
            if it["link"] in already:
                continue
            already.add(it["link"])
            fresh.append(it)
            if len(fresh) >= MAX_ITEMS_PER_POST:
                break
        if not fresh:
            continue

        info = CATEGORIES[cat]
        lines = [f"{info['label']} · {info['source']}"]
        lines += [f"• {_clean(it['title'])}" for it in fresh]
        sections.append("\n".join(lines))
        additions[cat] = [it["link"] for it in fresh]

    if not sections:
        return None, {}

    date = datetime.now(timezone.utc).strftime("%a %d %b %Y")
    text = f"GODDESS NEWS\n{date}\n{LINE}\n\n" + "\n\n".join(sections)
    return text, additions


def _commit_seen(seen: dict, chat_key: str, additions: dict) -> None:
    chat_seen = seen.setdefault(chat_key, {})
    for cat, links in additions.items():
        chat_seen[cat] = (chat_seen.get(cat, []) + links)[-SEEN_CAP_PER_CATEGORY:]


# ---------- command ----------
async def news_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This only works inside a group.")
        return
    if not (access.is_creator(update.effective_user.id) or access.is_group_admin(update.effective_user.id)):
        return  # silent

    chat_key = str(update.effective_chat.id)
    args = context.args
    settings = _load(SETTINGS_FILE)
    conf = settings.setdefault(chat_key, {"enabled": False, "categories": [], "interval_hours": 3})

    if not args or args[0].lower() == "status":
        cats = "all" if "all" in conf["categories"] else (", ".join(conf["categories"]) or "(none set)")
        text = (
            "NEWS FEED\n"
            f"{LINE}\n\n"
            f"status: {'on' if conf['enabled'] else 'off'}\n"
            f"categories: {cats}\n"
            f"interval: every {conf['interval_hours']}h\n\n"
            f"available: {', '.join(CATEGORIES.keys())}, all\n"
            "post right now: /news now"
        )
        await update.message.reply_text(text)
        return

    sub = args[0].lower()

    if sub == "on":
        conf["enabled"] = True
        _save(SETTINGS_FILE, settings)
        await update.message.reply_text("news feed on.")
    elif sub == "off":
        conf["enabled"] = False
        _save(SETTINGS_FILE, settings)
        await update.message.reply_text("news feed off.")
    elif sub == "now":
        if not _categories_for(conf):
            await update.message.reply_text("Set categories first: /news categories world tech")
            return
        seen = _load(SEEN_FILE)
        text, additions = await _build_post(chat_key, conf, seen)
        if not text:
            await update.message.reply_text("No new headlines right now.")
            return
        try:
            await context.bot.send_message(update.effective_chat.id, text, disable_web_page_preview=True)
        except TelegramError as e:
            logger.warning(f"Could not post news to {chat_key}: {e}")
            return
        _commit_seen(seen, chat_key, additions)
        _save(SEEN_FILE, seen)
        conf["last_run"] = datetime.now(timezone.utc).isoformat()  # don't double-post on the next tick
        _save(SETTINGS_FILE, settings)
    elif sub == "categories" and len(args) > 1:
        raw = " ".join(args[1:]).lower()
        if raw.strip() == "all":
            conf["categories"] = ["all"]
        else:
            requested = [c.strip() for c in raw.replace(",", " ").split()]
            valid = [c for c in requested if c in CATEGORIES]
            if not valid:
                await update.message.reply_text(
                    f"No valid categories. Choose from: {', '.join(CATEGORIES.keys())}, all"
                )
                return
            conf["categories"] = valid
        _save(SETTINGS_FILE, settings)
        await update.message.reply_text(f"categories set to: {', '.join(conf['categories'])}")
    elif sub == "interval" and len(args) > 1 and args[1].isdigit():
        hours = max(1, int(args[1]))
        conf["interval_hours"] = hours
        _save(SETTINGS_FILE, settings)
        await update.message.reply_text(f"news will post every {hours}h (when on).")
    else:
        usage = [
            "/news status",
            "/news on",
            "/news off",
            "/news now",
            f"/news categories <{'/'.join(CATEGORIES.keys())}|all>",
            "/news interval <hours>",
        ]
        await update.message.reply_text("Usage:\n" + "\n".join(usage))


# ---------- background loop ----------
async def _run_check(bot) -> None:
    settings = _load(SETTINGS_FILE)
    seen = _load(SEEN_FILE)
    now = datetime.now(timezone.utc)
    settings_changed = False
    seen_changed = False

    for chat_key, conf in list(settings.items()):
        if not conf.get("enabled"):
            continue

        last_run_str = conf.get("last_run")
        interval_hours = conf.get("interval_hours", 3)
        if last_run_str:
            last_run = datetime.fromisoformat(last_run_str)
            if (now - last_run).total_seconds() < interval_hours * 3600:
                continue

        text, additions = await _build_post(chat_key, conf, seen)

        if text:
            try:
                await bot.send_message(int(chat_key), text, disable_web_page_preview=True)
                # only mark stories as seen once they actually went out
                _commit_seen(seen, chat_key, additions)
                seen_changed = True
            except TelegramError as e:
                logger.warning(f"Could not post news to {chat_key}: {e}")

        conf["last_run"] = now.isoformat()
        settings_changed = True

    if settings_changed:
        _save(SETTINGS_FILE, settings)
    if seen_changed:
        _save(SEEN_FILE, seen)


async def start_background_loop(bot) -> None:
    """Call once from bot.py's post_init - runs forever, checking every
    CHECK_INTERVAL_SECONDS whether any group's news interval has elapsed."""
    while True:
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
        try:
            await _run_check(bot)
        except Exception:
            logger.exception("Error in news background loop")


def register(app) -> None:
    app.add_handler(CommandHandler("news", news_cmd))
