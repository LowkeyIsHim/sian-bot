"""
/startwcg - elimination word game:
  1. 30s join phase - type "join" to enter (need 2+ players or it cancels).
  2. Each turn, the current player gets a random letter + minimum word
     length. Answer correctly and in time, or you're eliminated.
  3. Every full round: minimum length goes up, time limit goes down.
  4. Last player standing wins. /endwcg force-ends it (admins/creators).

In-memory only - a live game is lost on restart, an acceptable tradeoff
for a casual group game.
"""

import asyncio
import random
import string

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

import access

JOIN_PHASE_SECONDS = 30
STARTING_MIN_LENGTH = 4
STARTING_TURN_SECONDS = 20
MIN_TURN_SECONDS = 5
LENGTH_INCREASE_PER_ROUND = 1
TIME_DECREASE_PER_ROUND = 3

_games: dict[int, dict] = {}  # chat_id -> game state


def _mention(uid: int, name: str) -> str:
    return f"[{name}](tg://user?id={uid})"


async def startwcg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This only works inside a group.")
        return

    chat_id = update.effective_chat.id
    if chat_id in _games:
        await update.message.reply_text("A word chain game is already running here.")
        return

    _games[chat_id] = {"phase": "joining", "joined": [], "names": {}}
    await update.message.reply_text(
        f"🔤 word chain game starting - type *join* in the next {JOIN_PHASE_SECONDS}s to play!\n"
        f"need at least 2 players.",
        parse_mode="Markdown",
    )
    asyncio.create_task(_end_join_phase(context.bot, chat_id))


async def endwcg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        return
    user_id = update.effective_user.id
    if not (access.is_creator(user_id) or access.is_group_admin(user_id)):
        return  # silent
    chat_id = update.effective_chat.id
    if chat_id in _games:
        del _games[chat_id]
        await update.message.reply_text("word chain game ended.")
    else:
        await update.message.reply_text("no word chain game running here.")


async def _end_join_phase(bot, chat_id: int) -> None:
    await asyncio.sleep(JOIN_PHASE_SECONDS)
    game = _games.get(chat_id)
    if not game or game["phase"] != "joining":
        return  # already cancelled

    if len(game["joined"]) < 2:
        await bot.send_message(chat_id, "not enough players joined - game cancelled.")
        del _games[chat_id]
        return

    game.update({
        "phase": "playing",
        "active": list(game["joined"]),
        "current_index": 0,
        "current_min_length": STARTING_MIN_LENGTH,
        "turn_seconds": STARTING_TURN_SECONDS,
        "used_words": set(),
        "turn_token": 0,
    })

    roster = ", ".join(_mention(uid, game["names"][uid]) for uid in game["active"])
    await bot.send_message(chat_id, f"players: {roster}\nlet's begin!", parse_mode="Markdown")
    await _announce_turn(bot, chat_id, game)


async def _announce_turn(bot, chat_id: int, game: dict) -> None:
    current_uid = game["active"][game["current_index"]]
    next_uid = game["active"][(game["current_index"] + 1) % len(game["active"])]

    letter = random.choice(string.ascii_lowercase)
    game["current_letter"] = letter
    game["turn_token"] += 1
    my_token = game["turn_token"]

    text = (
        f"{_mention(current_uid, game['names'][current_uid])}'s turn\n"
        f"letter: *{letter.upper()}*  |  min length: *{game['current_min_length']}*  |  "
        f"time: {game['turn_seconds']}s\n\n"
        f"next: {_mention(next_uid, game['names'][next_uid])}"
    )
    await bot.send_message(chat_id, text, parse_mode="Markdown")
    asyncio.create_task(_turn_timeout(bot, chat_id, my_token, game["turn_seconds"]))


async def _turn_timeout(bot, chat_id: int, token: int, seconds: int) -> None:
    await asyncio.sleep(seconds)
    game = _games.get(chat_id)
    if not game or game.get("phase") != "playing" or game["turn_token"] != token:
        return  # already answered or game ended - this timer is stale
    current_uid = game["active"][game["current_index"]]
    await bot.send_message(
        chat_id,
        f"⏰ {_mention(current_uid, game['names'][current_uid])} ran out of time - eliminated!",
        parse_mode="Markdown",
    )
    await _eliminate_current(bot, chat_id, game)


async def _eliminate_current(bot, chat_id: int, game: dict) -> None:
    eliminated_uid = game["active"].pop(game["current_index"])

    if len(game["active"]) <= 1:
        if game["active"]:
            winner_uid = game["active"][0]
            await bot.send_message(
                chat_id,
                f"🏆 {_mention(winner_uid, game['names'][winner_uid])} wins the word chain game!",
                parse_mode="Markdown",
            )
        else:
            await bot.send_message(chat_id, "everyone's eliminated - no winner!")
        del _games[chat_id]
        return

    if game["current_index"] >= len(game["active"]):
        game["current_index"] = 0
        game["current_min_length"] += LENGTH_INCREASE_PER_ROUND
        game["turn_seconds"] = max(MIN_TURN_SECONDS, game["turn_seconds"] - TIME_DECREASE_PER_ROUND)

    await _announce_turn(bot, chat_id, game)


async def _advance_after_correct(bot, chat_id: int, game: dict) -> None:
    game["current_index"] = (game["current_index"] + 1) % len(game["active"])
    if game["current_index"] == 0:
        game["current_min_length"] += LENGTH_INCREASE_PER_ROUND
        game["turn_seconds"] = max(MIN_TURN_SECONDS, game["turn_seconds"] - TIME_DECREASE_PER_ROUND)
    await _announce_turn(bot, chat_id, game)


async def _on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None or chat.type not in ("group", "supergroup"):
        return
    game = _games.get(chat.id)
    if not game:
        return

    message = update.effective_message
    if message is None or not message.text:
        return
    user = update.effective_user
    text = message.text.strip()

    if game["phase"] == "joining":
        if text.lower() == "join" and user.id not in game["joined"]:
            game["joined"].append(user.id)
            game["names"][user.id] = user.first_name
            await message.reply_text(f"{user.first_name} joined! ({len(game['joined'])} so far)")
        return

    if game["phase"] != "playing":
        return

    current_uid = game["active"][game["current_index"]]
    if user.id != current_uid:
        return  # not their turn, ignore silently

    word = text.lower()
    if not word.isalpha():
        return  # not a plausible attempt, ignore

    valid = (
        word[0] == game["current_letter"]
        and len(word) >= game["current_min_length"]
        and word not in game["used_words"]
    )

    if not valid:
        await message.reply_text("❌ wrong - eliminated!")
        await _eliminate_current(context.bot, chat.id, game)
        return

    game["used_words"].add(word)
    await message.reply_text(f'✅ "{word}" accepted!')
    await _advance_after_correct(context.bot, chat.id, game)


def register(app) -> None:
    app.add_handler(CommandHandler("startwcg", startwcg))
    app.add_handler(CommandHandler("endwcg", endwcg))
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, _on_message),
        group=5,
    )
