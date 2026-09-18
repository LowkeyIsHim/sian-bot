"""
Passive link detection: flags URLs, t.me links, and @mentions-as-links
in message text or entities, when antilink is enabled for the group.
"""

import re
import logging

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from commands.group.enforcement import enforce, is_exempt
from commands.group.settings import get_rule

logger = logging.getLogger(__name__)

URL_PATTERN = re.compile(
    r"(https?://\S+|www\.\S+|t\.me/\S+|\S+\.(com|net|org|io|xyz|gg|ru|info)\b)",
    re.IGNORECASE,
)


def _contains_link(message) -> bool:
    if message.entities:
        for entity in message.entities:
            if entity.type in ("url", "text_link"):
                return True
    text = message.text or message.caption or ""
    return bool(URL_PATTERN.search(text))


async def _check_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message
    if chat is None or chat.type not in ("group", "supergroup") or user is None or user.is_bot:
        return
    if message is None:
        return

    rule = get_rule(chat.id, "antilink")
    if not rule["enabled"]:
        logger.info(f"antilink not enabled in {chat.id}, skipping")
        return

    if not _contains_link(message):
        return

    if await is_exempt(context, chat.id, user.id):
        logger.info(f"antilink: {user.id} is exempt in {chat.id}, skipping")
        return

    logger.info(f"antilink: enforcing against {user.id} in {chat.id}")
    await enforce(context, chat.id, user.id, message.message_id, "antilink", rule, "posting a link")


def register(app) -> None:
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, _check_link), group=3)
