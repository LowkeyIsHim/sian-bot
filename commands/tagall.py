"""
/tagall [message] - mentions everyone the bot has seen post in this group.
Group-admin only (see access.is_group_admin) - not the same as regular
/access, and not the same as being a real Telegram group admin.
"""

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

import access
from commands.member_tracker import get_members

CHUNK_SIZE = 50  # keep each message a reasonable length/entity count


async def tagall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This only works inside a group.")
        return

    if not access.is_group_admin(update.effective_user.id):
        await update.message.reply_text("You don't have access to this bot.")
        return

    members = get_members(update.effective_chat.id)
    if not members:
        await update.message.reply_text(
            "I haven't seen anyone post here yet - I can only tag people "
            "once they've sent at least one message in this group."
        )
        return

    note = " ".join(context.args) if context.args else "tagging everyone"

    mentions = []
    for user_id_str, info in members.items():
        username = info.get("username")
        display = f"@{username}" if username else (info.get("first_name") or "there")
        mentions.append(f"[{display}](tg://user?id={user_id_str})")

    for i in range(0, len(mentions), CHUNK_SIZE):
        chunk = mentions[i : i + CHUNK_SIZE]
        prefix = f"{note}\n\n" if i == 0 else ""
        await update.message.reply_text(
            prefix + " ".join(chunk), parse_mode="Markdown", disable_web_page_preview=True
        )


def register(app) -> None:
    app.add_handler(CommandHandler("tagall", tagall))
