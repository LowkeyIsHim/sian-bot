"""
/purge - reply to the message where you want cleanup to start, then run
/purge. Deletes every message from there up through your command
(inclusive). Group admin only.
"""

import asyncio

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, CommandHandler

import access

MAX_PURGE = 200  # safety cap - Telegram allows deleting up to 100 IDs per call


async def purge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This only works inside a group.")
        return
    if not (access.is_creator(update.effective_user.id) or access.is_group_admin(update.effective_user.id)):
        return  # silent

    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to the message you want cleanup to start from.")
        return

    chat_id = update.effective_chat.id
    start_id = update.message.reply_to_message.message_id
    end_id = update.message.message_id

    if end_id - start_id > MAX_PURGE:
        await update.message.reply_text(f"That's more than {MAX_PURGE} messages - narrow it down.")
        return

    ids = list(range(start_id, end_id + 1))

    deleted = 0
    for i in range(0, len(ids), 100):  # Telegram allows up to 100 IDs per deleteMessages call
        chunk = ids[i : i + 100]
        try:
            await context.bot.delete_messages(chat_id, chunk)
            deleted += len(chunk)
        except TelegramError:
            # some IDs in the chunk may be too old or already gone - fall
            # back to deleting one at a time so a single bad ID doesn't
            # block the rest
            for mid in chunk:
                try:
                    await context.bot.delete_message(chat_id, mid)
                    deleted += 1
                except TelegramError:
                    pass

    notice = await context.bot.send_message(chat_id, f"🧹 purged {deleted} messages.")

    async def _cleanup():
        await asyncio.sleep(5)
        try:
            await context.bot.delete_message(chat_id, notice.message_id)
        except TelegramError:
            pass
    asyncio.create_task(_cleanup())


def register(app) -> None:
    app.add_handler(CommandHandler("purge", purge))
