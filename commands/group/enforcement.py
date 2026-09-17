"""
Shared enforcement logic used by flood_guard, link_guard, and word_guard.
Given a violation, always deletes the offending message, then applies
whatever escalation the group's settings call for.
"""

import json
import os
from datetime import datetime, timedelta, timezone

from telegram import ChatPermissions
from telegram.error import TelegramError
from telegram.ext import ContextTypes

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PERSISTENT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))
WARNINGS_FILE = os.path.join(_PERSISTENT_DIR, "group_warnings.json")


def _load() -> dict:
    if not os.path.exists(WARNINGS_FILE):
        return {}
    with open(WARNINGS_FILE, "r") as f:
        return json.load(f)


def _save(data: dict) -> None:
    with open(WARNINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_warnings(chat_id: int, user_id: int) -> int:
    data = _load()
    return data.get(str(chat_id), {}).get(str(user_id), 0)


def add_warning(chat_id: int, user_id: int) -> int:
    data = _load()
    chat_key, user_key = str(chat_id), str(user_id)
    data.setdefault(chat_key, {})
    data[chat_key][user_key] = data[chat_key].get(user_key, 0) + 1
    _save(data)
    return data[chat_key][user_key]


def clear_warnings(chat_id: int, user_id: int) -> None:
    data = _load()
    chat_key, user_key = str(chat_id), str(user_id)
    if chat_key in data and user_key in data[chat_key]:
        del data[chat_key][user_key]
        _save(data)


async def is_exempt(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    """Group admins (our own tier) and creators are never filtered. Real
    Telegram chat admins/owner are also exempt, so the bot doesn't
    accidentally moderate the people actually running the group."""
    import access

    if access.is_group_admin(user_id):
        return True
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except TelegramError:
        return False


async def enforce(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
    message_id: int,
    kind: str,
    rule: dict,
    reason: str,
) -> None:
    """Deletes the message and applies the configured escalation."""
    try:
        await context.bot.delete_message(chat_id, message_id)
    except TelegramError:
        pass  # message may already be gone, or bot lacks delete rights

    action = rule.get("action", "delete")

    if action == "delete":
        return

    if action == "ban":
        try:
            await context.bot.ban_chat_member(chat_id, user_id)
            await context.bot.send_message(chat_id, f"Removed a member for: {reason}.")
        except TelegramError:
            pass
        return

    if action == "mute":
        minutes = rule.get("mute_minutes", 10)
        until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        try:
            await context.bot.restrict_chat_member(
                chat_id, user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            await context.bot.send_message(
                chat_id, f"Muted a member for {minutes} minute(s) - {reason}."
            )
        except TelegramError:
            pass
        return

    if action == "warn":
        count = add_warning(chat_id, user_id)
        limit = rule.get("warn_limit", 3)
        if count >= limit:
            try:
                await context.bot.ban_chat_member(chat_id, user_id)
                await context.bot.send_message(
                    chat_id, f"Member reached {count}/{limit} warnings ({reason}) and was removed."
                )
            except TelegramError:
                pass
            clear_warnings(chat_id, user_id)
        else:
            try:
                await context.bot.send_message(
                    chat_id, f"Warning {count}/{limit} - {reason}."
                )
            except TelegramError:
                pass
