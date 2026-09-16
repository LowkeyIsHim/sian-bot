import os
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import access
from ai import ask_sian, reset_history

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ---------- helper ----------

async def _reply_no_access(update: Update) -> None:
    await update.message.reply_text("You don't have access to this bot.")


def _parse_user_id(args: list[str]) -> int | None:
    if not args:
        return None
    try:
        return int(args[0])
    except ValueError:
        return None


# ---------- commands ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not access.has_access(user_id):
        await _reply_no_access(update)
        return
    await update.message.reply_text(
        "Hey. I'm here.\nTalk to me, ask for a poem, or ask for a story."
    )


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Utility command so you and her can find your own Telegram user IDs
    to put into access.py's CREATOR_IDS."""
    user_id = update.effective_user.id
    await update.message.reply_text(f"Your Telegram user ID is: {user_id}")


async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not access.is_creator(user_id):
        await _reply_no_access(update)
        return

    target = _parse_user_id(context.args)
    if target is None:
        await update.message.reply_text("Usage: /access <telegram_user_id>")
        return

    if access.grant_access(target):
        await update.message.reply_text(f"Access granted to {target}.")
    else:
        await update.message.reply_text(f"{target} already has access.")


async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not access.is_creator(user_id):
        await _reply_no_access(update)
        return

    target = _parse_user_id(context.args)
    if target is None:
        await update.message.reply_text("Usage: /revoke <telegram_user_id>")
        return

    if access.revoke_access(target):
        await update.message.reply_text(f"Access revoked for {target}.")
    else:
        await update.message.reply_text(
            f"{target} wasn't in the granted list (or is a creator, who can't be revoked)."
        )


async def list_access_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not access.is_creator(user_id):
        await _reply_no_access(update)
        return

    data = access.list_access()
    lines = ["Creators:"]
    lines += [f"  {uid}" for uid in data["creators"]]
    lines.append("Granted:")
    lines += [f"  {uid}" for uid in data["granted"]] or ["  (none)"]
    await update.message.reply_text("\n".join(lines))


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not access.has_access(user_id):
        await _reply_no_access(update)
        return
    reset_history(update.effective_chat.id)
    await update.message.reply_text("Conversation reset.")


# ---------- freeform chat / poem / story ----------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not access.has_access(user_id):
        await _reply_no_access(update)
        return

    chat_id = update.effective_chat.id
    user_text = update.message.text

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        reply = ask_sian(chat_id, user_text)
    except Exception:
        logger.exception("Error calling AI")
        reply = "Something went wrong on my end. Try again in a moment."

    await update.message.reply_text(reply)


# ---------- app setup ----------

def build_app() -> Application:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CommandHandler("access", grant))
    app.add_handler(CommandHandler("revoke", revoke))
    app.add_handler(CommandHandler("listaccess", list_access_cmd))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app
