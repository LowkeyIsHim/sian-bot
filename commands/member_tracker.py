"""
Passively records who has posted in each group the bot is in. Telegram's
Bot API doesn't let bots list a full member list for privacy reasons, so
/tagall can only ping people this tracker has actually seen post at least
once. Records nothing, replies nothing - runs silently in the background.
"""

import json
import os

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))      # .../bot_src/commands
_BOT_SRC_DIR = os.path.dirname(_THIS_DIR)                    # .../bot_src
_PERSISTENT_DIR = os.path.dirname(_BOT_SRC_DIR)              # one above bot_src, survives updates
MEMBERS_FILE = os.path.join(_PERSISTENT_DIR, "group_members.json")


def _load() -> dict:
    if not os.path.exists(MEMBERS_FILE):
        return {}
    with open(MEMBERS_FILE, "r") as f:
        return json.load(f)


def _save(data: dict) -> None:
    with open(MEMBERS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_members(chat_id: int) -> dict:
    data = _load()
    return data.get(str(chat_id), {})


def record_member(chat_id: int, user_id: int, username: str | None, first_name: str | None) -> None:
    data = _load()
    chat_key = str(chat_id)
    data.setdefault(chat_key, {})
    data[chat_key][str(user_id)] = {"username": username, "first_name": first_name}
    _save(data)


async def _track(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_chat.type not in ("group", "supergroup"):
        return
    user = update.effective_user
    if user is None or user.is_bot:
        return
    record_member(update.effective_chat.id, user.id, user.username, user.first_name)


def register(app) -> None:
    # Registered in a separate handler group (PTB's internal concept, not
    # a Telegram group) so it runs alongside other handlers rather than
    # stealing the update from them.
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, _track), group=1)
