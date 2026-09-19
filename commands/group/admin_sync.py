"""
Watches for real Telegram admin status changes made directly through
Telegram's own UI (Group Info > Administrators), and keeps our internal
group_admin permission tier in sync automatically - so someone promoted
the normal Telegram way gets bot group features too, without a separate
/gadmin-style step. Works alongside /promote and /demote, which do the
same sync explicitly.
"""

from telegram import ChatMemberUpdated, Update
from telegram.ext import ContextTypes, ChatMemberHandler

import access
from menus import refresh_group_menu

ADMIN_STATUSES = ("administrator", "creator")


async def _on_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result: ChatMemberUpdated = update.chat_member
    if result is None:
        return

    chat = result.chat
    if chat.type not in ("group", "supergroup"):
        return

    user = result.new_chat_member.user
    if user.is_bot:
        return

    was_admin = result.old_chat_member.status in ADMIN_STATUSES
    is_admin = result.new_chat_member.status in ADMIN_STATUSES

    if is_admin and not was_admin:
        access.grant_group_admin(user.id)
        await refresh_group_menu(context.bot, chat.id, user.id)
    elif was_admin and not is_admin:
        access.revoke_group_admin(user.id)
        await refresh_group_menu(context.bot, chat.id, user.id)


def register(app) -> None:
    app.add_handler(ChatMemberHandler(_on_chat_member_update, ChatMemberHandler.CHAT_MEMBER))
