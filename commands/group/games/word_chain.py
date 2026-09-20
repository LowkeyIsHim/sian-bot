"""
/startwcg [word] - Word Chain Game: each new word must start with the
last letter of the previous one, no repeats. /endwcg to stop.
Passively watches messages while active - ignores anything that isn't a
plausible single-word attempt, so it doesn't interfere with normal chat.
"""

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

_active: dict[int, dict] = {}  # chat_id -> game state


async def startwcg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This only works inside a group.")
        return

    chat_id = update.effective_chat.id
    starting_word = (context.args[0].lower() if context.args else "start")
    if not starting_word.isalpha():
        starting_word = "start"

    _active[chat_id] = {
        "last_letter": starting_word[-1],
        "used": {starting_word},
        "last_player": None,
    }
    await update.message.reply_text(
        f"🔤 word chain started with *{starting_word}*.\n"
        f"next word must start with *{starting_word[-1].upper()}*.\n"
        f"no repeats. /endwcg to stop.",
        parse_mode="Markdown",
    )


async def endwcg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        return
    chat_id = update.effective_chat.id
    if chat_id in _active:
        del _active[chat_id]
        await update.message.reply_text("word chain ended.")
    else:
        await update.message.reply_text("no word chain running here.")


async def _check_word(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None or chat.type not in ("group", "supergroup"):
        return
    game = _active.get(chat.id)
    if not game:
        return

    message = update.effective_message
    if message is None or not message.text:
        return

    word = message.text.strip().lower()
    if not word.isalpha():
        return  # not a plausible word-chain move, ignore - don't interfere with normal chat

    if word[0] != game["last_letter"]:
        return  # doesn't fit - probably just normal conversation, ignore silently

    user_id = update.effective_user.id

    if word in game["used"]:
        await message.reply_text(f'"{word}" was already used.')
        return
    if user_id == game["last_player"]:
        await message.reply_text("wait for someone else to go before you play again.")
        return

    game["used"].add(word)
    game["last_letter"] = word[-1]
    game["last_player"] = user_id
    await message.reply_text(f"✅ next: *{word[-1].upper()}*", parse_mode="Markdown")


def register(app) -> None:
    app.add_handler(CommandHandler("startwcg", startwcg))
    app.add_handler(CommandHandler("endwcg", endwcg))
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, _check_word),
        group=5,
    )
