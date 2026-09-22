"""
/leaderboard - tracks wins across the group's games (tic-tac-toe, word
chain). Open to everyone.
"""

import json
import os

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from branding import header

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


async def leaderboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This only works inside a group.")
        return

    data = _load().get(str(update.effective_chat.id), {})
    if not data:
        await update.message.reply_text("No games have been won here yet.")
        return

    ranked = sorted(data.items(), key=lambda kv: sum(kv[1]["wins"].values()), reverse=True)[:10]

    medals = ["🥇", "🥈", "🥉"]
    lines = [header("leaderboard"), ""]
    for i, (_, entry) in enumerate(ranked):
        rank_icon = medals[i] if i < len(medals) else f"{i + 1}."
        total = sum(entry["wins"].values())
        breakdown = ", ".join(f"{g}: {c}" for g, c in entry["wins"].items())
        lines.append(f"{rank_icon} {entry['name']} - {total} win(s)  ({breakdown})")

    await update.message.reply_text("\n".join(lines))


def register(app) -> None:
    app.add_handler(CommandHandler("leaderboard", leaderboard_cmd))
