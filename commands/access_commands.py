"""
/access, /revoke, /listaccess - creator-only access management.
"""

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, CommandHandler

import access
from branding import header, DOT_DIVIDER
from menus import refresh_private_menu


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
        return  # silent

    target = _parse_user_id(context.args)
    if target is None:
        await update.message.reply_text("Usage: /access <telegram_user_id>")
        return

    if access.grant_access(target):
        await refresh_private_menu(context.bot, target)
        await update.message.reply_text(f"Access granted to {target}.")
    else:
        await update.message.reply_text(f"{target} already has access.")


async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not access.is_creator(user_id):
        return  # silent

    target = _parse_user_id(context.args)
    if target is None:
        await update.message.reply_text("Usage: /revoke <telegram_user_id>")
        return

    if access.revoke_access(target):
        await refresh_private_menu(context.bot, target)
        await update.message.reply_text(f"Access revoked for {target}.")
    else:
        await update.message.reply_text(
            f"{target} wasn't in the granted list (or is a creator, who can't be revoked)."
        )


async def _describe_user(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> str:
    try:
        chat = await context.bot.get_chat(user_id)
        if chat.username:
            return f"[@{chat.username}](https://t.me/{chat.username})"
        name = chat.first_name or "user"
        return f"[{name}](tg://user?id={user_id})"
    except TelegramError:
        return str(user_id)


async def list_access_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not access.is_creator(user_id):
        return  # silent

    data = access.list_access()

    creator_lines = [await _describe_user(context, uid) for uid in data["creators"]]
    granted_lines = [await _describe_user(context, uid) for uid in data["granted"]] or ["_none_"]

    text = (
        f"{header('access list')}\n\n"
        "*creators*\n" + "\n".join(f"• {line}" for line in creator_lines) + "\n\n"
        f"{DOT_DIVIDER}\n\n"
        "*granted*\n" + "\n".join(f"• {line}" for line in granted_lines)
    )
    await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)


def register(app) -> None:
    app.add_handler(CommandHandler("access", grant))
    app.add_handler(CommandHandler("revoke", revoke))
    app.add_handler(CommandHandler("listaccess", list_access_cmd))
