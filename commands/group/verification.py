"""
New-member verification: when someone joins, they're muted and given a
button to tap within a time limit. Tap in time -> unmuted, welcomed.
Miss the window -> kicked (can rejoin and try again). Kills most
scam/spam accounts before they can ever post.

Also houses /rules and /setrules, since they're naturally shown together
in the welcome flow.
"""

import asyncio
import json
import os

from telegram import (
    ChatMemberUpdated,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler, ChatMemberHandler, CommandHandler, ContextTypes

import access
from branding import header

VERIFY_WINDOW_SECONDS = 300  # 5 minutes

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PERSISTENT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))
RULES_FILE = os.path.join(_PERSISTENT_DIR, "rules.json")

# (chat_id, user_id) -> token. A new join or a new attempt bumps the
# token, invalidating any in-flight timeout/verify callback tied to the
# old one - avoids a stale timer kicking someone who already verified.
_pending: dict[tuple, int] = {}


def _load_rules() -> dict:
    if not os.path.exists(RULES_FILE):
        return {}
    with open(RULES_FILE, "r") as f:
        return json.load(f)


def _save_rules(data: dict) -> None:
    with open(RULES_FILE, "w") as f:
        json.dump(data, f, indent=2)


async def setrules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Run this inside the group.")
        return
    if not (access.is_creator(update.effective_user.id) or access.is_group_admin(update.effective_user.id)):
        return  # silent

    text = update.message.text.partition(" ")[2].strip()
    if not text:
        await update.message.reply_text("Usage: /setrules <text>")
        return

    data = _load_rules()
    data[str(update.effective_chat.id)] = text
    _save_rules(data)
    await update.message.reply_text("Rules updated.")


async def rules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This only works inside a group.")
        return
    data = _load_rules()
    text = data.get(str(update.effective_chat.id))
    if not text:
        await update.message.reply_text("No rules have been set for this group yet.")
        return
    await update.message.reply_text(f"{header('rules')}\n\n{text}")


async def _on_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result: ChatMemberUpdated = update.chat_member
    if result is None:
        return
    chat = result.chat
    if chat.type not in ("group", "supergroup"):
        return

    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    user = result.new_chat_member.user

    if user.is_bot:
        return

    just_joined = old_status in ("left", "kicked") and new_status == "member"
    if not just_joined:
        return

    chat_id, user_id = chat.id, user.id

    try:
        await context.bot.restrict_chat_member(
            chat_id, user_id, permissions=ChatPermissions(can_send_messages=False)
        )
    except TelegramError:
        pass  # bot may lack rights - verification is still offered either way

    token = _pending.get((chat_id, user_id), 0) + 1
    _pending[(chat_id, user_id)] = token

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ I'm not a bot - verify me",
            callback_data=f"verify:{chat_id}:{user_id}:{token}",
        )
    ]])

    rules_note = "\nuse /rules to see this group's rules." if str(chat_id) in _load_rules() else ""

    sent = await context.bot.send_message(
        chat_id,
        f"welcome, {user.first_name}. tap below within "
        f"{VERIFY_WINDOW_SECONDS // 60} minutes to verify you're human - "
        f"you're muted until then.{rules_note}",
        reply_markup=keyboard,
    )
    asyncio.create_task(_expire_verification(context.bot, chat_id, user_id, token, sent.message_id))


async def _expire_verification(bot, chat_id: int, user_id: int, token: int, message_id: int) -> None:
    await asyncio.sleep(VERIFY_WINDOW_SECONDS)
    if _pending.get((chat_id, user_id)) != token:
        return  # already verified, or superseded by a newer join

    del _pending[(chat_id, user_id)]
    try:
        await bot.ban_chat_member(chat_id, user_id)
        await bot.unban_chat_member(chat_id, user_id)  # kick, not permanent - can rejoin
    except TelegramError:
        pass
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id,
            text="⏰ didn't verify in time - removed. you're welcome to rejoin and try again.",
        )
    except TelegramError:
        pass


async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, chat_id_str, user_id_str, token_str = query.data.split(":")
    chat_id, user_id, token = int(chat_id_str), int(user_id_str), int(token_str)

    if query.from_user.id != user_id:
        await query.answer("This isn't your verification.", show_alert=True)
        return
    if _pending.get((chat_id, user_id)) != token:
        await query.answer("This verification has expired.", show_alert=True)
        return

    del _pending[(chat_id, user_id)]
    await query.answer("Verified!")

    try:
        await context.bot.restrict_chat_member(
            chat_id, user_id,
            permissions=ChatPermissions(
                can_send_messages=True, can_send_other_messages=True,
                can_send_polls=True, can_add_web_page_previews=True,
            ),
        )
    except TelegramError:
        pass

    await query.edit_message_text(f"✅ {query.from_user.first_name} is verified. welcome!")


def register(app) -> None:
    app.add_handler(CommandHandler("setrules", setrules))
    app.add_handler(CommandHandler("rules", rules_cmd))
    app.add_handler(ChatMemberHandler(_on_member_update, ChatMemberHandler.CHAT_MEMBER), group=6)
    app.add_handler(CallbackQueryHandler(verify_callback, pattern="^verify:"))
