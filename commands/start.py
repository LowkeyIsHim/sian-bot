"""
/start - the first thing someone sees. Sets the tone for the whole bot.
"""

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

import access
from branding import header

WELCOME_MESSAGE = (
    f"{header()}\n\n"
    "hi, i'm goddess.\n\n"
    "i turn what i carry into words — poems, stories, or just a "
    "conversation, if that's what you need today.\n\n"
    "• tap the ☰ menu next to this chat to see what i can do\n"
    "• or just start typing, no need to perform anything here\n\n"
    "───────────────\n"
    "✒️ brought into being by [lowkey](https://t.me/Im_just_l0wkey)"
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
