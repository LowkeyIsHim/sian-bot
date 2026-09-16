"""
/help - lists everything the bot can do.
"""

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

import access

HELP_TEXT = (
    "🎀 *Here's what I can do*\n\n"
    "✒️ /poem \\[theme] — write a poem, about anything you name (or nothing at all)\n"
    "📖 /story \\[theme] — write a short story\n"
    "🕊️ /reset — clear our current conversation, start fresh\n"
    "🪪 /whoami — see your Telegram ID\n"
    "🛠️ /developer — who built me\n\n"
    "Or just talk to me like a person. No command needed for that."
)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not access.has_access(update.effective_user.id):
        await update.message.reply_text("You don't have access to this bot.")
        return
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


def register(app) -> None:
    app.add_handler(CommandHandler("help", help_cmd))
