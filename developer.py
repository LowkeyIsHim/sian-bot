"""
/developer - credits the person who built this bot.
"""

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

DEVELOPER_TEXT = (
    "🛠️ *Built with love by Lowkey he's him*\n"
    "Telegram: [@Im\\_just\\_l0wkey](https://t.me/Im_just_l0wkey)\n\n"
    "Reach out for bugs, feature ideas, or just to say hi."
)


async def developer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        DEVELOPER_TEXT, parse_mode="Markdown", disable_web_page_preview=True
    )


def register(app) -> None:
    app.add_handler(CommandHandler("developer", developer))
