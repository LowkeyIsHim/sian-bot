"""
/setconfessions - creator-only, run INSIDE a group to enable anonymous
confessions for that group.

/confess <text> - DM-only (for anonymity). Checks the sender is actually
a member of an enabled group, then posts their message anonymously there.
"""

import json
import os

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, CommandHandler

import access
from branding import header

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PERSISTENT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))
CONFESSIONS_FILE = os.path.join(_PERSISTENT_DIR, "confessions.json")


def _load() -> dict:
    if not os.path.exists(CONFESSIONS_FILE):
        return {"enabled_groups": []}
    with open(CONFESSIONS_FILE, "r") as f:
        data = json.load(f)
    data.setdefault("enabled_groups", [])
    return data


def _save(data: dict) -> None:
    with open(CONFESSIONS_FILE, "w") as f:
        json.dump(data, f, indent=2)


async def setconfessions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Run this inside the group you want to enable it for.")
        return
    if not access.is_creator(update.effective_user.id):
        return  # silent

    chat_id = update.effective_chat.id
    data = _load()
    if chat_id in data["enabled_groups"]:
        data["enabled_groups"].remove(chat_id)
        _save(data)
        await update.message.reply_text("Confessions disabled for this group.")
    else:
        data["enabled_groups"].append(chat_id)
        _save(data)
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

    user_id = update.effective_user.id
    data = _load()

    for chat_id in data["enabled_groups"]:
        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            if member.status in ("left", "kicked"):
                continue
        except TelegramError:
            continue

        confession_text = f"{header('anonymous confession')}\n\n{text}"
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


def register(app) -> None:
    app.add_handler(CommandHandler("setconfessions", setconfessions))
    app.add_handler(CommandHandler("confess", confess))
