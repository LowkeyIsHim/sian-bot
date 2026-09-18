"""
Per-group configuration for the moderation rules (antiflood, antilink,
antiword). Group-admin-only to view/change.

Each rule has the same shape:
  enabled: bool
  action: "delete" | "warn" | "mute" | "ban"
    - delete: remove the message, nothing else
    - warn:   remove the message, add a warning; at warn_limit, ban
    - mute:   remove the message, mute the sender for mute_minutes
    - ban:    remove the message, ban the sender immediately
  warn_limit: int   (used by "warn")
  mute_minutes: int (used by "mute")

antiword additionally stores its own "words" list.
"""

import asyncio
import json
import os

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, CommandHandler

import access

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))              # .../bot_src/commands/group
_PERSISTENT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))  # above bot_src
SETTINGS_FILE = os.path.join(_PERSISTENT_DIR, "group_settings.json")

VALID_ACTIONS = {"delete", "warn", "mute", "ban"}
DEFAULT_RULE = {"enabled": False, "action": "delete", "warn_limit": 3, "mute_minutes": 10}


def _load() -> dict:
    if not os.path.exists(SETTINGS_FILE):
        return {}
    with open(SETTINGS_FILE, "r") as f:
        return json.load(f)


def _save(data: dict) -> None:
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_rule(chat_id: int, kind: str) -> dict:
    data = _load()
    chat_rules = data.get(str(chat_id), {})
    rule = dict(DEFAULT_RULE)
    rule.update(chat_rules.get(kind, {}))
    if kind == "antiword":
        rule.setdefault("words", chat_rules.get(kind, {}).get("words", []))
    return rule


def _set_field(chat_id: int, kind: str, field: str, value) -> None:
    data = _load()
    chat_key = str(chat_id)
    data.setdefault(chat_key, {})
    data[chat_key].setdefault(kind, dict(DEFAULT_RULE))
    data[chat_key][kind][field] = value
    _save(data)


def add_word(chat_id: int, word: str) -> bool:
    rule = get_rule(chat_id, "antiword")
    words = rule.get("words", [])
    word = word.lower().strip()
    if word in words:
        return False
    words.append(word)
    _set_field(chat_id, "antiword", "words", words)
    return True


def remove_word(chat_id: int, word: str) -> bool:
    rule = get_rule(chat_id, "antiword")
    words = rule.get("words", [])
    word = word.lower().strip()
    if word not in words:
        return False
    words.remove(word)
    _set_field(chat_id, "antiword", "words", words)
    return True


def _format_rule(kind: str, rule: dict) -> str:
    lines = [
        f"*{kind}*",
        f"enabled: {rule['enabled']}",
        f"action: {rule['action']}",
    ]
    if rule["action"] == "warn":
        lines.append(f"warn limit: {rule['warn_limit']}")
    if rule["action"] == "mute":
        lines.append(f"mute minutes: {rule['mute_minutes']}")
    if kind == "antiword":
        words = rule.get("words", [])
        lines.append(f"words: {', '.join(words) if words else '(none set)'}")
    return "\n".join(lines)


AUTO_DELETE_SECONDS = 5


async def _delete_later(bot, chat_id: int, message_ids: list[int]) -> None:
    await asyncio.sleep(AUTO_DELETE_SECONDS)
    for mid in message_ids:
        try:
            await bot.delete_message(chat_id, mid)
        except TelegramError:
            pass


def _make_settings_command(kind: str):
    """Builds a /antiflood, /antilink, or /antiword command handler -
    they all share the same on/off/action/limit mechanics."""

    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat.type not in ("group", "supergroup"):
            await update.message.reply_text("This only works inside a group.")
            return
        if not access.is_group_admin(update.effective_user.id):
            return  # silent - don't confirm to randoms that this exists

        chat_id = update.effective_chat.id
        args = context.args

        async def reply(text: str, **kwargs) -> None:
            sent = await update.message.reply_text(text, **kwargs)
            asyncio.create_task(
                _delete_later(context.bot, chat_id, [update.message.message_id, sent.message_id])
            )

        if not args or args[0] == "status":
            rule = get_rule(chat_id, kind)
            await reply(_format_rule(kind, rule), parse_mode="Markdown")
            return

        sub = args[0].lower()

        if sub == "on":
            _set_field(chat_id, kind, "enabled", True)
            await reply(f"{kind} enabled.")
        elif sub == "off":
            _set_field(chat_id, kind, "enabled", False)
            await reply(f"{kind} disabled.")
        elif sub == "action" and len(args) > 1 and args[1].lower() in VALID_ACTIONS:
            _set_field(chat_id, kind, "action", args[1].lower())
            await reply(f"{kind} action set to {args[1].lower()}.")
        elif sub == "warnlimit" and len(args) > 1 and args[1].isdigit():
            _set_field(chat_id, kind, "warn_limit", int(args[1]))
            await reply(f"{kind} warn limit set to {args[1]}.")
        elif sub == "muteminutes" and len(args) > 1 and args[1].isdigit():
            _set_field(chat_id, kind, "mute_minutes", int(args[1]))
            await reply(f"{kind} mute duration set to {args[1]} minutes.")
        elif kind == "antiword" and sub == "addword" and len(args) > 1:
            word = " ".join(args[1:])
            if add_word(chat_id, word):
                await reply(f"Added \"{word}\" to the banned word list.")
            else:
                await reply(f"\"{word}\" is already on the list.")
        elif kind == "antiword" and sub == "removeword" and len(args) > 1:
            word = " ".join(args[1:])
            if remove_word(chat_id, word):
                await reply(f"Removed \"{word}\" from the banned word list.")
            else:
                await reply(f"\"{word}\" wasn't on the list.")
        else:
            usage = [
                f"/{kind} status",
                f"/{kind} on",
                f"/{kind} off",
                f"/{kind} action <delete|warn|mute|ban>",
                f"/{kind} warnlimit <n>",
                f"/{kind} muteminutes <n>",
            ]
            if kind == "antiword":
                usage += [f"/{kind} addword <word>", f"/{kind} removeword <word>"]
            await reply("Usage:\n" + "\n".join(usage))

    return handler


def register(app) -> None:
    app.add_handler(CommandHandler("antiflood", _make_settings_command("antiflood")))
    app.add_handler(CommandHandler("antilink", _make_settings_command("antilink")))
    app.add_handler(CommandHandler("antiword", _make_settings_command("antiword")))
