"""
/help and /menu - lists everything the bot can do, styled to match her aesthetic.
"""

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

import access
from branding import header

HELP_TEXT = (
    f"{header('menu')}\n\n"
    "✒️ /poem `[theme]`\n"
    "   → a poem, written the way i would\n\n"
    "📖 /story `[theme]`\n"
    "   → a short story, quiet and internal\n\n"
    "🕊️ /reset\n"
    "   → clear the page, start fresh\n\n"
    "🪶 /whoami\n"
    "   → see your telegram id\n\n"
    "🛠️ /developer\n"
    "   → who built me\n\n"
    "───────────────\n"
    "or just talk to me like a person.\n"
    "no command needed for that."
)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not access.has_access(update.effective_user.id):
        await update.message.reply_text("You don't have access to this bot.")
        return
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


def register(app) -> None:
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("menu", help_cmd))
