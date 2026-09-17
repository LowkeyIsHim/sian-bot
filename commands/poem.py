"""
/poem [theme] - explicitly requests a poem, optionally about a given theme.
Sends a matching aesthetic photo alongside it, mirroring how she posts on
her own channel.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

import access
from ai import ask_sian, get_image_search_phrase, split_title, RateLimitError
from photos import get_matching_photo

logger = logging.getLogger(__name__)


async def poem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not access.has_access(user_id):
        await update.message.reply_text("You don't have access to this bot.")
        return

    theme = " ".join(context.args) if context.args else None
    prompt = f"Write me a poem about {theme}." if theme else "Write me a poem."

    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        reply = ask_sian(chat_id, prompt)
    except RateLimitError:
        await update.message.reply_text("i need a moment to catch my breath — try again shortly.")
        return
    except Exception:
        logger.exception("Error generating poem")
        await update.message.reply_text("Something went wrong on my end. Try again in a moment.")
        return

    title, body = split_title(reply)
    if title:
        await update.message.reply_text(f"✒️ *{title}*", parse_mode="Markdown")
        await update.message.reply_text(body)
    else:
        await update.message.reply_text(reply)

    # Best-effort: pair the poem with a matching aesthetic photo. If this
    # fails for any reason (no Pexels key, no results, network hiccup),
    # the poem has already been sent - we just skip the photo silently.
    try:
        query = get_image_search_phrase(body)
        photo_file = get_matching_photo(query)
        if photo_file:
            await update.message.reply_photo(photo=photo_file)
    except Exception:
        pass


def register(app) -> None:
    app.add_handler(CommandHandler("poem", poem))
