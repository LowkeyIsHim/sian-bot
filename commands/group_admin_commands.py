"""
/gadmin, /ungadmin, /gadminlist - creator-only management of who has
GROUP admin powers (tag-all, spam controls, moderation).

This is completely separate from /access - someone can have full personal
chat access with none of this, or vice versa. Creators have both, always.
"""

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, CommandHandler

import access
from branding import header, DOT_DIVIDER


def _parse_user_id(args: list[str]) -> int | None:
    if not args:
        return None
    try:
        return int(args[0])
    except ValueError:
        return None


async def gadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not access.is_creator(update.effective_user.id):
        await update.message.reply_text("You don't have access to this bot.")
        return

    target = _parse_user_id(context.args)
    if target is None:
        await update.message.reply_text("Usage: /gadmin <telegram_user_id>")
        return

    if access.grant_group_admin(target):
        await update.message.reply_text(f"{target} can now use group admin features.")
    else:
        await update.message.reply_text(f"{target} already has group admin access.")


async def ungadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not access.is_creator(update.effective_user.id):
        await update.message.reply_text("You don't have access to this bot.")
        return

    target = _parse_user_id(context.args)
    if target is None:
        await update.message.reply_text("Usage: /ungadmin <telegram_user_id>")
        return

    if access.revoke_group_admin(target):
        await update.message.reply_text(f"Group admin access revoked for {target}.")
    else:
        await update.message.reply_text(
            f"{target} wasn't a group admin (or is a creator, who can't be revoked)."
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


async def gadminlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not access.is_creator(update.effective_user.id):
        await update.message.reply_text("You don't have access to this bot.")
        return

    data = access.list_access()
    creator_lines = [await _describe_user(context, uid) for uid in data["creators"]]
    admin_lines = [await _describe_user(context, uid) for uid in data["group_admins"]] or ["_none_"]

    text = (
        f"{header('group admins')}\n\n"
        "*creators (full access)*\n" + "\n".join(f"• {line}" for line in creator_lines) + "\n\n"
        f"{DOT_DIVIDER}\n\n"
        "*group admins*\n" + "\n".join(f"• {line}" for line in admin_lines)
    )
    await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)


def register(app) -> None:
    app.add_handler(CommandHandler("gadmin", gadmin))
    app.add_handler(CommandHandler("ungadmin", ungadmin))
    app.add_handler(CommandHandler("gadminlist", gadminlist))
