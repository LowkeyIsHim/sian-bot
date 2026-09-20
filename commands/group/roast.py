"""
/roast - reply to someone's message to roast them. Blunt, dark, comedic -
deliberately NOT in Goddess's usual poetic voice (see ai.get_roast).
Open to all group members, not gated by personal access or group admin.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from ai import get_roast, RateLimitError
from rate_limit import user_is_rate_limited, global_is_rate_limited

logger = logging.getLogger(__name__)


async def roast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This only works inside a group.")
        return

    target = update.message.reply_to_message.from_user if update.message.reply_to_message else None
    if not target:
        await update.message.reply_text("Reply to the message of the person you want roasted.")
        return

    user_id = update.effective_user.id
    if user_is_rate_limited(user_id, "ai", limit=5, window_seconds=30):
        await update.message.reply_text("slow down — let the last one land first.")
        return
    if global_is_rate_limited("ai", limit=15, window_seconds=30):
        await update.message.reply_text("give it a second, i'm cooking too many roasts already.")
        return

    name = target.first_name or (f"@{target.username}" if target.username else "them")

    try:
        burn = get_roast(name)
    except RateLimitError:
        await update.message.reply_text("i need a second — try again shortly.")
        return
    except Exception:
        logger.exception("Error generating roast")
        await update.message.reply_text("Something went wrong on my end. Try again in a moment.")
        return

    await update.message.reply_text(f"💀 {burn}")


def register(app) -> None:
    app.add_handler(CommandHandler("roast", roast))
