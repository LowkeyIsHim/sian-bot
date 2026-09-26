"""
/leaderboard - overall wins across all games.
/leaderboard <game> - ranking for one game only (tictactoe, rps, trivia,
wcg, hangman). Open to everyone.
"""

import json
import os

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from branding import header, DOT_DIVIDER

GAME_ICONS = {
    "tictactoe": "🎮",
    "rps": "✊",
    "trivia": "❓",
    "wcg": "🔤",
    "hangman": "🎯",
}
GAME_LABELS = {
    "tictactoe": "tic-tac-toe",
    "rps": "rock paper scissors",
    "trivia": "trivia",
    "wcg": "word chain",
    "hangman": "hangman",
}

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PERSISTENT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))
LEADERBOARD_FILE = os.path.join(_PERSISTENT_DIR, "leaderboard.json")


def _load() -> dict:
    if not os.path.exists(LEADERBOARD_FILE):
        return {}
    with open(LEADERBOARD_FILE, "r") as f:
        return json.load(f)


def _save(data: dict) -> None:
    with open(LEADERBOARD_FILE, "w") as f:
        json.dump(data, f, indent=2)


def record_win(chat_id: int, user_id: int, name: str, game: str) -> None:
    data = _load()
    chat_key, user_key = str(chat_id), str(user_id)
    data.setdefault(chat_key, {})
    entry = data[chat_key].setdefault(user_key, {"name": name, "wins": {}})
    entry["name"] = name  # keep display name fresh
    entry["wins"][game] = entry["wins"].get(game, 0) + 1
    _save(data)


def _medal(i: int) -> str:
    medals = ["🥇", "🥈", "🥉"]
    return medals[i] if i < len(medals) else f"{i + 1}."


def _game_list_hint() -> str:
    return "/".join(GAME_ICONS.keys())


async def leaderboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This only works inside a group.")
        return

    data = _load().get(str(update.effective_chat.id), {})
    if not data:
        await update.message.reply_text("No games have been won here yet.")
        return

    game_filter = context.args[0].lower() if context.args else None

    if game_filter and game_filter not in GAME_ICONS:
        await update.message.reply_text(
            f"Unknown game. Try: {_game_list_hint()} - or no argument for the overall board."
        )
        return

    if game_filter:
        icon, label = GAME_ICONS[game_filter], GAME_LABELS[game_filter]
        ranked = sorted(
            ((uid, e) for uid, e in data.items() if e["wins"].get(game_filter, 0) > 0),
            key=lambda kv: kv[1]["wins"][game_filter],
            reverse=True,
        )[:10]

        if not ranked:
            await update.message.reply_text(f"No {label} wins here yet.")
            return

        lines = [header(f"{icon} {label}"), ""]
        for i, (_, entry) in enumerate(ranked):
            wins = entry["wins"][game_filter]
            win_word = "win" if wins == 1 else "wins"
            lines.append(f"{_medal(i)} {entry['name']} — {wins} {win_word}")
        lines.append(f"\n{DOT_DIVIDER}\nother games: /leaderboard <{_game_list_hint()}>")
        await update.message.reply_text("\n".join(lines))
        return

    # overall board - every game combined
    ranked = sorted(data.items(), key=lambda kv: sum(kv[1]["wins"].values()), reverse=True)[:10]
    lines = [header("leaderboard"), ""]
    for i, (_, entry) in enumerate(ranked):
        total = sum(entry["wins"].values())
        win_word = "win" if total == 1 else "wins"
        breakdown = "  ".join(f"{GAME_ICONS.get(g, '•')} {c}" for g, c in entry["wins"].items())
        lines.append(f"{_medal(i)} {entry['name']} — {total} {win_word}")
        lines.append(f"    {breakdown}")
        if i != len(ranked) - 1:
            lines.append(DOT_DIVIDER)
    lines.append(f"\n{DOT_DIVIDER}\nsee one game: /leaderboard <{_game_list_hint()}>")
    await update.message.reply_text("\n".join(lines))


def register(app) -> None:
    app.add_handler(CommandHandler("leaderboard", leaderboard_cmd))
