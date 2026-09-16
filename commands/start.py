"""
/start - the first thing someone sees. Sets the tone for the whole bot.
"""

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

import access

WELCOME_MESSAGE = (
    "🕊️ *Hi. I'm Sian.*\n\n"
    "I turn what I carry into words — poems, stories, or just a conversation, "
    "if that's what you need today.\n\n"
    "Tap the ☰ menu next to this chat to see what I can do, "
    "or just start typing. No need to perform anything here.\n\n"
    "✒️ _brought into being by_ [Lowkey](https://t.me/Im_just_l0wkey)"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not access.has_access(user_id):
        await update.message.reply_text("You don't have access to this bot.")
        return
    await update.message.reply_text(
        WELCOME_MESSAGE, parse_mode="Markdown", disable_web_page_preview=True
    )


def register(app) -> None:
    app.add_handler(CommandHandler("start", start))
