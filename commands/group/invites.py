"""commands/group/invites.py

/invite       - get your personal invite link for this group
/topinviters  - who has brought in the most people

Each member gets their own invite link (created by the bot). When someone
joins through it, the owner is credited once per person (rejoining can't
farm points, joining via your own link doesn't count).

Needs: the bot is a group admin with the "Invite Users" right, and
allowed_updates includes chat_member (main.py already uses ALL_TYPES).
Handler group used: 8.
"""

import html
import json
import os

from telegram import ChatMemberUpdated, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))  # .../bot_src/commands/group
_PERSISTENT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))
INVITES_FILE = os.path.join(_PERSISTENT_DIR, "invites.json")


# ---------- storage ----------
def _load() -> dict:
    if not os.path.exists(INVITES_FILE):
        return {}
    try:
        with open(INVITES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}


def _save(data: dict) -> None:
    tmp = INVITES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, INVITES_FILE)  # atomic


def _chat(data: dict, chat_id) -> dict:
    return data.setdefault(
        str(chat_id),
        {"links": {}, "by_url": {}, "credited": {}, "counts": {}},
    )


def get_count(chat_id, user_id) -> int:
    """Used by stats.py - people this user brought in via their link."""
    chat = _load().get(str(chat_id), {})
    return chat.get("counts", {}).get(str(user_id), {}).get("count", 0)


# ---------- helpers ----------
def _is_in(member) -> bool:
    return member.status in ("member", "administrator", "creator") or (
        member.status == "restricted" and getattr(member, "is_member", False)
    )


# ---------- commands ----------
async def invite_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    m = update.effective_message
    user = update.effective_user
    chat_id = m.chat_id

    data = _load()
    chat = _chat(data, chat_id)
    existing = chat["links"].get(str(user.id))

    if existing:
        url = existing["url"]
    else:
        try:
            link = await context.bot.create_chat_invite_link(
                chat_id=chat_id, name=f"{user.full_name}"[:32]
            )
        except TelegramError:
            await m.reply_text(
                "I couldn't make a link. I need to be an admin here with the "
                "Invite Users permission 😔"
            )
            return
        url = link.invite_link
        chat["links"][str(user.id)] = {"url": url, "name": user.full_name}
        chat["by_url"][url] = str(user.id)
        _save(data)

    count = chat["counts"].get(str(user.id), {}).get("count", 0)
    await m.reply_text(
        f"🔗 <b>{html.escape(user.full_name)}'s invite link</b>\n"
        f"{html.escape(url)}\n\n"
        f"Everyone who joins through it counts for you. "
        f"You've brought in <b>{count}</b> so far.",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def topinviters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    m = update.effective_message
    counts = _load().get(str(m.chat_id), {}).get("counts", {})
    ranked = sorted(
        (e for e in counts.values() if e["count"] > 0),
        key=lambda e: e["count"],
        reverse=True,
    )[:10]
    if not ranked:
        await m.reply_text("Nobody has brought anyone in yet. Use /invite to get your link 🔗")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = ["🔗 <b>Top inviters</b>", ""]
    for i, e in enumerate(ranked):
        tag = medals[i] if i < 3 else f"<b>{i + 1}.</b>"
        word = "person" if e["count"] == 1 else "people"
        lines.append(f"{tag} <b>{html.escape(e['name'])}</b> · {e['count']} {word}")
    await m.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ---------- join watcher ----------
async def on_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cmu: ChatMemberUpdated = update.chat_member
    if not cmu or not cmu.invite_link:
        return
    member = cmu.new_chat_member.user
    if member.is_bot:
        return
    if _is_in(cmu.old_chat_member) or not _is_in(cmu.new_chat_member):
        return  # not a fresh join

    data = _load()
    chat = _chat(data, cmu.chat.id)
    owner_id = chat["by_url"].get(cmu.invite_link.invite_link)
    if not owner_id or owner_id == str(member.id):
        return  # not one of our links, or joined via their own
    if str(member.id) in chat["credited"]:
        return  # already credited once, no farming by leave/rejoin

    chat["credited"][str(member.id)] = owner_id
    owner = chat["counts"].setdefault(
        owner_id, {"name": chat["links"][owner_id]["name"], "count": 0}
    )
    owner["count"] += 1
    _save(data)


def register(app) -> None:
    app.add_handler(CommandHandler("invite", invite_cmd, filters=filters.ChatType.GROUPS))
    app.add_handler(
        CommandHandler("topinviters", topinviters_cmd, filters=filters.ChatType.GROUPS)
    )
    app.add_handler(
        ChatMemberHandler(on_member_update, ChatMemberHandler.CHAT_MEMBER), group=8
    )
