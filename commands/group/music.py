"""commands/group/music.py

ONE command: /music

    /music <song or "artist - song">
        Replies with the album art as a banner, the song info, and a button
        that opens the full song on Spotify. "Not this one? Next" cycles
        through the other matches (wrong version? cover?).

    Admins and creators only (always allowed to request too):
    /music access                      show the current mode
    /music access everyone|selected|off
        everyone  - every member can request
        selected  - only members you allowed (default)
        off       - members can't request at all
    /music allow     (reply to a member, or add their numeric ID)
    /music disallow  (reply to a member, or add their numeric ID)
    /music users     list the members who are allowed

Data comes from the Spotify Web API (search only, client-credentials flow).
Needs SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in secrets.env.

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

TOKEN_URL = "https://accounts.spotify.com/api/token"
SEARCH_URL = "https://api.spotify.com/v1/search"
RESULT_LIMIT = 5
COOLDOWN_SECONDS = 20       # per member, admins/creators are exempt
STATE_CAP = 200             # remembered "Next result" sessions

MODES = ("everyone", "selected", "off")
ADMIN_SUBS = ("allow", "disallow", "access", "users")

_last_request: dict = {}
_token = {"value": "", "expires": 0.0}


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
    conf = data.setdefault(str(chat_id), {"mode": "selected", "allowed": {}})
    conf.setdefault("mode", "selected")
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


# ---------- Spotify ----------
def _get_token():
    """Cached client-credentials token. None if the keys aren't set."""
    cid = os.environ.get("SPOTIFY_CLIENT_ID")
    secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not cid or not secret:
        return None
    if _token["value"] and time.time() < _token["expires"] - 60:
        return _token["value"]
    r = requests.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(cid, secret),
        timeout=10,
    )
    r.raise_for_status()
    d = r.json()
    _token["value"] = d["access_token"]
    _token["expires"] = time.time() + d.get("expires_in", 3600)
    return _token["value"]


def _parse(item: dict) -> dict | None:
    if not item or not item.get("name"):
        return None
    artists = ", ".join(a["name"] for a in item.get("artists", []) if a.get("name"))
    if not artists:
        return None
    album = item.get("album") or {}
    images = album.get("images") or []  # Spotify lists the largest first
    secs = (item.get("duration_ms") or 0) // 1000
    return {
        "title": item["name"],
        "artist": artists,
        "album": album.get("name", ""),
        "year": (album.get("release_date") or "")[:4],
        "length": f"{secs // 60}:{secs % 60:02d}" if secs else "",
        "explicit": bool(item.get("explicit")),
        "art": images[0]["url"] if images else "",
        "link": (item.get("external_urls") or {}).get("spotify", ""),
    }


def _search_all(query: str):
    """Returns (results, error). error is None, 'nocreds' or 'unavailable'."""
    try:
        token = _get_token()
    except Exception as e:
        logger.warning(f"Spotify token request failed: {e}")
        return [], "unavailable"
    if not token:
        return [], "nocreds"
    try:
        r = requests.get(
            SEARCH_URL,
            params={"q": query, "type": "track", "limit": RESULT_LIMIT},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if r.status_code == 401:
            _token["value"] = ""  # expired early - next request fetches a new one
        if r.status_code >= 400:
            logger.warning(f"Spotify {r.status_code}: {r.text[:300]}")
        r.raise_for_status()
        items = r.json().get("tracks", {}).get("items", [])
        return [p for p in map(_parse, items) if p], None
    except Exception as e:
        logger.warning(f"Spotify search failed: {e}")
        return [], "unavailable"


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
    lines = [f"🎵 <b>{html.escape(t['title'])}</b>", f"by {html.escape(t['artist'])}", ""]
    if t["album"]:
        lines.append(f"Album: {html.escape(t['album'])}")
    if t["year"]:
        lines.append(f"Released: {t['year']}")
    if t["length"]:
        lines.append(f"Length: {t['length']}")
    if t["explicit"]:
        lines.append("Explicit")
    lines.append("")
    lines.append(f"Result {idx + 1} of {total}")
    return "\n".join(lines)


def _keyboard(t: dict, total: int):
    row = []
    if t["link"]:
        row.append(InlineKeyboardButton("Listen on Spotify", url=t["link"]))
    if total > 1:
        row.append(InlineKeyboardButton("Not this one? Next", callback_data="music:next"))
    return InlineKeyboardMarkup([row]) if row else None


async def _send_track(m, context, results: list[dict], requester_id: int) -> None:
    t = results[0]
    caption, kb = _caption(t, 0, len(results)), _keyboard(t, len(results))

    card = None
    if t["art"]:
        try:
            card = await m.reply_photo(
                t["art"], caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb
            )
        except TelegramError:
            data = await asyncio.to_thread(_fetch_bytes, t["art"], "cover.jpg")
            if data:
                try:
                    card = await m.reply_photo(
                        data, caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb
                    )
                except TelegramError:
                    card = None
    if card is None:  # no artwork - plain info card
        card = await m.reply_text(caption, parse_mode=ParseMode.HTML, reply_markup=kb)

    state = context.bot_data.setdefault("music_state", {})
    state[f"{card.chat_id}:{card.message_id}"] = {
        "results": results,
        "idx": 0,
        "user": requester_id,
    }
    while len(state) > STATE_CAP:  # forget the oldest sessions
        state.pop(next(iter(state)))


# ---------- /music ----------
async def music_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    m, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await m.reply_text("This only works inside a group.")
        return

    # admin subcommands: /music allow | disallow | access | users
    if context.args and context.args[0].lower() in ADMIN_SUBS and _privileged(user.id):
        await _admin(update, context, context.args[0].lower(), context.args[1:])
        return

    ok, reason = _check_access(chat.id, user.id)
    if not ok:
        await m.reply_text(reason)
        return

    query = " ".join(context.args).strip()[:100]
    if not query:
        usage = "Usage: /music <song name>  or  /music <artist - song>"
        if _privileged(user.id):
            usage += "\n\nAdmin: /music allow | disallow | access | users"
        await m.reply_text(usage)
        return

    if not _privileged(user.id):
        wait = COOLDOWN_SECONDS - (time.time() - _last_request.get((chat.id, user.id), 0))
        if wait > 0:
            await m.reply_text(f"Easy, try again in {int(wait) + 1}s.")
            return
    _last_request[(chat.id, user.id)] = time.time()

    results, err = await asyncio.to_thread(_search_all, query)
    if err == "nocreds":
        await m.reply_text("Music isn't set up yet. The creator needs to add the Spotify keys.")
        return
    if err:
        await m.reply_text("Couldn't reach Spotify right now. Try again in a minute.")
        return
    if not results:
        await m.reply_text("Couldn't find that song. Try adding the artist's name.")
        return
    await _send_track(m, context, results, user.id)


async def music_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    msg = q.message
    state = context.bot_data.get("music_state", {}).get(f"{msg.chat_id}:{msg.message_id}")
    if not state:
        await q.answer("This request has expired. Send /music again.", show_alert=True)
        return
    if q.from_user.id != state["user"] and not _privileged(q.from_user.id):
        await q.answer("Only the person who requested this can switch results.", show_alert=True)
        return

    await q.answer()
    total = len(state["results"])
    idx = (state["idx"] + 1) % total
    t = state["results"][idx]
    caption, kb = _caption(t, idx, total), _keyboard(t, total)

    try:
        if not t["art"]:
            await q.edit_message_caption(caption, parse_mode=ParseMode.HTML, reply_markup=kb)
        else:
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
        state["idx"] = idx
    except TelegramError as e:
        logger.warning(f"Could not switch music result: {e}")


# ---------- admin subcommands ----------
def _target(m, rest: list[str]):
    if m.reply_to_message and m.reply_to_message.from_user:
        u = m.reply_to_message.from_user
        return str(u.id), u.full_name
    if rest and rest[0].isdigit():
        return rest[0], f"member {rest[0]}"
    return None, None


async def _admin(update: Update, context: ContextTypes.DEFAULT_TYPE, sub: str, rest: list[str]) -> None:
    m = update.effective_message
    data = _load()
    conf = _conf(data, update.effective_chat.id)

    if sub == "access":
        if not rest:
            await m.reply_text(
                f"Music access: {conf['mode']}\n"
                f"Allowed members: {len(conf['allowed'])}\n\n"
                "/music access everyone - all members can request\n"
                "/music access selected - only members you allow\n"
                "/music access off - members can't request"
            )
            return
        mode = rest[0].lower()
        if mode not in MODES:
            await m.reply_text("Choose: everyone, selected, or off.")
            return
        conf["mode"] = mode
        _save(data)
        await m.reply_text(f"Music access set to: {mode}")
        return

    if sub == "users":
        if not conf["allowed"]:
            await m.reply_text(f"Nobody is on the list yet. Access mode: {conf['mode']}")
            return
        names = "\n".join(f"• {html.escape(n)}" for n in conf["allowed"].values())
        await m.reply_text(
            f"<b>Allowed to request music</b> (mode: {conf['mode']})\n\n{names}",
            parse_mode=ParseMode.HTML,
        )
        return

    # allow / disallow
    uid, name = _target(m, rest)
    if not uid:
        await m.reply_text(f"Reply to a member's message with /music {sub} (or add their ID).")
        return
    safe = html.escape(name)

    if sub == "allow":
        conf["allowed"][uid] = name
        _save(data)
        extra = (
            ""
            if conf["mode"] == "selected"
            else f"\n(access is '{conf['mode']}' - use /music access selected to apply the list)"
        )
        await m.reply_text(f"{safe} can now request music.{extra}", parse_mode=ParseMode.HTML)
    else:
        if conf["allowed"].pop(uid, None) is None:
            await m.reply_text(f"{safe} wasn't on the list.", parse_mode=ParseMode.HTML)
            return
        _save(data)
        await m.reply_text(f"{safe} can no longer request music.", parse_mode=ParseMode.HTML)


# ---------- wiring ----------
def register(app) -> None:
    app.add_handler(CommandHandler("music", music_cmd, filters=filters.ChatType.GROUPS))
    app.add_handler(CallbackQueryHandler(music_callback, pattern="^music:"), group=9)
