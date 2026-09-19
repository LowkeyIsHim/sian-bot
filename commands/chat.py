"""
Freeform conversation - anything the user sends that isn't a command.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

import access
from ai import ask_sian, split_title, RateLimitError
from rate_limit import user_is_rate_limited, global_is_rate_limited

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 2000


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not access.has_access(user_id):
        return  # silent

    if user_is_rate_limited(user_id, "ai", limit=8, window_seconds=30):
        await update.message.reply_text("slow down a little — give me a few seconds.")
        return
    if global_is_rate_limited("ai", limit=15, window_seconds=30):
        await update.message.reply_text("i'm a bit overwhelmed right now — try again in a moment.")
        return

    chat_id = update.effective_chat.id
    user_text = update.message.text
    if len(user_text) > MAX_MESSAGE_LENGTH:
        await update.message.reply_text("that's a lot — try something shorter?")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        reply = ask_sian(chat_id, user_text)
    except RateLimitError:
        await update.message.reply_text("i need a moment to catch my breath — try again shortly.")
        return
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
    app.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, handle_message)
    )
