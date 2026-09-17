"""
/story [theme] - explicitly requests a short story, optionally about a given theme.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

import access
from ai import ask_sian, split_title, RateLimitError

logger = logging.getLogger(__name__)


async def story(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not access.has_access(user_id):
        await update.message.reply_text("You don't have access to this bot.")
        return

    theme = " ".join(context.args) if context.args else None
    prompt = f"Write me a short story about {theme}." if theme else "Write me a short story."

    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        reply = ask_sian(chat_id, prompt)
    except RateLimitError:
        await update.message.reply_text("i need a moment to catch my breath — try again shortly.")
        return
    except Exception:
        logger.exception("Error generating story")
        await update.message.reply_text("Something went wrong on my end. Try again in a moment.")
        return

    title, body = split_title(reply)
    if title:
        await update.message.reply_text(f"📖 *{title}*", parse_mode="Markdown")
        await update.message.reply_text(body)
    else:
        await update.message.reply_text(reply)


def register(app) -> None:
    app.add_handler(CommandHandler("story", story))
