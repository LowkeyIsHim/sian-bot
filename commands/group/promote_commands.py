"""
/promote, /demote - actually promote/demote someone to a real Telegram
group admin (not just our internal permission tier). Reply to their
message to target them. Creator-only, since this hands out real power.

/listadmin - lists the group's actual current Telegram admins. Open to
everyone (informational only).

The bot itself must already be a real admin with "can promote members"
rights for /promote and /demote to work - Telegram blocks the API call
otherwise, and there's no way around that from code.
"""

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, CommandHandler

import access
from menus import refresh_group_menu


def _get_target(update: Update):
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user
    return None


def _is_anonymous_admin_post(user) -> bool:
    """Messages sent by an admin with 'remain anonymous' turned on show up
    as this system account, not the real user - Telegram can't resolve a
    real user ID from it, which is what causes USER_ID_INVALID."""
    return user.username == "GroupAnonymousBot" or user.id == 1087968824


async def promote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        return
    if not access.is_creator(update.effective_user.id):
        return  # silent - only creators hand out real admin rights

    target = _get_target(update)
    if not target:
        await update.message.reply_text("Reply to the message of the person you want to promote.")
        return
    if _is_anonymous_admin_post(target):
        await update.message.reply_text(
            "Can't target that - they posted with 'remain anonymous' on, so "
            "Telegram won't tell me who they really are. Ask them to turn "
            "that off and post again, or promote them directly in Telegram's "
            "own admin settings."
        )
        return

    chat_id = update.effective_chat.id
    try:
        await context.bot.promote_chat_member(
            chat_id, target.id,
            can_delete_messages=True,
            can_restrict_members=True,
            can_invite_users=True,
            can_pin_messages=True,
            can_manage_chat=True,
        )
    except TelegramError as e:
        await update.message.reply_text(f"Couldn't promote them: {e}")
        return

    access.grant_group_admin(target.id)
    await refresh_group_menu(context.bot, chat_id, target.id)
    await update.message.reply_text(f"{target.first_name} is now a group admin.")


async def demote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        return
    if not access.is_creator(update.effective_user.id):
        return

    target = _get_target(update)
    if not target:
        await update.message.reply_text("Reply to the message of the person you want to demote.")
        return
    if _is_anonymous_admin_post(target):
        await update.message.reply_text(
            "Can't target that - they posted with 'remain anonymous' on, so "
            "Telegram won't tell me who they really are. Ask them to turn "
            "that off and post again, or demote them directly in Telegram's "
            "own admin settings."
        )
        return

    chat_id = update.effective_chat.id
    try:
        await context.bot.promote_chat_member(
            chat_id, target.id,
            can_delete_messages=False,
            can_restrict_members=False,
            can_invite_users=False,
            can_pin_messages=False,
            can_manage_chat=False,
        )
    except TelegramError as e:
        await update.message.reply_text(f"Couldn't demote them: {e}")
        return

    access.revoke_group_admin(target.id)
    await refresh_group_menu(context.bot, chat_id, target.id)
    await update.message.reply_text(f"{target.first_name} is no longer a group admin.")


async def _describe_admin(member) -> str:
    user = member.user
    if user.username:
        return f"[@{user.username}](https://t.me/{user.username})"
    return f"[{user.first_name}](tg://user?id={user.id})"


async def listadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This only works inside a group.")
        return
    try:
        members = await context.bot.get_chat_administrators(update.effective_chat.id)
    except TelegramError as e:
        await update.message.reply_text(f"Couldn't fetch admins: {e}")
        return
    lines = [await _describe_admin(m) for m in members]
    text = "*group admins*\n" + "\n".join(f"• {line}" for line in lines)
    await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)


def register(app) -> None:
    app.add_handler(CommandHandler("promote", promote))
    app.add_handler(CommandHandler("demote", demote))
    app.add_handler(CommandHandler("listadmin", listadmin))
