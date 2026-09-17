"""
/gmenu - overview of everything the bot can do inside a group.
Visible to everyone (informational only) - actually using most of these
still requires group admin access.
"""

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from branding import header, DOT_DIVIDER

GROUP_MENU_TEXT = (
    f"{header('group tools')}\n\n"
    "*/tagall* `[message]`\n"
    "_mention everyone who's posted here_\n\n"
    f"{DOT_DIVIDER}\n\n"
    "*/mute* `/unmute` `/ban` `/unban`\n"
    "_reply to someone's message to act on them_\n\n"
    "*/warn* `/clearwarns` `/warnings`\n"
    "_track and manage warnings_\n\n"
    f"{DOT_DIVIDER}\n\n"
    "*/antiflood* `/antilink* `/antiword`\n"
    "_configure automatic protection - send one_\n"
    "_with no arguments to see its current setup_\n\n"
    f"{DOT_DIVIDER}\n\n"
    "_group admin access needed for all of the above -_\n"
    "_ask a creator if you need it._"
)


async def gmenu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This only works inside a group.")
        return
    await update.message.reply_text(GROUP_MENU_TEXT, parse_mode="Markdown")


def register(app) -> None:
    app.add_handler(CommandHandler("gmenu", gmenu))
