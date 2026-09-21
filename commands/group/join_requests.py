"""
Handles Telegram's "Approve New Members" group setting (Group Info >
Permissions). Only fires if that setting is turned ON for the group -
otherwise people join instantly and this never triggers.

When someone requests to join, admins get a DM with Approve/Decline
buttons. /approve and /decline also work as manual fallback commands,
run inside the group.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler, ChatJoinRequestHandler, CommandHandler, ContextTypes

import access


async def _on_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    request = update.chat_join_request
    chat = request.chat
    user = request.from_user

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"joinreq:approve:{chat.id}:{user.id}"),
        InlineKeyboardButton("❌ Decline", callback_data=f"joinreq:decline:{chat.id}:{user.id}"),
    ]])

    username_note = f"@{user.username}" if user.username else "(no username)"
    text = f"📥 join request for {chat.title}\n\n{user.first_name} {username_note}\nid: {user.id}"

    data = access.list_access()
    recipients = set(data["creators"]) | set(data["group_admins"])
    for admin_id in recipients:
        try:
            await context.bot.send_message(admin_id, text, reply_markup=keyboard)
        except TelegramError:
            pass  # that admin hasn't DM'd the bot yet


async def joinreq_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, action, chat_id_str, user_id_str = query.data.split(":")
    chat_id, user_id = int(chat_id_str), int(user_id_str)

    if not (access.is_creator(query.from_user.id) or access.is_group_admin(query.from_user.id)):
        await query.answer("You don't have access to this.", show_alert=True)
        return

    await query.answer()
    try:
        if action == "approve":
            await context.bot.approve_chat_join_request(chat_id, user_id)
            await query.edit_message_text("✅ approved.")
        else:
            await context.bot.decline_chat_join_request(chat_id, user_id)
            await query.edit_message_text("❌ declined.")
    except TelegramError as e:
        await query.edit_message_text(f"couldn't process: {e}")


async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        return
    if not (access.is_creator(update.effective_user.id) or access.is_group_admin(update.effective_user.id)):
        return  # silent
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /approve <telegram_user_id>")
        return
    try:
        await context.bot.approve_chat_join_request(update.effective_chat.id, int(context.args[0]))
        await update.message.reply_text(f"Approved {context.args[0]}.")
    except TelegramError as e:
        await update.message.reply_text(f"Couldn't approve: {e}")


async def decline_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        return
    if not (access.is_creator(update.effective_user.id) or access.is_group_admin(update.effective_user.id)):
        return  # silent
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /decline <telegram_user_id>")
        return
    try:
        await context.bot.decline_chat_join_request(update.effective_chat.id, int(context.args[0]))
        await update.message.reply_text(f"Declined {context.args[0]}.")
    except TelegramError as e:
        await update.message.reply_text(f"Couldn't decline: {e}")


def register(app) -> None:
    app.add_handler(ChatJoinRequestHandler(_on_join_request))
    app.add_handler(CallbackQueryHandler(joinreq_callback, pattern="^joinreq:"))
    app.add_handler(CommandHandler("approve", approve_cmd))
    app.add_handler(CommandHandler("decline", decline_cmd))
