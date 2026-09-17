"""
Freeform conversation - anything the user sends that isn't a command.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

import access
from ai import ask_sian, split_title

logger = logging.getLogger(__name__)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not access.has_access(user_id):
        await update.message.reply_text("You don't have access to this bot.")
        return

    chat_id = update.effective_chat.id
    user_text = update.message.text

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        reply = ask_sian(chat_id, user_text)
    except Exception:
        logger.exception("Error calling AI")
        await update.message.reply_text("Something went wrong on my end. Try again in a moment.")
        return

    title, body = split_title(reply)
    if title:
        await update.message.reply_text(f"✒️ *{title}*", parse_mode="Markdown")
        await update.message.reply_text(body)
    else:
        await update.message.reply_text(reply)


def register(app) -> None:
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
