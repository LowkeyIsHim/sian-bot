"""
Passively records who has posted in each group the bot is in. Telegram's
Bot API doesn't let bots list a full member list for privacy reasons, so
/tagall can only ping people this tracker has actually seen post at least
once. Also gives each newly-seen member a baseline scoped menu.
"""

import json
import os

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from menus import refresh_group_menu

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))                          # .../bot_src/commands/group
_PERSISTENT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))  # one above bot_src
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

    chat_id = update.effective_chat.id
    record_member(chat_id, user.id, user.username, user.first_name)

    # Always re-sync, not just for new members: Telegram caches whatever
    # menu was last set for a scope, even across restarts, so anyone
    # tracked before this feature existed would otherwise be stuck with
    # a stale menu forever. This keeps everyone's menu accurate as their
    # permissions change (promote/demote, gadmin, etc).
    await refresh_group_menu(context.bot, chat_id, user.id)


def register(app) -> None:
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, _track), group=1)
