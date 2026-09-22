"""
/trivia - posts a random multiple-choice question with a time limit.
First correct answer wins the round. Open to everyone.
"""

import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from ai import get_trivia_question, RateLimitError
from branding import header, DOT_DIVIDER
from commands.group.leaderboard import record_win
from rate_limit import global_is_rate_limited

TRIVIA_SECONDS = 20

_active: dict[int, dict] = {}  # message_id -> round state


async def trivia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This only works inside a group.")
        return

    if global_is_rate_limited("ai", limit=15, window_seconds=30):
        await update.message.reply_text("give it a moment - too many requests right now.")
        return

    try:
        q = get_trivia_question()
    except RateLimitError:
        await update.message.reply_text("i need a moment - try again shortly.")
        return
    except Exception:
        await update.message.reply_text("couldn't come up with a question - try again.")
        return

    sent = await update.message.reply_text(
        f"{header('trivia')}\n\n{q['question']}\n\n{DOT_DIVIDER}\nyou have {TRIVIA_SECONDS}s!"
    )
    game_id = sent.message_id
    _active[game_id] = {
        "correct_index": q["correct_index"],
        "options": q["options"],
        "answered": False,
        "chat_id": update.effective_chat.id,
    }
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(opt, callback_data=f"trivia:{game_id}:{i}")]
        for i, opt in enumerate(q["options"])
    ])
    await sent.edit_reply_markup(reply_markup=keyboard)
    asyncio.create_task(_expire(context.bot, game_id))


async def _expire(bot, game_id: int) -> None:
    await asyncio.sleep(TRIVIA_SECONDS)
    game = _active.get(game_id)
    if not game or game["answered"]:
        return
    game["answered"] = True
    correct = game["options"][game["correct_index"]]
    try:
        await bot.edit_message_text(
            chat_id=game["chat_id"], message_id=game_id,
            text=f"⏰ time's up! the answer was: {correct}",
        )
    except Exception:
        pass
    del _active[game_id]


async def trivia_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, game_id_str, choice_str = query.data.split(":")
    game_id, choice = int(game_id_str), int(choice_str)

    game = _active.get(game_id)
    if not game or game["answered"]:
        await query.answer("This round has ended.", show_alert=True)
        return

    if choice != game["correct_index"]:
        await query.answer("❌ not quite!", show_alert=True)
        return

    game["answered"] = True
    await query.answer("✅ correct!")
    winner = query.from_user
    record_win(game["chat_id"], winner.id, winner.first_name, "trivia")
    correct = game["options"][game["correct_index"]]
    await query.edit_message_text(f"🏆 {winner.first_name} got it first! the answer was: {correct}")
    del _active[game_id]


def register(app) -> None:
    app.add_handler(CommandHandler("trivia", trivia))
    app.add_handler(CallbackQueryHandler(trivia_callback, pattern="^trivia:"))
