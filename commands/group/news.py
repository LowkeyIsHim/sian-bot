"""
/news - configurable auto-posting news feed for a group. Pick categories
(or "all"), set the posting interval in hours, toggle on/off. Runs on
RSS feeds - the legitimate way to pull headlines (scraping arbitrary
sites would violate their terms and breaks constantly).

A background loop (started from bot.py's post_init) checks every 5
minutes whether any enabled group's interval has elapsed, and if so,
fetches new headlines from that group's chosen categories and posts them.
"""

import asyncio
import json
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import CommandHandler, ContextTypes

import access
from branding import header, DOT_DIVIDER

logger = logging.getLogger(__name__)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PERSISTENT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))
SETTINGS_FILE = os.path.join(_PERSISTENT_DIR, "news_settings.json")
SEEN_FILE = os.path.join(_PERSISTENT_DIR, "news_seen.json")

CHECK_INTERVAL_SECONDS = 300  # background loop wakes up every 5 min
MAX_ITEMS_PER_POST = 3        # cap headlines per category per check
SEEN_CAP_PER_CATEGORY = 50

CATEGORIES = {
    "world": {"label": "World News", "icon": "🌍", "feed": "http://feeds.bbci.co.uk/news/world/rss.xml"},
    "political": {"label": "Politics", "icon": "🏛️", "feed": "http://feeds.bbci.co.uk/news/politics/rss.xml"},
    "crypto": {"label": "Crypto & Markets", "icon": "📈", "feed": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    "tech": {"label": "Tech", "icon": "💻", "feed": "https://techcrunch.com/feed/"},
    "sports": {"label": "Sports", "icon": "⚽", "feed": "http://feeds.bbci.co.uk/sport/rss.xml?edition=uk"},
    "entertainment": {"label": "Entertainment", "icon": "🎬", "feed": "http://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml"},
}


def _load(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)


def _save(path: str, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _md_safe(text: str) -> str:
    for ch in ("[", "]", "*", "_", "`"):
        text = text.replace(ch, "")
    return text


def _fetch_feed_items(feed_url: str, limit: int = 10) -> list[dict]:
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
            f"{header('news feed')}\n\n"
            f"status: {'🟢 on' if conf['enabled'] else '🔴 off'}\n"
            f"categories: {cats}\n"
            f"interval: every {conf['interval_hours']}h\n\n"
            f"available: {', '.join(CATEGORIES.keys())}, all"
        )
        await update.message.reply_text(text, parse_mode="Markdown")
        return

    sub = args[0].lower()

    if sub == "on":
        conf["enabled"] = True
        _save(SETTINGS_FILE, settings)
        await update.message.reply_text("🟢 news feed enabled.")
    elif sub == "off":
        conf["enabled"] = False
        _save(SETTINGS_FILE, settings)
        await update.message.reply_text("🔴 news feed disabled.")
    elif sub == "categories" and len(args) > 1:
        raw = " ".join(args[1:]).lower()
        if raw.strip() == "all":
            conf["categories"] = ["all"]
        else:
            requested = [c.strip() for c in raw.replace(",", " ").split()]
            valid = [c for c in requested if c in CATEGORIES]
            if not valid:
                await update.message.reply_text(f"No valid categories. Choose from: {', '.join(CATEGORIES.keys())}, all")
                return
            conf["categories"] = valid
        _save(SETTINGS_FILE, settings)
        await update.message.reply_text(f"categories set to: {', '.join(conf['categories'])}")
    elif sub == "interval" and len(args) > 1 and args[1].isdigit():
        hours = max(1, int(args[1]))
        conf["interval_hours"] = hours
        _save(SETTINGS_FILE, settings)
        await update.message.reply_text(f"⚙️ news will post every {hours}h (when enabled).")
    else:
        usage = [
            "/news status",
            "/news on",
            "/news off",
            f"/news categories <{'/'.join(CATEGORIES.keys())}|all>",
            "/news interval <hours>",
        ]
        await update.message.reply_text("Usage:\n" + "\n".join(usage))


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

        chat_id = int(chat_key)
        categories = conf.get("categories", [])
        cats_to_check = list(CATEGORIES.keys()) if "all" in categories else categories
        seen.setdefault(chat_key, {})

        sections = []
        for cat in cats_to_check:
            if cat not in CATEGORIES:
                continue
            info = CATEGORIES[cat]
            items = _fetch_feed_items(info["feed"])
            if not items:
                continue

            seen_links = set(seen[chat_key].get(cat, []))
            new_items = [it for it in items if it["link"] not in seen_links][:MAX_ITEMS_PER_POST]
            if not new_items:
                continue

            lines = [f"{info['icon']} *{info['label']}*"]
            for it in new_items:
                lines.append(f"• [{_md_safe(it['title'])}]({it['link']})")
            sections.append("\n".join(lines))

            seen[chat_key].setdefault(cat, [])
            seen[chat_key][cat] = (seen[chat_key][cat] + [it["link"] for it in new_items])[-SEEN_CAP_PER_CATEGORY:]
            seen_changed = True

        if sections:
            body = f"\n\n{DOT_DIVIDER}\n\n".join(sections)
            text = f"{header('news')}\n\n{body}"
            try:
                await bot.send_message(
                    chat_id, text, parse_mode="Markdown", disable_web_page_preview=True
                )
            except TelegramError as e:
                logger.warning(f"Could not post news to {chat_id}: {e}")

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
