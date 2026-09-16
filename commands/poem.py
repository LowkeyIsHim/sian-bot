"""
/poem [theme] - explicitly requests a poem, optionally about a given theme.
"""

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

import access
from ai import ask_sian


async def poem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not access.has_access(user_id):
        await update.message.reply_text("You don't have access to this bot.")
        return

    theme = " ".join(context.args) if context.args else None
    prompt = f"Write me a poem about {theme}." if theme else "Write me a poem."

    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        reply = ask_sian(chat_id, prompt)
    except Exception:
        reply = "Something went wrong on my end. Try again in a moment."

    await update.message.reply_text(reply)


def register(app) -> None:
    app.add_handler(CommandHandler("poem", poem))
