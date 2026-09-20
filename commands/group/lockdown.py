"""
/lockdown - emergency toggle: restricts the whole group to admins-only
messaging, or restores normal permissions. Group admin only.
"""

import json
import os

from telegram import ChatPermissions, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, CommandHandler

import access

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PERSISTENT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))
LOCKDOWN_FILE = os.path.join(_PERSISTENT_DIR, "lockdown.json")

NORMAL_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_other_messages=True,
    can_send_polls=True,
    can_add_web_page_previews=True,
    can_invite_users=True,
)
LOCKED_PERMISSIONS = ChatPermissions(can_send_messages=False)


def _load() -> dict:
    if not os.path.exists(LOCKDOWN_FILE):
        return {}
    with open(LOCKDOWN_FILE, "r") as f:
        return json.load(f)


def _save(data: dict) -> None:
    with open(LOCKDOWN_FILE, "w") as f:
        json.dump(data, f, indent=2)


async def lockdown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This only works inside a group.")
        return
    if not (access.is_creator(update.effective_user.id) or access.is_group_admin(update.effective_user.id)):
        return  # silent

    chat_id = update.effective_chat.id
    data = _load()
    is_locked = data.get(str(chat_id), False)

    try:
        if is_locked:
            await context.bot.set_chat_permissions(chat_id, NORMAL_PERMISSIONS)
            data[str(chat_id)] = False
            await update.message.reply_text("🔓 lockdown lifted - normal messaging restored.")
        else:
            await context.bot.set_chat_permissions(chat_id, LOCKED_PERMISSIONS)
            data[str(chat_id)] = True
            await update.message.reply_text("🔒 lockdown active - only admins can send messages.")
        _save(data)
    except TelegramError as e:
        await update.message.reply_text(f"Couldn't change permissions: {e}")


def register(app) -> None:
    app.add_handler(CommandHandler("lockdown", lockdown))
