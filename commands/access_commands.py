"""
/access, /revoke, /listaccess - creator-only access management.
"""

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

import access


def _parse_user_id(args: list[str]) -> int | None:
    if not args:
        return None
    try:
        return int(args[0])
    except ValueError:
        return None


async def grant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not access.is_creator(user_id):
        await update.message.reply_text("You don't have access to this bot.")
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
        await update.message.reply_text("You don't have access to this bot.")
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
        await update.message.reply_text("You don't have access to this bot.")
        return

    data = access.list_access()
    lines = ["Creators:"]
    lines += [f"  {uid}" for uid in data["creators"]]
    lines.append("Granted:")
    lines += [f"  {uid}" for uid in data["granted"]] or ["  (none)"]
    await update.message.reply_text("\n".join(lines))


def register(app) -> None:
    app.add_handler(CommandHandler("access", grant))
    app.add_handler(CommandHandler("revoke", revoke))
    app.add_handler(CommandHandler("listaccess", list_access_cmd))
