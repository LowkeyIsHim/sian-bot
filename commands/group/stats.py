"""commands/group/stats.py

Group engagement stats: messages, media types, join date, members added,
and game wins (read from leaderboard.json). Commands: /stats (reply to see someone else's),
/topchatters.

Drop into commands/group/, add "commands.group.stats" to COMMAND_MODULES.
Handler groups used: 6 (tracker), 7 (join watcher).
"""
import html
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from telegram import Update
from telegram.constants import ParseMode
from commands.group.invites import get_count as invite_count
from commands.group.leaderboard import GAME_LABELS, _load as _load_leaderboard
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# One folder ABOVE bot_src so auto-updates never wipe it.
# parents[0]=group, [1]=commands, [2]=bot_src, [3]=above. Swap in your
# own data-dir helper if you have one.
STATS_FILE = Path(__file__).resolve().parents[3] / "stats.json"

MEDIA_LABELS = [
    ("stickers", "🎭 Stickers"),
    ("photos", "🖼 Photos"),
    ("videos", "🎬 Videos"),
    ("gifs", "👾 GIFs"),
    ("voice", "🎙 Voice notes"),
    ("audio", "🎵 Audio"),
    ("video_notes", "⏺ Video notes"),
    ("documents", "📎 Files"),
]

_data = None
_events = 0


# ---------- storage ----------
def _load() -> dict:
    global _data
    if _data is None:
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                _data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            _data = {}
    return _data


def flush() -> None:
    if _data is None:
        return
    tmp = f"{STATS_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_data, f)
    os.replace(tmp, STATS_FILE)  # atomic, no half-written file on crash


def _dirty() -> None:
    global _events
    _events += 1
    if _events >= 20:
        _events = 0
        flush()


def _user(chat_id, user) -> dict:
    chat = _load().setdefault(str(chat_id), {})
    rec = chat.get(str(user.id))
    if rec is None:
        rec = {
            "name": user.full_name,
            "first_seen": time.time(),
            "joined": None,
            "messages": 0,
            "replies": 0,
            "added": 0,
        }
        for key, _ in MEDIA_LABELS:
            rec[key] = 0
        chat[str(user.id)] = rec
    rec["name"] = user.full_name  # keep display name fresh
    return rec


# ---------- passive trackers ----------
def _kind(m):
    if m.sticker:
        return "stickers"
    if m.voice:
        return "voice"
    if m.audio:
        return "audio"
    if m.photo:
        return "photos"
    if m.video:
        return "videos"
    if m.animation:  # must come before document (GIFs carry both)
        return "gifs"
    if m.video_note:
        return "video_notes"
    if m.document:
        return "documents"
    return None


async def track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    u = update.effective_user
    if not m or not u or u.is_bot:
        return
    rec = _user(m.chat_id, u)
    rec["messages"] += 1
    if m.reply_to_message:
        rec["replies"] += 1
    kind = _kind(m)
    if kind:
        rec[kind] += 1
    _dirty()


async def on_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    if not m or not m.new_chat_members:
        return
    adder = m.from_user
    now = time.time()
    for member in m.new_chat_members:
        if member.is_bot:
            continue
        rec = _user(m.chat_id, member)
        if not rec["joined"]:
            rec["joined"] = now
        if adder and not adder.is_bot and adder.id != member.id:
            _user(m.chat_id, adder)["added"] += 1
    _dirty()


async def _flush_job(context: ContextTypes.DEFAULT_TYPE):
    flush()


# ---------- commands ----------
def _date(ts) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d %b %Y")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    target = (
        m.reply_to_message.from_user
        if m.reply_to_message and m.reply_to_message.from_user
        else update.effective_user
    )
    if target.is_bot:
        await m.reply_text("Bots don't get stats 🤖")
        return

    chat = _load().get(str(m.chat_id), {})
    rec = chat.get(str(target.id))
    if not rec:
        await m.reply_text("No stats yet for that person, they haven't said anything since tracking started 🌚")
        return

    rank = 1 + sum(1 for r in chat.values() if r["messages"] > rec["messages"])
    name = html.escape(rec["name"])
    if rec["joined"]:
        joined = f"Joined: {_date(rec['joined'])}"
    else:
        joined = f"Tracked since: {_date(rec['first_seen'])}"

    lines = [
        f"📊 <b>{name}</b>",
        joined,
        "",
        f"💬 Messages: <b>{rec['messages']}</b> (#{rank} in the group)",
        f"↩️ Replies: {rec['replies']}",
    ]
    for key, label in MEDIA_LABELS:
        if rec.get(key):
            lines.append(f"{label}: {rec[key]}")
    via_link = invite_count(m.chat_id, target.id)
    lines.append(f"👥 Members added: {rec['added'] + via_link}")

    wins = (
        _load_leaderboard().get(str(m.chat_id), {}).get(str(target.id), {}).get("wins", {})
    )
    wins = {g: c for g, c in wins.items() if c > 0}
    if wins:
        lines.append("")
        lines.append(f"🏆 <b>Game wins</b> ({sum(wins.values())} total)")
        for game, count in sorted(wins.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"• {html.escape(GAME_LABELS.get(game, game))}: {count}")

    await m.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    chat = _load().get(str(m.chat_id), {})
    if not chat:
        await m.reply_text("Nothing tracked yet 🌚")
        return
    top = sorted(chat.values(), key=lambda r: r["messages"], reverse=True)[:10]
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>Top chatters</b>", ""]
    for i, r in enumerate(top):
        tag = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{tag} {html.escape(r['name'])}: {r['messages']}")
    await m.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ---------- wiring ----------
def register(app):
    app.add_handler(
        CommandHandler("stats", stats_cmd, filters=filters.ChatType.GROUPS)
    )
    app.add_handler(
        CommandHandler("topchatters", top_cmd, filters=filters.ChatType.GROUPS)
    )
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.StatusUpdate.NEW_CHAT_MEMBERS,
            on_join,
        ),
        group=7,
    )
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & ~filters.StatusUpdate.ALL, track
        ),
        group=6,
    )
    if app.job_queue:
        app.job_queue.run_repeating(_flush_job, interval=60, first=60)
