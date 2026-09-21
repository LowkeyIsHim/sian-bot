"""
Manual moderation commands - /mute, /unmute, /ban, /unban, /warn,
/clearwarns. Group-admin only. Reply to the target's message to act on
them (simplest, no need to know their numeric ID).

The admin's own command message auto-deletes after running, to keep the
group clean - the resulting notification (who got muted/warned/etc) stays
visible since that's the useful part.
"""

from datetime import datetime, timedelta, timezone

from telegram import ChatPermissions, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, CommandHandler

import access
from commands.group.enforcement import add_warning, clear_warnings, get_warnings, mention_html


def _get_target(update: Update):
    """Target is whoever the command replies to."""
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user
    return None


async def _check_admin(update: Update) -> bool:
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This only works inside a group.")
        return False
    if not access.is_group_admin(update.effective_user.id):
        return False  # silent - don't confirm to randoms that this exists
    return True


async def _delete_command(update: Update) -> None:
    try:
        await update.message.delete()
    except TelegramError:
        pass


async def mute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_admin(update):
        return
    target = _get_target(update)
    if not target:
        await update.message.reply_text("Reply to the message of the person you want to mute.")
        return
    minutes = int(context.args[0]) if context.args and context.args[0].isdigit() else 10
    until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    chat_id = update.effective_chat.id
    try:
        await context.bot.restrict_chat_member(
            chat_id, target.id,
            permissions=ChatPermissions(can_send_messages=False), until_date=until,
        )
        mention = await mention_html(context, chat_id, target.id)
        await _delete_command(update)
        await context.bot.send_message(chat_id, f"{mention} muted for {minutes} minute(s).", parse_mode="HTML")
    except TelegramError as e:
        await update.message.reply_text(f"Couldn't mute them: {e}")


async def unmute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_admin(update):
        return
    target = _get_target(update)
    if not target:
        await update.message.reply_text("Reply to the message of the person you want to unmute.")
        return
    chat_id = update.effective_chat.id
    try:
        await context.bot.restrict_chat_member(
            chat_id, target.id,
            permissions=ChatPermissions(
                can_send_messages=True, can_send_other_messages=True,
                can_send_polls=True, can_add_web_page_previews=True,
            ),
        )
        mention = await mention_html(context, chat_id, target.id)
        await _delete_command(update)
        await context.bot.send_message(chat_id, f"{mention} unmuted.", parse_mode="HTML")
    except TelegramError as e:
        await update.message.reply_text(f"Couldn't unmute them: {e}")


async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_admin(update):
        return
    target = _get_target(update)
    if not target:
        await update.message.reply_text("Reply to the message of the person you want to ban.")
        return
    chat_id = update.effective_chat.id
    try:
        await context.bot.ban_chat_member(chat_id, target.id)
        mention = await mention_html(context, chat_id, target.id)
        await _delete_command(update)
        await context.bot.send_message(chat_id, f"{mention} banned.", parse_mode="HTML")
    except TelegramError as e:
        await update.message.reply_text(f"Couldn't ban them: {e}")


async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_admin(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /unban <telegram_user_id>")
        return
    user_id = int(context.args[0])
    chat_id = update.effective_chat.id
    try:
        await context.bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
        await _delete_command(update)
        await context.bot.send_message(chat_id, f"Unbanned {user_id}.")
    except TelegramError as e:
        await update.message.reply_text(f"Couldn't unban them: {e}")


async def warn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_admin(update):
        return
    target = _get_target(update)
    if not target:
        await update.message.reply_text("Reply to the message of the person you want to warn.")
        return
    chat_id = update.effective_chat.id
    count = add_warning(chat_id, target.id)
    mention = await mention_html(context, chat_id, target.id)
    await _delete_command(update)
    await context.bot.send_message(chat_id, f"{mention} now has {count} warning(s).", parse_mode="HTML")


async def clearwarns(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_admin(update):
        return
    target = _get_target(update)
    if not target:
        await update.message.reply_text("Reply to the message of the person to clear warnings for.")
        return
    chat_id = update.effective_chat.id
    clear_warnings(chat_id, target.id)
    mention = await mention_html(context, chat_id, target.id)
    await _delete_command(update)
    await context.bot.send_message(chat_id, f"Cleared warnings for {mention}.", parse_mode="HTML")


async def warnings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_admin(update):
        return
    target = _get_target(update)
    if not target:
        await update.message.reply_text("Reply to the message of the person to check.")
        return
    chat_id = update.effective_chat.id
    count = get_warnings(chat_id, target.id)
    mention = await mention_html(context, chat_id, target.id)
    # not auto-deleted - this is an informational lookup worth keeping visible
    await update.message.reply_text(f"{mention} has {count} warning(s).", parse_mode="HTML")


def register(app) -> None:
    app.add_handler(CommandHandler("mute", mute))
    app.add_handler(CommandHandler("unmute", unmute))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("warn", warn))
    app.add_handler(CommandHandler("clearwarns", clearwarns))
    app.add_handler(CommandHandler("warnings", warnings_cmd))
