"""
/whoami - shows the user their Telegram ID (no access needed - this is
how you find the IDs to put into access.py's CREATOR_IDS, or the IDs
someone gives you to /access).
"""

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await update.message.reply_text(f"Your Telegram user ID is: {user_id}")


def register(app) -> None:
    app.add_handler(CommandHandler("whoami", whoami))
