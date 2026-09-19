"""
/story [theme] - explicitly requests a short story, optionally about a given theme.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

import access
from ai import ask_sian, split_title, RateLimitError
from rate_limit import user_is_rate_limited, global_is_rate_limited

logger = logging.getLogger(__name__)

MAX_THEME_LENGTH = 200


async def story(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not access.has_access(user_id):
        return  # silent

    if user_is_rate_limited(user_id, "ai", limit=5, window_seconds=30):
        await update.message.reply_text("slow down a little — give me a few seconds.")
        return
    if global_is_rate_limited("ai", limit=15, window_seconds=30):
        await update.message.reply_text("i'm a bit overwhelmed right now — try again in a moment.")
        return

    theme = " ".join(context.args) if context.args else None
    if theme and len(theme) > MAX_THEME_LENGTH:
        theme = theme[:MAX_THEME_LENGTH]
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
