"""
/hangman - classic word guessing. Bot picks a secret word, shows blanks,
players guess letters via buttons. Limited wrong guesses before the word
is revealed. Open to everyone in the group - anyone can guess, not just
whoever started it.
"""

import html
import random

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from branding import header, DOT_DIVIDER
from commands.group.leaderboard import record_win

MAX_WRONG = 6

WORDS = [
    "resilience", "aesthetic", "poem", "goddess", "confession",
    "moonlight", "silence", "journey", "shelter", "witness",
    "gratitude", "wildfire", "horizon", "whisper", "solitude",
    "sunrise", "anchor", "mirror", "flourish", "quiet",
]

STAGES = [
    "😌",  # 0 wrong
    "😐",  # 1
    "😟",  # 2
    "😧",  # 3
    "😨",  # 4
    "💀",  # 5
    "☠️",  # 6 - hanged
]

_games: dict[int, dict] = {}  # chat_id -> game state


def _blanks(word: str, guessed: set) -> str:
    return " ".join(c if c in guessed else "_" for c in word)


def _keyboard(chat_id: int, guessed: set) -> InlineKeyboardMarkup:
    import string
    rows = []
    row = []
    for letter in string.ascii_lowercase:
        if letter in guessed:
            continue
        row.append(InlineKeyboardButton(letter, callback_data=f"hangman:{chat_id}:{letter}"))
        if len(row) == 7:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _status_text(word: str, guessed: set, wrong: int) -> str:
    stage = STAGES[min(wrong, MAX_WRONG)]
    return (
        f"{header('hangman')}\n\n"
        f"{stage}  wrong guesses: {wrong}/{MAX_WRONG}\n\n"
        f"{_blanks(word, guessed)}\n\n"
        f"{DOT_DIVIDER}\nanyone can guess a letter."
    )


async def hangman(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This only works inside a group.")
        return

    chat_id = update.effective_chat.id
    if chat_id in _games:
        await update.message.reply_text("A hangman game is already running here.")
        return

    word = random.choice(WORDS)
    _games[chat_id] = {"word": word, "guessed": set(), "wrong": 0}

    sent = await update.message.reply_text(_status_text(word, set(), 0))
    await sent.edit_reply_markup(reply_markup=_keyboard(chat_id, set()))
    _games[chat_id]["message_id"] = sent.message_id


async def endhangman(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        return
    import access
    if not (access.is_creator(update.effective_user.id) or access.is_group_admin(update.effective_user.id)):
        return  # silent
    chat_id = update.effective_chat.id
    if chat_id in _games:
        word = _games[chat_id]["word"]
        del _games[chat_id]
        await update.message.reply_text(f"hangman ended - the word was *{word}*.", parse_mode="Markdown")
    else:
        await update.message.reply_text("no hangman game running here.")


async def hangman_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, chat_id_str, letter = query.data.split(":")
    chat_id = int(chat_id_str)

    game = _games.get(chat_id)
    if not game:
        await query.answer("This game has ended.", show_alert=True)
        return

    if letter in game["guessed"]:
        await query.answer("Already guessed.", show_alert=True)
        return

    await query.answer()
    game["guessed"].add(letter)

    if letter not in game["word"]:
        game["wrong"] += 1

    word, guessed, wrong = game["word"], game["guessed"], game["wrong"]
    solved = all(c in guessed for c in word)

    if solved:
        record_win(chat_id, query.from_user.id, query.from_user.first_name, "hangman")
        del _games[chat_id]
        safe_name = html.escape(query.from_user.first_name)
        await query.edit_message_text(
            f"{header('hangman')}\n\n🏆 solved! the word was <b>{word}</b>.\n"
            f"last letter guessed by {safe_name}.",
            parse_mode="HTML",
        )
        return

    if wrong >= MAX_WRONG:
        del _games[chat_id]
        await query.edit_message_text(
            f"{header('hangman')}\n\n☠️ out of guesses. the word was *{word}*.",
            parse_mode="Markdown",
        )
        return

    await query.edit_message_text(
        _status_text(word, guessed, wrong), reply_markup=_keyboard(chat_id, guessed)
    )


def register(app) -> None:
    app.add_handler(CommandHandler("hangman", hangman))
    app.add_handler(CommandHandler("endhangman", endhangman))
    app.add_handler(CallbackQueryHandler(hangman_callback, pattern="^hangman:"))
