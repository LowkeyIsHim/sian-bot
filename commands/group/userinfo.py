"""
/userinfo - reply to someone (or use it with no reply for your own info)
to see their full profile: ID, status, admin levels, warnings, and when
the bot first saw them.
"""

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, CommandHandler

import access
from branding import header
from commands.group.enforcement import get_warnings
from commands.group.member_tracker import get_member_info


async def userinfo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This only works inside a group.")
        return

    target = (
        update.message.reply_to_message.from_user
        if update.message.reply_to_message
        else update.effective_user
    )
    chat_id = update.effective_chat.id

    try:
        member = await context.bot.get_chat_member(chat_id, target.id)
        chat_status = member.status
    except TelegramError:
        chat_status = "unknown"

    tracked = get_member_info(chat_id, target.id) or {}
    warnings = get_warnings(chat_id, target.id)

    lines = [header("user info"), ""]
    lines.append(f"name: {target.first_name}" + (f" {target.last_name}" if target.last_name else ""))
    lines.append(f"username: @{target.username}" if target.username else "username: (none)")
    lines.append(f"telegram id: {target.id}")
    lines.append(f"is bot: {'yes' if target.is_bot else 'no'}")
    lines.append(f"chat status: {chat_status}")
    lines.append(f"creator (bot owner): {'yes' if access.is_creator(target.id) else 'no'}")
    lines.append(f"group admin (bot tier): {'yes' if access.is_group_admin(target.id) else 'no'}")
    lines.append(f"warnings: {warnings}")

    if tracked:
        lines.append(f"first seen by me: {tracked.get('first_seen', 'unknown')[:10]}")
        lines.append(f"messages tracked: {tracked.get('message_count', 0)}")
    else:
        lines.append("first seen by me: not yet tracked")

    await update.message.reply_text("\n".join(lines))


def register(app) -> None:
    app.add_handler(CommandHandler("userinfo", userinfo))
