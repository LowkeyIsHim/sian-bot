"""
/developer - credits the person who built this bot.
"""

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from branding import header

DEVELOPER_TEXT = (
    f"{header('the person behind this')}\n\n"
    "built by *lowkey*\n\n"
    "telegram: [@Im\\_just\\_l0wkey](https://t.me/Im_just_l0wkey)\n\n"
    "_reach out for bugs, feature ideas, or just to say hi._"
)


async def developer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        DEVELOPER_TEXT, parse_mode="Markdown", disable_web_page_preview=True
    )


def register(app) -> None:
    app.add_handler(CommandHandler("developer", developer))
