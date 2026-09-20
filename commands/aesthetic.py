"""
/aesthetic - a short standalone quote + a matching moody photo. Not a
full poem - just a quick aesthetic drop, works in DM or group.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

import access
from ai import get_short_quote, get_image_search_phrase, RateLimitError
from photos import get_matching_photo
from rate_limit import user_is_rate_limited, global_is_rate_limited

logger = logging.getLogger(__name__)


async def aesthetic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not access.has_access(user_id):
        return  # silent

    if user_is_rate_limited(user_id, "ai", limit=5, window_seconds=30):
        await update.message.reply_text("slow down a little — give me a few seconds.")
        return
    if global_is_rate_limited("ai", limit=15, window_seconds=30):
        await update.message.reply_text("i'm a bit overwhelmed right now — try again in a moment.")
        return

    try:
        quote = get_short_quote()
    except RateLimitError:
        await update.message.reply_text("i need a moment to catch my breath — try again shortly.")
        return
    except Exception:
        logger.exception("Error generating aesthetic quote")
        await update.message.reply_text("Something went wrong on my end. Try again in a moment.")
        return

    try:
        query = get_image_search_phrase(quote)
        photo_file = get_matching_photo(query)
        if photo_file:
            await update.message.reply_photo(photo=photo_file, caption=quote)
            return
    except Exception:
        pass

    await update.message.reply_text(quote)


def register(app) -> None:
    app.add_handler(CommandHandler("aesthetic", aesthetic))
