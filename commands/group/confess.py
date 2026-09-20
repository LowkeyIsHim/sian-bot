"""
/setconfessions - creator-only, run INSIDE a group to enable anonymous
confessions for that group.

/confess <text> - DM-only (for anonymity). Checks the sender is actually
a member of an enabled group, then posts their message anonymously there.

/confessionlog - creator-only, DM-only. Every confession is logged with
its real sender, visible only here - a safety net so abuse (threats,
doxxing, etc.) can be traced even though the group itself never sees who
sent what.
"""

import json
import os
from datetime import datetime, timezone

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, CommandHandler

import access
from branding import header

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PERSISTENT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))
CONFESSIONS_FILE = os.path.join(_PERSISTENT_DIR, "confessions.json")
LOG_FILE = os.path.join(_PERSISTENT_DIR, "confession_log.json")

LOG_PAGE_SIZE = 20


def _load_settings() -> dict:
    if not os.path.exists(CONFESSIONS_FILE):
        return {"enabled_groups": []}
    with open(CONFESSIONS_FILE, "r") as f:
        data = json.load(f)
    data.setdefault("enabled_groups", [])
    return data


def _save_settings(data: dict) -> None:
    with open(CONFESSIONS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _load_log() -> dict:
    if not os.path.exists(LOG_FILE):
        return {"next_id": 1, "entries": []}
    with open(LOG_FILE, "r") as f:
        data = json.load(f)
    data.setdefault("next_id", 1)
    data.setdefault("entries", [])
    return data


def _save_log(data: dict) -> None:
    with open(LOG_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _record_confession(chat_id: int, user, text: str) -> int:
    log = _load_log()
    entry_id = log["next_id"]
    log["next_id"] += 1
    log["entries"].append({
        "id": entry_id,
        "chat_id": chat_id,
        "user_id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "text": text,
        "time": datetime.now(timezone.utc).isoformat(),
    })
    _save_log(log)
    return entry_id


async def setconfessions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Run this inside the group you want to enable it for.")
        return
    if not access.is_creator(update.effective_user.id):
        return  # silent

    chat_id = update.effective_chat.id
    data = _load_settings()
    if chat_id in data["enabled_groups"]:
        data["enabled_groups"].remove(chat_id)
        _save_settings(data)
        await update.message.reply_text("Confessions disabled for this group.")
    else:
        data["enabled_groups"].append(chat_id)
        _save_settings(data)
        await update.message.reply_text("Confessions enabled for this group. Members can now DM me /confess.")


async def confess(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        await update.message.reply_text("DM me this one - it's anonymous.")
        return

    text = " ".join(context.args) if context.args else None
    if not text:
        await update.message.reply_text("Usage: /confess <your confession>")
        return
    if len(text) > 1000:
        text = text[:1000]

    user = update.effective_user
    data = _load_settings()

    for chat_id in data["enabled_groups"]:
        try:
            member = await context.bot.get_chat_member(chat_id, user.id)
            if member.status in ("left", "kicked"):
                continue
        except TelegramError:
            continue

        entry_id = _record_confession(chat_id, user, text)
        confession_text = f"{header('anonymous confession')}\n\n{text}\n\n_#{entry_id}_"
        try:
            await context.bot.send_message(chat_id, confession_text, parse_mode="Markdown")
            await update.message.reply_text("Sent - anonymously, no one will know it was you.")
        except TelegramError:
            await update.message.reply_text("Couldn't post that right now, try again shortly.")
        return

    await update.message.reply_text(
        "Confessions aren't open for you right now - you need to be a member "
        "of a group that has them enabled."
    )


async def confessionlog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        return  # silent - this is sensitive, only ever answer in DM
    if not access.is_creator(update.effective_user.id):
        return  # silent

    log = _load_log()
    entries = log["entries"][-LOG_PAGE_SIZE:]
    if not entries:
        await update.message.reply_text("No confessions logged yet.")
        return

    lines = [header("confession log (private)")]
    for e in reversed(entries):
        who = f"@{e['username']}" if e["username"] else e["first_name"]
        snippet = e["text"][:60] + ("..." if len(e["text"]) > 60 else "")
        lines.append(f"\n#{e['id']} - {who} (`{e['user_id']}`)\n{snippet}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def clearconfessionlog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        return  # silent - this is sensitive, only ever handle in DM
    if not access.is_creator(update.effective_user.id):
        return  # silent

    _save_log({"next_id": 1, "entries": []})
    await update.message.reply_text("Confession log cleared.")


def register(app) -> None:
    app.add_handler(CommandHandler("setconfessions", setconfessions))
    app.add_handler(CommandHandler("confess", confess))
    app.add_handler(CommandHandler("confessionlog", confessionlog))
    app.add_handler(CommandHandler("clearconfessionlog", clearconfessionlog))
