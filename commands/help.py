"""
/help and /menu - lists everything the bot can do, styled to match her aesthetic.
"""

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from branding import header, LINE, DOT_DIVIDER

HELP_TEXT = (
    f"{header('menu')}\n\n"
    "*/poem* `[theme]`\n"
    "_a poem, written the way i would_\n\n"
    f"{DOT_DIVIDER}\n\n"
    "*/story* `[theme]`\n"
    "_a short story, quiet and internal_\n\n"
    f"{DOT_DIVIDER}\n\n"
    "*/reset*\n"
    "_clear the page, start fresh_\n\n"
    f"{DOT_DIVIDER}\n\n"
    "*/whoami*\n"
    "_see your telegram id_\n\n"
    f"{DOT_DIVIDER}\n\n"
    "*/developer*\n"
    "_who built me_\n\n"
    f"{LINE}\n\n"
    "or just talk to me like a person.\n"
    "no command needed for that."
)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


def register(app) -> None:
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("menu", help_cmd))
