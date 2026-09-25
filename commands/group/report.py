"""
/report - reply to a bad message to quietly flag it to admins via DM.
No public callout, no drama. Open to every member.
"""

import html

from telegram.error import TelegramError
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

import access
from branding import header


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This only works inside a group.")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to the message you want to report.")
        return

    reporter = update.effective_user
    reported_msg = update.message.reply_to_message
    reported_user = reported_msg.from_user
    chat = update.effective_chat

    reported_text = reported_msg.text or reported_msg.caption or "(no text - media/other content)"
    if len(reported_text) > 500:
        reported_text = reported_text[:500] + "..."

    link = (
        f"https://t.me/{chat.username}/{reported_msg.message_id}"
        if chat.username else None
    )

    # HTML + escaping, not Markdown - a chat title, first name, or the
    # reported text itself could contain an unmatched underscore/asterisk
    # that would silently break a Markdown-parsed message.
    notice = (
        f"{header('report')}\n\n"
        f"🚩 in <b>{html.escape(chat.title or 'this group')}</b>\n\n"
        f"reported: {html.escape(reported_user.first_name)} ({reported_user.id})\n"
        f"by: {html.escape(reporter.first_name)} ({reporter.id})\n\n"
        f"message:\n{html.escape(reported_text)}"
    )
    if link:
        notice += f'\n\n<a href="{link}">jump to message</a>'

    data = access.list_access()
    recipients = set(data["creators"]) | set(data["group_admins"])

    sent_to_anyone = False
    for admin_id in recipients:
        try:
            await context.bot.send_message(admin_id, notice, parse_mode="HTML", disable_web_page_preview=True)
            sent_to_anyone = True
        except TelegramError:
            pass  # that admin may have blocked the bot or never started a DM with it

    if sent_to_anyone:
        await update.message.reply_text("🚩 Reported to admins - thanks for flagging it.")
    else:
        await update.message.reply_text(
            "Couldn't reach any admins right now - they may need to message me in DM first."
        )


def register(app) -> None:
    app.add_handler(CommandHandler("report", report))
