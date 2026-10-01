"""
/leaderboard - overall wins across all games.
/leaderboard <game> - ranking for one game only (tictactoe, rps, trivia,
wcg, hangman). Open to everyone.
"""

import html
import json
import os

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, CommandHandler

from branding import header, DOT_DIVIDER

# Kept under the same names in case other modules import them.
GAME_ICONS = {
    "tictactoe": "❌",
    "rps": "✊",
    "trivia": "🧠",
    "wcg": "🔤",
    "hangman": "🎯",
}
GAME_LABELS = {
    "tictactoe": "Tic-Tac-Toe",
    "rps": "Rock Paper Scissors",
    "trivia": "Trivia",
    "wcg": "Word Chain",
    "hangman": "Hangman",
}
# Short names for the per-player breakdown line (always text, never icon-only).
GAME_SHORT = {
    "tictactoe": "TicTacToe",
    "rps": "RPS",
    "trivia": "Trivia",
    "wcg": "WordChain",
    "hangman": "Hangman",
}
# Typed variations people will actually use.
ALIASES = {
    "ttt": "tictactoe",
    "tic": "tictactoe",
    "tic-tac-toe": "tictactoe",
    "rockpaperscissors": "rps",
    "wordchain": "wcg",
    "word-chain": "wcg",
    "chain": "wcg",
    "word": "wcg",
    "hang": "hangman",
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


# ---------- formatting helpers ----------
def _medal(i: int) -> str:
    medals = ["🥇", "🥈", "🥉"]
    return medals[i] if i < len(medals) else f"<b>{i + 1}.</b>"


def _wins(n: int) -> str:
    return f"{n} win" if n == 1 else f"{n} wins"


def _title(text: str) -> str:
    # header() output is escaped so it's safe inside HTML parse mode
    return f"<b>{html.escape(header(text))}</b>"


def _games_footer() -> str:
    cmds = "  ".join(f"<code>{g}</code>" for g in GAME_ICONS)
    return f"{html.escape(DOT_DIVIDER)}\nsee one game: /leaderboard + {cmds}"


def _breakdown(wins: dict) -> str:
    parts = sorted(wins.items(), key=lambda kv: kv[1], reverse=True)
    return "  ·  ".join(f"{GAME_SHORT.get(g, g)} {c}" for g, c in parts if c > 0)


def _you_line(ranked_all, user_id: str, value_fn) -> str:
    for i, (uid, entry) in enumerate(ranked_all):
        if uid == user_id:
            return f"\nyou: #{i + 1} · {_wins(value_fn(entry))}"
    return ""


async def leaderboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This only works inside a group.")
        return

    data = _load().get(str(update.effective_chat.id), {})
    if not data:
        await update.message.reply_text("No games have been won here yet.")
        return

    me = str(update.effective_user.id)
    arg = context.args[0].lower() if context.args else None
    game_filter = ALIASES.get(arg, arg) if arg else None

    if game_filter and game_filter not in GAME_ICONS:
        names = "\n".join(
            f"{GAME_ICONS[g]} {GAME_LABELS[g]} → <code>/leaderboard {g}</code>"
            for g in GAME_ICONS
        )
        await update.message.reply_text(
            f"Don't know that game. Pick one:\n\n{names}",
            parse_mode=ParseMode.HTML,
        )
        return

    # ----- single game board -----
    if game_filter:
        ranked_all = sorted(
            ((uid, e) for uid, e in data.items() if e["wins"].get(game_filter, 0) > 0),
            key=lambda kv: kv[1]["wins"][game_filter],
            reverse=True,
        )
        if not ranked_all:
            await update.message.reply_text(f"No {GAME_LABELS[game_filter]} wins here yet.")
            return

        icon, label = GAME_ICONS[game_filter], GAME_LABELS[game_filter]
        lines = [f"{icon} {_title(label + ' leaderboard')}", html.escape(DOT_DIVIDER), ""]
        for i, (_, entry) in enumerate(ranked_all[:10]):
            wins = entry["wins"][game_filter]
            lines.append(f"{_medal(i)} <b>{html.escape(entry['name'])}</b> · {_wins(wins)}")
        lines.append(_you_line(ranked_all, me, lambda e: e["wins"][game_filter]))
        lines.append(f"\n{_games_footer()}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return

    # ----- overall board -----
    ranked_all = sorted(
        ((uid, e) for uid, e in data.items() if sum(e["wins"].values()) > 0),
        key=lambda kv: sum(kv[1]["wins"].values()),
        reverse=True,
    )
    lines = [f"🏆 {_title('leaderboard')}", html.escape(DOT_DIVIDER), ""]
    for i, (_, entry) in enumerate(ranked_all[:10]):
        total = sum(entry["wins"].values())
        lines.append(f"{_medal(i)} <b>{html.escape(entry['name'])}</b> · {_wins(total)}")
        lines.append(f"      <i>{html.escape(_breakdown(entry['wins']))}</i>")
        lines.append("")
    lines.append(_you_line(ranked_all, me, lambda e: sum(e["wins"].values())))
    lines.append(f"\n{_games_footer()}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


def register(app) -> None:
    app.add_handler(CommandHandler("leaderboard", leaderboard_cmd))
