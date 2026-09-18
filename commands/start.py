"""
/start - the first thing someone sees. Sets the tone for the whole bot.

Available to everyone: people without access get a short message pointing
them toward /whoami (to get their ID) and /developer (who to ask), rather
than a dead-end denial.
"""

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

import access
from branding import header, DOT_DIVIDER
from menus import refresh_private_menu

WELCOME_MESSAGE = (
    f"{header()}\n\n"
    "hi, i'm goddess.\n\n"
    "i turn what i carry into words — poems, stories, or just a "
    "conversation, if that's what you need today.\n\n"
    f"{DOT_DIVIDER}\n\n"
    "tap the ☰ menu next to this chat to see what i can do, "
    "or just start typing — no need to perform anything here.\n\n"
    f"{DOT_DIVIDER}\n\n"
    "_brought into being by_ [lowkey](https://t.me/Im_just_l0wkey)"
)

LIMITED_MESSAGE = (
    f"{header()}\n\n"
    "hi, i'm Goddess.\n\n"
    "this is a private space right now — you don't have access yet.\n\n"
    f"{DOT_DIVIDER}\n\n"
    "send /whoami to get your telegram id, then pass it along to "
    "whoever invited you so they can grant you access.\n\n"
    f"{DOT_DIVIDER}\n\n"
    "_curious who built this?_ try /developer."
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await refresh_private_menu(context.bot, user_id)
    message = WELCOME_MESSAGE if access.has_access(user_id) else LIMITED_MESSAGE
    await update.message.reply_text(
        message, parse_mode="Markdown", disable_web_page_preview=True
    )


def register(app) -> None:
    app.add_handler(CommandHandler("start", start))
