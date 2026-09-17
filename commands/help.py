"""
/help and /menu - lists everything the bot can do, styled to match her aesthetic.
"""

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

import access
from branding import framed, BOT_NAME, DIVIDER

HELP_TEXT = (
    f"{framed(BOT_NAME)}\n\n"
    "✒️ /poem `[theme]` — a poem, written the way i would\n"
    "📖 /story `[theme]` — a short story, quiet and internal\n"
    "🕊️ /reset — clear the page, start fresh\n"
    "🪶 /whoami — see your telegram id\n"
    "🛠️ /developer — who built me\n\n"
    f"{DIVIDER}\n\n"
    "or just talk to me like a person. no command needed for that."
)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not access.has_access(update.effective_user.id):
        await update.message.reply_text("You don't have access to this bot.")
        return
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


def register(app) -> None:
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("menu", help_cmd))
