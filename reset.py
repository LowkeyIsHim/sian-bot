"""
/reset - clears the rolling conversation history for this chat.
"""

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

import access
from ai import reset_history


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not access.has_access(update.effective_user.id):
        await update.message.reply_text("You don't have access to this bot.")
        return
    reset_history(update.effective_chat.id)
    await update.message.reply_text("Conversation reset. Clean page.")


def register(app) -> None:
    app.add_handler(CommandHandler("reset", reset))
