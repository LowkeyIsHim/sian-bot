"""
Passive banned-word detection - checks message text against the group's
configured word list when antiword is enabled.
"""

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from commands.group.enforcement import enforce, is_exempt
from commands.group.settings import get_rule


async def _check_words(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message
    if chat is None or chat.type not in ("group", "supergroup") or user is None or user.is_bot:
        return
    if message is None or not message.text:
        return

    rule = get_rule(chat.id, "antiword")
    if not rule["enabled"] or not rule.get("words"):
        return

    text_lower = message.text.lower()
    if not any(word in text_lower for word in rule["words"]):
        return

    if await is_exempt(context, chat.id, user.id):
        return

    await enforce(context, chat.id, user.id, message.message_id, "antiword", rule, "using a banned word")


def register(app) -> None:
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, _check_words), group=4)
