"""commands/group/music.py

ONE command: /music

    /music <song or "artist - song">
        Replies with a designed "now playing" card (blurred album-art
        background, cover, title, artist, album/year/genre, player bar).
        Song info comes from Deezer's free public API.

        FULL SONG: if the same song exists on Audius (an open platform for
        independent artists) the bot looks it up. If the artist allows
        downloads, the full track is sent right under the card. If not, a
        "Listen on Audius" button is shown instead. Mainstream releases
        aren't on Audius, so those get an "Open in Spotify" button only:
        copyrighted full songs can't be sent by a bot.

    "Not this one? Next" cycles through the other matches.

    Admins and creators only (always allowed to request too):
    /music access                      show the current mode
    /music access everyone|selected|off
        everyone  - every member can request
        selected  - only members you allowed (default)
        off       - members can't request at all
    /music allow     (reply to a member, or add their numeric ID)
    /music disallow  (reply to a member, or add their numeric ID)
    /music users     list the members who are allowed

Optional secrets.env line: AUDIUS_API_KEY (free at audius.co/settings,
gives higher rate limits). Works without it.

Handler group used: 9 (the "Next result" button).
"""

import asyncio
import html
import json
import logging
import os
import re
import time
from io import BytesIO
from urllib.parse import quote

import requests
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

import access

logger = logging.getLogger(__name__)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))  # .../bot_src/commands/group
_PERSISTENT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))
SETTINGS_FILE = os.path.join(_PERSISTENT_DIR, "music_settings.json")

SEARCH_URL = "https://api.deezer.com/search"
ALBUM_URL = "https://api.deezer.com/album"
AUDIUS_SEARCH_URL = "https://api.audius.co/v1/tracks/search"
AUDIUS_STREAM_URL = "https://api.audius.co/v1/tracks/{id}/stream"
AUDIUS_APP_NAME = "GoddessBot"
MAX_AUDIO_BYTES = 45 * 1024 * 1024  # Telegram bots can upload up to 50 MB

RESULT_LIMIT = 5
COOLDOWN_SECONDS = 20       # per member, admins/creators are exempt
STATE_CAP = 200             # remembered "Next result" sessions

MODES = ("everyone", "selected", "off")
ADMIN_SUBS = ("allow", "disallow", "access", "users")

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


# ---------- Deezer: song info ----------
def _parse(item: dict) -> dict | None:
    artist = (item.get("artist") or {}).get("name") if item else None
    if not item or not item.get("title") or not artist:
        return None
    album = item.get("album") or {}
    secs = item.get("duration") or 0
    title = item["title"]
    return {
        "title": title,
        "artist": artist,
        "album": album.get("title", ""),
        "album_id": album.get("id"),
        "year": "",
        "genre": "",
        "length": f"{secs // 60}:{secs % 60:02d}" if secs else "",
        "explicit": bool(item.get("explicit_lyrics")),
        "art": album.get("cover_xl") or album.get("cover_big") or album.get("cover") or "",
        "spotify": "https://open.spotify.com/search/" + quote(f"{title} {artist}"),
        "enriched": False,
        "prepared": False,
        "card": None,
        "audius": None,
    }


def _search_all(query: str):
    """Returns (results, error). error is None or 'unavailable'."""
    try:
        r = requests.get(SEARCH_URL, params={"q": query, "limit": RESULT_LIMIT}, timeout=10)
        r.raise_for_status()
        body = r.json()
        if "error" in body:  # Deezer reports some errors with a 200 status
            logger.warning(f"Deezer error: {body['error']}")
            return [], "unavailable"
        return [p for p in map(_parse, body.get("data", [])) if p], None
    except Exception as e:
        logger.warning(f"Deezer search failed: {e}")
        return [], "unavailable"


def _enrich(t: dict) -> None:
    """Adds release year and genre (one extra lookup, done once per result)."""
    if t["enriched"]:
        return
    t["enriched"] = True
    if not t.get("album_id"):
        return
    try:
        r = requests.get(
            f"{ALBUM_URL}/{t['album_id']}", headers={"Accept-Language": "en"}, timeout=10
        )
        r.raise_for_status()
        album = r.json()
        t["year"] = (album.get("release_date") or "")[:4]
        genres = (album.get("genres") or {}).get("data") or []
        t["genre"] = genres[0].get("name", "") if genres else ""
    except Exception as e:
        logger.info(f"Could not fetch album details: {e}")


# ---------- Audius: full songs where the artist allows it ----------
def _audius_headers() -> dict:
    key = os.environ.get("AUDIUS_API_KEY")
    return {"Authorization": f"Bearer {key}"} if key else {}


def _norm(text: str) -> str:
    text = re.sub(r"[\(\[].*?[\)\]]", "", text or "")  # drop "(feat. X)" / "[Remix]"
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _find_audius(t: dict) -> dict | None:
    """Finds the SAME song on Audius (exact title + matching artist), or None."""
    try:
        r = requests.get(
            AUDIUS_SEARCH_URL,
            params={"query": f"{t['title']} {t['artist']}", "app_name": AUDIUS_APP_NAME, "limit": 10},
            headers=_audius_headers(),
            timeout=10,
        )
        r.raise_for_status()
        want_title = _norm(t["title"])
        want_artist = _norm(t["artist"].split(",")[0])
        for tr in r.json().get("data", []):
            title = _norm(tr.get("title", ""))
            artist = _norm((tr.get("user") or {}).get("name", ""))
            if not title or not artist or not want_artist:
                continue
            if title != want_title or not (want_artist in artist or artist in want_artist):
                continue
            if tr.get("is_streamable") is False:
                continue
            permalink = tr.get("permalink") or ""
            return {
                "id": tr["id"],
                "link": f"https://audius.co{permalink}" if permalink else "",
                # only artists who switched downloads on get their file sent
                "downloadable": bool(tr.get("is_downloadable"))
                or bool((tr.get("download") or {}).get("is_downloadable")),
            }
    except Exception as e:
        logger.info(f"Audius lookup failed: {e}")
    return None


def _download_audio(track_id: str):
    try:
        r = requests.get(
            AUDIUS_STREAM_URL.format(id=track_id),
            params={"app_name": AUDIUS_APP_NAME},
            headers=_audius_headers(),
            stream=True,
            timeout=30,
        )
        r.raise_for_status()
        if int(r.headers.get("Content-Length") or 0) > MAX_AUDIO_BYTES:
            return None
        buf, total = BytesIO(), 0
        for chunk in r.iter_content(65536):
            total += len(chunk)
            if total > MAX_AUDIO_BYTES:
                return None
            buf.write(chunk)
        buf.seek(0)
        buf.name = "song.mp3"
        return buf
    except Exception as e:
        logger.warning(f"Could not download Audius track: {e}")
        return None


# ---------- the card ----------
CARD_W, CARD_H = 1080, 1350


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)  # built into Pillow 10.1+, no font file needed
    except TypeError:
        return ImageFont.load_default()


def _fit(draw, text: str, size: int, max_w: int, min_size: int = 30):
    """Shrinks the font until the text fits, then truncates with an ellipsis."""
    s = size
    while s > min_size:
        f = _font(s)
        if draw.textlength(text, font=f) <= max_w:
            return f, text
        s -= 2
    f = _font(min_size)
    if draw.textlength(text, font=f) <= max_w:
        return f, text
    while text and draw.textlength(text + "…", font=f) > max_w:
        text = text[:-1]
    return f, text.rstrip() + "…"


def _vignette(img: Image.Image, strength: float) -> Image.Image:
    radial = Image.radial_gradient("L").resize(img.size)
    mask = radial.point(lambda p: int(255 * ((p / 255) ** 2.2) * strength))
    return Image.composite(ImageEnhance.Brightness(img).enhance(0.45), img, mask)


def _build_card(cover_bytes: bytes, t: dict) -> bytes:
    W, H = CARD_W, CARD_H
    cover = Image.open(BytesIO(cover_bytes)).convert("RGB")

    # moody blurred background made from the cover itself
    bg = ImageOps.fit(cover, (W, H), Image.LANCZOS).filter(ImageFilter.GaussianBlur(48))
    bg = ImageEnhance.Color(bg).enhance(0.55)
    bg = ImageEnhance.Brightness(bg).enhance(0.40)
    bg = _vignette(bg, 0.7)
    grain = Image.effect_noise((W, H), 20).convert("RGB")
    bg = Image.blend(bg, ImageChops.overlay(bg, grain), 0.18).convert("RGBA")

    # cover with soft shadow and rounded corners
    size, x, y, radius = 780, (W - 780) // 2, 150, 40
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (x, y + 26, x + size, y + size + 26), radius, fill=(0, 0, 0, 190)
    )
    bg = Image.alpha_composite(bg, shadow.filter(ImageFilter.GaussianBlur(38)))
    art = ImageOps.fit(cover, (size, size), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size, size), radius, fill=255)
    bg.paste(art, (x, y), mask)

    d = ImageDraw.Draw(bg)
    cx, max_w = W // 2, W - 180

    # top label
    d.text((cx, 72), "G O D D E S S   ·   M U S I C", font=_font(24), fill=(205, 205, 205, 255), anchor="mm")

    # title / artist / meta
    ty = y + size + 70
    f, text = _fit(d, t["title"], 64, max_w)
    d.text((cx, ty), text, font=f, fill=(255, 255, 255, 255), anchor="mt", stroke_width=1, stroke_fill=(255, 255, 255, 255))
    f, text = _fit(d, t["artist"], 40, max_w, 26)
    d.text((cx, ty + 84), text, font=f, fill=(215, 215, 215, 255), anchor="mt")
    meta = "  ·  ".join(p for p in (t["album"], t["year"], t["genre"]) if p)
    if meta:
        f, text = _fit(d, meta, 28, max_w, 20)
        d.text((cx, ty + 142), text, font=f, fill=(150, 150, 150, 255), anchor="mt")

    # player bar
    by, bx0, bx1 = ty + 235, 150, W - 150
    d.rounded_rectangle((bx0, by, bx1, by + 6), 3, fill=(105, 105, 105, 255))  # dim track (alpha doesn't blend when drawing)
    d.ellipse((bx0 - 9, by - 6, bx0 + 9, by + 12), fill=(255, 255, 255, 255))
    d.text((bx0, by + 28), "0:00", font=_font(24), fill=(170, 170, 170, 255), anchor="lt")
    if t["length"]:
        d.text((bx1, by + 28), t["length"], font=_font(24), fill=(170, 170, 170, 255), anchor="rt")

    out = BytesIO()
    bg.convert("RGB").save(out, "JPEG", quality=90)
    return out.getvalue()


def _make_card(t: dict):
    """Downloads the cover and builds the card. Returns JPEG bytes or None."""
    if not t["art"]:
        return None
    try:
        r = requests.get(t["art"], timeout=20)
        r.raise_for_status()
        return _build_card(r.content, t)
    except Exception as e:
        logger.warning(f"Could not build music card: {e}")
        return None


def _photo(t: dict) -> BytesIO:
    buf = BytesIO(t["card"])
    buf.name = "card.jpg"
    return buf


# ---------- message building ----------
def _caption(t: dict, idx: int, total: int) -> str:
    lines = [f"🎵 <b>{html.escape(t['title'])}</b>", f"by {html.escape(t['artist'])}", ""]
    a = t.get("audius")
    if a and a["downloadable"]:
        lines.append("Full song below")
    elif a:
        lines.append("Full song: tap Listen on Audius")
    else:
        lines.append("Full song: tap Open in Spotify")
    if t["explicit"]:
        lines.append("Explicit")
    lines.append(f"Result {idx + 1} of {total}")
    return "\n".join(lines)


def _keyboard(t: dict, total: int):
    rows = []
    links = [InlineKeyboardButton("Open in Spotify", url=t["spotify"])]
    a = t.get("audius")
    if a and a["link"]:
        links.append(InlineKeyboardButton("Listen on Audius", url=a["link"]))
    rows.append(links)
    if total > 1:
        rows.append([InlineKeyboardButton("Not this one? Next", callback_data="music:next")])
    return InlineKeyboardMarkup(rows)


async def _prepare(t: dict) -> None:
    """Everything slow, done once per result: album details, card, Audius match."""
    if t["prepared"]:
        return
    t["prepared"] = True
    await asyncio.to_thread(_enrich, t)
    t["card"] = await asyncio.to_thread(_make_card, t)
    t["audius"] = await asyncio.to_thread(_find_audius, t)


async def _send_full_song(reply_to, context, t: dict):
    """Sends the full file under the card when the artist allows downloads.
    Returns the message id or None."""
    a = t.get("audius")
    if not a or not a["downloadable"]:
        return None
    try:
        await context.bot.send_chat_action(reply_to.chat_id, ChatAction.UPLOAD_VOICE)
    except TelegramError:
        pass
    data = await asyncio.to_thread(_download_audio, a["id"])
    if not data:
        return None
    try:
        msg = await reply_to.reply_audio(
            audio=data, title=t["title"][:64], performer=t["artist"][:64]
        )
        return msg.message_id
    except TelegramError as e:
        logger.warning(f"Could not send full song: {e}")
        return None


async def _send_track(m, context, results: list[dict], requester_id: int) -> None:
    t = results[0]
    await _prepare(t)
    caption, kb = _caption(t, 0, len(results)), _keyboard(t, len(results))

    card = None
    if t["card"]:
        try:
            card = await m.reply_photo(
                _photo(t), caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb
            )
        except TelegramError as e:
            logger.warning(f"Could not send card: {e}")
    if card is None:  # no artwork / card failed - plain info message
        card = await m.reply_text(caption, parse_mode=ParseMode.HTML, reply_markup=kb)

    audio_id = await _send_full_song(card, context, t)

    state = context.bot_data.setdefault("music_state", {})
    state[f"{card.chat_id}:{card.message_id}"] = {
        "results": results,
        "idx": 0,
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

    try:
        await context.bot.send_chat_action(chat.id, ChatAction.UPLOAD_PHOTO)
    except TelegramError:
        pass

    results, err = await asyncio.to_thread(_search_all, query)
    if err:
        await m.reply_text("Couldn't reach the music service right now. Try again in a minute.")
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
    await _prepare(t)
    caption, kb = _caption(t, idx, total), _keyboard(t, total)

    try:
        if not msg.photo:  # the original was a plain text message
            await q.edit_message_text(caption, parse_mode=ParseMode.HTML, reply_markup=kb)
        elif t["card"]:
            await q.edit_message_media(
                InputMediaPhoto(_photo(t), caption=caption, parse_mode=ParseMode.HTML),
                reply_markup=kb,
            )
        else:
            await q.edit_message_caption(caption, parse_mode=ParseMode.HTML, reply_markup=kb)
        state["idx"] = idx
    except TelegramError as e:
        logger.warning(f"Could not switch music result: {e}")
        return

    if state.get("audio_id"):
        try:
            await context.bot.delete_message(msg.chat_id, state["audio_id"])
        except TelegramError:
            pass
    state["audio_id"] = await _send_full_song(msg, context, t)


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
