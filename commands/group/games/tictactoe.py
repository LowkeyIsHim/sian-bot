"""
/tictactoe - reply to someone's message to challenge them. Turn-based,
buttons edit the same message in place. In-memory only - a live game is
lost on restart, which is an acceptable tradeoff for a quick group game.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from branding import DOT_DIVIDER
from commands.group.leaderboard import record_win

EMPTY, X, O = " ", "❌", "⭕"

_games: dict[int, dict] = {}  # message_id -> game state

WIN_LINES = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),
    (0, 3, 6), (1, 4, 7), (2, 5, 8),
    (0, 4, 8), (2, 4, 6),
]


def _render_board(board: list[str], game_id: int) -> InlineKeyboardMarkup:
    rows = []
    for r in range(3):
        row = []
        for c in range(3):
            i = r * 3 + c
            label = board[i] if board[i] != EMPTY else "・"
            row.append(InlineKeyboardButton(label, callback_data=f"ttt:{game_id}:{i}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _check_winner(board: list[str]):
    for a, b, c in WIN_LINES:
        if board[a] != EMPTY and board[a] == board[b] == board[c]:
            return board[a]
    if EMPTY not in board:
        return "draw"
    return None


def _status_text(game: dict, whose_turn_id: int) -> str:
    p1_id, p2_id = game["order"]
    return (
        f"🎮 *tic-tac-toe*\n{DOT_DIVIDER}\n\n"
        f"{X} {game['names'][p1_id]}  vs  {O} {game['names'][p2_id]}\n"
        f"{game['names'][whose_turn_id]}'s turn ({game['players'][whose_turn_id]})"
    )


async def tictactoe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    board = [EMPTY] * 9
    game = {
        "board": board,
        "players": {challenger.id: X, opponent.id: O},
        "names": {challenger.id: challenger.first_name, opponent.id: opponent.first_name},
        "order": [challenger.id, opponent.id],
        "turn": challenger.id,
    }

    sent = await update.message.reply_text(_status_text(game, challenger.id), parse_mode="Markdown")
    _games[sent.message_id] = game
    await sent.edit_reply_markup(reply_markup=_render_board(board, sent.message_id))


async def tictactoe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, game_id_str, cell_str = query.data.split(":")
    game_id, cell = int(game_id_str), int(cell_str)

    game = _games.get(game_id)
    if not game:
        await query.answer("This game has ended.", show_alert=True)
        return

    user_id = query.from_user.id
    if user_id not in game["players"]:
        await query.answer("You're not in this game.", show_alert=True)
        return
    if game["turn"] != user_id:
        await query.answer("Not your turn.", show_alert=True)
        return
    if game["board"][cell] != EMPTY:
        await query.answer("That spot's taken.", show_alert=True)
        return

    await query.answer()
    game["board"][cell] = game["players"][user_id]

    winner = _check_winner(game["board"])
    if winner == "draw":
        del _games[game_id]
        await query.edit_message_text(f"🎮 *tic-tac-toe*\n{DOT_DIVIDER}\n\n🤝 it's a draw!", parse_mode="Markdown")
        return
    if winner:
        del _games[game_id]
        record_win(query.message.chat_id, user_id, game["names"][user_id], "tictactoe")
        await query.edit_message_text(
            f"🎮 *tic-tac-toe*\n{DOT_DIVIDER}\n\n🏆 {game['names'][user_id]} wins! ({winner})",
            parse_mode="Markdown",
        )
        return

    other_id = next(pid for pid in game["players"] if pid != user_id)
    game["turn"] = other_id
    await query.edit_message_text(
        _status_text(game, other_id), parse_mode="Markdown", reply_markup=_render_board(game["board"], game_id)
    )


def register(app) -> None:
    app.add_handler(CommandHandler("tictactoe", tictactoe))
    app.add_handler(CallbackQueryHandler(tictactoe_callback, pattern="^ttt:"))
