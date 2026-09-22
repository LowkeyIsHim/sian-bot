"""
/rps - reply to someone's message to challenge them to rock-paper-scissors.
Both pick secretly via buttons, revealed once both have chosen.
"""

import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from branding import header, DOT_DIVIDER
from commands.group.leaderboard import record_win

CHOICES = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}

_games: dict[int, dict] = {}  # message_id -> game state


def _keyboard(game_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"{CHOICES[c]} {c}", callback_data=f"rps:{game_id}:{c}") for c in CHOICES
    ]])


async def rps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This only works inside a group.")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to the message of the person you want to challenge.")
        return

    challenger = update.effective_user
    opponent = update.message.reply_to_message.from_user
    if opponent.id == challenger.id or opponent.is_bot:
        await update.message.reply_text("Pick a real opponent.")
        return

    sent = await update.message.reply_text(
        f"{header('rock paper scissors')}\n\n"
        f"{challenger.first_name} vs {opponent.first_name}\n"
        f"{DOT_DIVIDER}\nboth players pick - your choice stays hidden until both are in."
    )
    game_id = sent.message_id
    _games[game_id] = {
        "players": {challenger.id: None, opponent.id: None},
        "names": {challenger.id: challenger.first_name, opponent.id: opponent.first_name},
    }
    await sent.edit_reply_markup(reply_markup=_keyboard(game_id))


async def rps_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, game_id_str, choice = query.data.split(":")
    game_id = int(game_id_str)

    game = _games.get(game_id)
    if not game:
        await query.answer("This game has ended.", show_alert=True)
        return

    user_id = query.from_user.id
    if user_id not in game["players"]:
        await query.answer("You're not in this game.", show_alert=True)
        return
    if game["players"][user_id] is not None:
        await query.answer("You already chose.", show_alert=True)
        return

    game["players"][user_id] = choice
    await query.answer(f"you chose {choice}")

    if any(v is None for v in game["players"].values()):
        return  # still waiting on the other player

    ids = list(game["players"].keys())
    p1, p2 = ids[0], ids[1]
    c1, c2 = game["players"][p1], game["players"][p2]
    del _games[game_id]

    name1, name2 = html.escape(game["names"][p1]), html.escape(game["names"][p2])
    result_text = (
        f"{header('rock paper scissors')}\n\n"
        f"{name1}: {CHOICES[c1]} {c1}\n"
        f"{name2}: {CHOICES[c2]} {c2}\n\n"
    )

    if c1 == c2:
        result_text += "🤝 it's a draw!"
    elif BEATS[c1] == c2:
        record_win(query.message.chat_id, p1, game["names"][p1], "rps")
        result_text += f"🏆 {name1} wins!"
    else:
        record_win(query.message.chat_id, p2, game["names"][p2], "rps")
        result_text += f"🏆 {name2} wins!"

    await query.edit_message_text(result_text)


def register(app) -> None:
    app.add_handler(CommandHandler("rps", rps))
    app.add_handler(CallbackQueryHandler(rps_callback, pattern="^rps:"))
