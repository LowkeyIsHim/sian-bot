"""commands/group/music.py

/music <song or "artist - song">
    Replies with the album art as a banner, the song info, and a 30-second
    audio preview, plus a button to open the full song on Apple Music.
    "Next result" cycles through the other matches (wrong version? cover?).

Access control (admins/creators always allowed):
    /musicaccess                       show the current mode
    /musicaccess everyone|selected|off
        everyone  - every member can request
        selected  - only members you allowed (default)
        off       - members can't request at all
    /allowmusic     (reply to a member, or pass their numeric ID)
    /disallowmusic  (reply to a member, or pass their numeric ID)
    /musicusers     list the members who are allowed

Why previews and not full songs: the bot uses Apple's free iTunes Search
API, which provides 30-second previews and links. Ripping full tracks from
other sites is copyright infringement and breaks constantly, so the button
sends people to the real song instead.

Handler group used: 9 (the "Next result" button).
"""

import asyncio
import html
import json
import logging
import os
import time
from io import BytesIO

import requests
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

import access

logger = logging.getLogger(__name__)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))  # .../bot_src/commands/group
_PERSISTENT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))
SETTINGS_FILE = os.path.join(_PERSISTENT_DIR, "music_settings.json")

SEARCH_URL = "https://itunes.apple.com/search"
# Stores tried in order until one returns results (catalogues differ a bit).
COUNTRIES = [c.strip().upper() for c in os.environ.get("MUSIC_COUNTRIES", "US,NG,GB").split(",")]
RESULT_LIMIT = 5
COOLDOWN_SECONDS = 20       # per member, admins/creators are exempt
STATE_CAP = 200             # remembered "Next result" sessions

MODES = ("everyone", "selected", "off")
DEFAULT_CONF = {"mode": "selected", "allowed": {}}

_last_request: dict = {}


# ---------- storage ----------
def _load() -> dict:
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}


def _save(data: dict) -> None:
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, SETTINGS_FILE)


def _conf(data: dict, chat_id) -> dict:
    conf = data.setdefault(str(chat_id), {"mode": DEFAULT_CONF["mode"], "allowed": {}})
    conf.setdefault("mode", DEFAULT_CONF["mode"])
    conf.setdefault("allowed", {})
    return conf


# ---------- permissions ----------
def _privileged(user_id: int) -> bool:
    return access.is_creator(user_id) or access.is_group_admin(user_id)


def _check_access(chat_id: int, user_id: int):
    """Returns (allowed, message_if_not)."""
    if _privileged(user_id):
        return True, ""
    conf = _conf(_load(), chat_id)
    if conf["mode"] == "everyone":
        return True, ""
    if conf["mode"] == "off":
        return False, "Music requests are switched off in this group."
    if str(user_id) in conf["allowed"]:
        return True, ""
    return False, "You're not allowed to request music here. Ask an admin to allow you."


# ---------- iTunes search ----------
def _parse(t: dict) -> dict | None:
    if not t.get("trackName") or not t.get("artistName"):
        return None
    millis = t.get("trackTimeMillis") or 0
    secs = millis // 1000
    art = (t.get("artworkUrl100") or "").replace("100x100", "600x600")
    return {
        "title": t["trackName"],
        "artist": t["artistName"],
        "album": t.get("collectionName", ""),
        "year": (t.get("releaseDate") or "")[:4],
        "genre": t.get("primaryGenreName", ""),
        "length": f"{secs // 60}:{secs % 60:02d}" if secs else "",
        "explicit": t.get("trackExplicitness") == "explicit",
        "art": art,
        "preview": t.get("previewUrl", ""),
        "link": t.get("trackViewUrl", ""),
    }


def _search_all(query: str) -> list[dict]:
    for country in COUNTRIES:
        try:
            r = requests.get(
                SEARCH_URL,
                params={
                    "term": query,
                    "media": "music",
                    "entity": "song",
                    "limit": RESULT_LIMIT,
                    "country": country,
                },
                timeout=10,
            )
            r.raise_for_status()
            results = [p for p in map(_parse, r.json().get("results", [])) if p]
            if results:
                return results
        except Exception as e:
            logger.warning(f"iTunes search failed ({country}): {e}")
    return []


def _fetch_bytes(url: str, name: str):
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        buf = BytesIO(r.content)
        buf.name = name
        return buf
    except Exception as e:
        logger.warning(f"Could not download {url}: {e}")
        return None


# ---------- message building ----------
def _caption(t: dict, idx: int, total: int) -> str:
    lines = [f"🎵 <b>{html.escape(t['title'])}</b>"]
    lines.append(f"by {html.escape(t['artist'])}")
    lines.append("")
    if t["album"]:
        lines.append(f"Album: {html.escape(t['album'])}")
    if t["year"]:
        lines.append(f"Released: {t['year']}")
    if t["genre"]:
        lines.append(f"Genre: {html.escape(t['genre'])}")
    if t["length"]:
        lines.append(f"Length: {t['length']}")
    if t["explicit"]:
        lines.append("Explicit")
    lines.append("")
    tail = "30-second preview" if t["preview"] else "No preview available"
    lines.append(f"{tail} · result {idx + 1} of {total}")
    return "\n".join(lines)


def _keyboard(t: dict, total: int):
    row = []
    if t["link"]:
        row.append(InlineKeyboardButton("Listen to full song", url=t["link"]))
    if total > 1:
        row.append(InlineKeyboardButton("Not this one? Next", callback_data="music:next"))
    return InlineKeyboardMarkup([row]) if row else None


async def _send_preview(reply_to, t: dict):
    """Sends the 30s audio under the banner. Returns its message id or None."""
    if not t["preview"]:
        return None
    data = await asyncio.to_thread(_fetch_bytes, t["preview"], "preview.m4a")
    if not data:
        return None
    try:
        msg = await reply_to.reply_audio(
            audio=data,
            title=t["title"][:64],
            performer=t["artist"][:64],
            duration=30,
        )
        return msg.message_id
    except TelegramError as e:
        logger.warning(f"Could not send preview: {e}")
        return None


async def _send_track(m, context, results: list[dict], idx: int, requester_id: int) -> None:
    t = results[idx]
    caption, kb = _caption(t, idx, len(results)), _keyboard(t, len(results))

    banner = None
    if t["art"]:
        try:
            banner = await m.reply_photo(
                t["art"], caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb
            )
        except TelegramError:
            data = await asyncio.to_thread(_fetch_bytes, t["art"], "cover.jpg")
            if data:
                try:
                    banner = await m.reply_photo(
                        data, caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb
                    )
                except TelegramError:
                    banner = None
    if banner is None:  # no artwork - plain info card
        banner = await m.reply_text(caption, parse_mode=ParseMode.HTML, reply_markup=kb)

    audio_id = await _send_preview(banner, t)

    state = context.bot_data.setdefault("music_state", {})
    state[f"{banner.chat_id}:{banner.message_id}"] = {
        "results": results,
        "idx": idx,
        "user": requester_id,
        "audio_id": audio_id,
    }
    while len(state) > STATE_CAP:  # forget the oldest sessions
        state.pop(next(iter(state)))


# ---------- /music ----------
async def music_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    m, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await m.reply_text("This only works inside a group.")
        return

    ok, reason = _check_access(chat.id, user.id)
    if not ok:
        await m.reply_text(reason)
        return

    query = " ".join(context.args).strip()[:100]
    if not query:
        await m.reply_text("Usage: /music <song name>  or  /music <artist - song>")
        return

    if not _privileged(user.id):
        wait = COOLDOWN_SECONDS - (time.time() - _last_request.get((chat.id, user.id), 0))
        if wait > 0:
            await m.reply_text(f"Easy, try again in {int(wait) + 1}s.")
            return
    _last_request[(chat.id, user.id)] = time.time()

    results = await asyncio.to_thread(_search_all, query)
    if not results:
        await m.reply_text("Couldn't find that song. Try adding the artist's name.")
        return
    await _send_track(m, context, results, 0, user.id)


async def music_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    msg = q.message
    key = f"{msg.chat_id}:{msg.message_id}"
    state = context.bot_data.get("music_state", {}).get(key)
    if not state:
        await q.answer("This request has expired. Send /music again.", show_alert=True)
        return
    if q.from_user.id != state["user"] and not _privileged(q.from_user.id):
        await q.answer("Only the person who requested this can switch results.", show_alert=True)
        return

    await q.answer()
    idx = (state["idx"] + 1) % len(state["results"])
    t = state["results"][idx]
    caption, kb = _caption(t, idx, len(state["results"])), _keyboard(t, len(state["results"]))

    try:
        try:
            await q.edit_message_media(
                InputMediaPhoto(t["art"], caption=caption, parse_mode=ParseMode.HTML),
                reply_markup=kb,
            )
        except TelegramError:
            data = await asyncio.to_thread(_fetch_bytes, t["art"], "cover.jpg")
            if not data:
                raise
            await q.edit_message_media(
                InputMediaPhoto(data, caption=caption, parse_mode=ParseMode.HTML),
                reply_markup=kb,
            )
    except TelegramError as e:
        logger.warning(f"Could not switch music result: {e}")
        return

    if state.get("audio_id"):
        try:
            await context.bot.delete_message(msg.chat_id, state["audio_id"])
        except TelegramError:
            pass
    state["idx"] = idx
    state["audio_id"] = await _send_preview(msg, t)


# ---------- admin controls ----------
def _target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.effective_message
    if m.reply_to_message and m.reply_to_message.from_user:
        u = m.reply_to_message.from_user
        return str(u.id), u.full_name
    if context.args and context.args[0].isdigit():
        return context.args[0], f"member {context.args[0]}"
    return None, None


async def musicaccess_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    m = update.effective_message
    if not _privileged(update.effective_user.id):
        return  # silent
    data = _load()
    conf = _conf(data, update.effective_chat.id)

    if not context.args:
        await m.reply_text(
            f"Music access: {conf['mode']}\n"
            f"Allowed members: {len(conf['allowed'])}\n\n"
            "/musicaccess everyone - all members can request\n"
            "/musicaccess selected - only members you allow\n"
            "/musicaccess off - members can't request\n"
            "/allowmusic or /disallowmusic - reply to a member"
        )
        return

    mode = context.args[0].lower()
    if mode not in MODES:
        await m.reply_text("Choose: everyone, selected, or off.")
        return
    conf["mode"] = mode
    _save(data)
    await m.reply_text(f"Music access set to: {mode}")


async def allowmusic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    m = update.effective_message
    if not _privileged(update.effective_user.id):
        return
    uid, name = _target(update, context)
    if not uid:
        await m.reply_text("Reply to a member's message with /allowmusic (or add their ID).")
        return
    data = _load()
    conf = _conf(data, update.effective_chat.id)
    conf["allowed"][uid] = name
    _save(data)
    extra = "" if conf["mode"] == "selected" else f"\n(access mode is '{conf['mode']}', set /musicaccess selected to use the list)"
    await m.reply_text(f"{html.escape(name)} can now request music.{extra}", parse_mode=ParseMode.HTML)


async def disallowmusic_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    m = update.effective_message
    if not _privileged(update.effective_user.id):
        return
    uid, name = _target(update, context)
    if not uid:
        await m.reply_text("Reply to a member's message with /disallowmusic (or add their ID).")
        return
    data = _load()
    conf = _conf(data, update.effective_chat.id)
    if conf["allowed"].pop(uid, None) is None:
        await m.reply_text(f"{html.escape(name)} wasn't on the list.", parse_mode=ParseMode.HTML)
        return
    _save(data)
    await m.reply_text(f"{html.escape(name)} can no longer request music.", parse_mode=ParseMode.HTML)


async def musicusers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    m = update.effective_message
    if not _privileged(update.effective_user.id):
        return
    conf = _conf(_load(), update.effective_chat.id)
    if not conf["allowed"]:
        await m.reply_text(f"Nobody is on the list yet. Access mode: {conf['mode']}")
        return
    names = "\n".join(f"• {html.escape(n)}" for n in conf["allowed"].values())
    await m.reply_text(
        f"<b>Allowed to request music</b> (mode: {conf['mode']})\n\n{names}",
        parse_mode=ParseMode.HTML,
    )


# ---------- wiring ----------
def register(app) -> None:
    g = filters.ChatType.GROUPS
    app.add_handler(CommandHandler("music", music_cmd, filters=g))
    app.add_handler(CommandHandler("musicaccess", musicaccess_cmd, filters=g))
    app.add_handler(CommandHandler("allowmusic", allowmusic_cmd, filters=g))
    app.add_handler(CommandHandler("disallowmusic", disallowmusic_cmd, filters=g))
    app.add_handler(CommandHandler("musicusers", musicusers_cmd, filters=g))
    app.add_handler(CallbackQueryHandler(music_callback, pattern="^music:"), group=9)
