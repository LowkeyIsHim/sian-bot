"""
Manual moderation commands - /mute, /unmute, /ban, /unban, /warn,
/clearwarns. Group-admin only. Reply to the target's message to act on
them (simplest, no need to know their numeric ID).
"""

from datetime import datetime, timedelta, timezone

from telegram import ChatPermissions, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, CommandHandler

import access
from commands.group.enforcement import add_warning, clear_warnings, get_warnings


def _get_target(update: Update):
    """Target is whoever the command replies to."""
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user
    return None


async def _check_admin(update: Update) -> bool:
    if update.effective_chat.type not in ("group", "supergroup"):
        return False
    if not access.is_group_admin(update.effective_user.id):
        return False  # silent - don't confirm to randoms that this command exists
    return True


async def mute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_admin(update):
        return
    target = _get_target(update)
    if not target:
        await update.message.reply_text("Reply to the message of the person you want to mute.")
        return
    minutes = int(context.args[0]) if context.args and context.args[0].isdigit() else 10
    until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    try:
        await context.bot.restrict_chat_member(
            update.effective_chat.id, target.id,
            permissions=ChatPermissions(can_send_messages=False), until_date=until,
        )
        await update.message.reply_text(f"Muted {target.first_name} for {minutes} minute(s).")
    except TelegramError as e:
        await update.message.reply_text(f"Couldn't mute them: {e}")


async def unmute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_admin(update):
        return
    target = _get_target(update)
    if not target:
        await update.message.reply_text("Reply to the message of the person you want to unmute.")
        return
    try:
        await context.bot.restrict_chat_member(
            update.effective_chat.id, target.id,
            permissions=ChatPermissions(
                can_send_messages=True, can_send_other_messages=True,
                can_send_polls=True, can_add_web_page_previews=True,
            ),
        )
        await update.message.reply_text(f"Unmuted {target.first_name}.")
    except TelegramError as e:
        await update.message.reply_text(f"Couldn't unmute them: {e}")


async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_admin(update):
        return
    target = _get_target(update)
    if not target:
        await update.message.reply_text("Reply to the message of the person you want to ban.")
        return
    try:
        await context.bot.ban_chat_member(update.effective_chat.id, target.id)
        await update.message.reply_text(f"Banned {target.first_name}.")
    except TelegramError as e:
        await update.message.reply_text(f"Couldn't ban them: {e}")


async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_admin(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /unban <telegram_user_id>")
        return
    user_id = int(context.args[0])
    try:
        await context.bot.unban_chat_member(update.effective_chat.id, user_id, only_if_banned=True)
        await update.message.reply_text(f"Unbanned {user_id}.")
    except TelegramError as e:
        await update.message.reply_text(f"Couldn't unban them: {e}")


async def warn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_admin(update):
        return
    target = _get_target(update)
    if not target:
        await update.message.reply_text("Reply to the message of the person you want to warn.")
        return
    count = add_warning(update.effective_chat.id, target.id)
    await update.message.reply_text(f"{target.first_name} now has {count} warning(s).")


async def clearwarns(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_admin(update):
        return
    target = _get_target(update)
    if not target:
        await update.message.reply_text("Reply to the message of the person to clear warnings for.")
        return
    clear_warnings(update.effective_chat.id, target.id)
    await update.message.reply_text(f"Cleared warnings for {target.first_name}.")


async def warnings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_admin(update):
        return
    target = _get_target(update)
    if not target:
        await update.message.reply_text("Reply to the message of the person to check.")
        return
    count = get_warnings(update.effective_chat.id, target.id)
    await update.message.reply_text(f"{target.first_name} has {count} warning(s).")


def register(app) -> None:
    app.add_handler(CommandHandler("mute", mute))
    app.add_handler(CommandHandler("unmute", unmute))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("warn", warn))
    app.add_handler(CommandHandler("clearwarns", clearwarns))
    app.add_handler(CommandHandler("warnings", warnings_cmd))
