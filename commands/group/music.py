"""commands/group/music.py  -  Goddess Music (card edition, full audio)

    /music <song or "artist - song">
        A designed "now playing" card (colours pulled from the album art,
        waveform player, chips for album / year / genre / BPM) with:
            ▶ Play         sends the FULL song right under the card
                           (tap again = ⏹ Stop, removes it)
            Spotify | Apple Music | YT Music    search links
            ⏮  1/5  ⏭  ✕   cycle through the 5 best matches / close
        Song info: Deezer's free public API.
        Full audio: the group library first (instant), otherwise the best
        matching YouTube upload (picked by title, artist and exact duration)
        downloaded with yt-dlp. Tags (title / artist / cover) come from Deezer,
        so no more "(Official Video)" in the player.

    GROUP LIBRARY: admins can upload songs they own or have the rights to
    share. Requests that match a library song play the saved file instantly.

    Admins and creators only (always allowed to request too):
    /music access                      show the current mode
    /music access everyone|selected|off
        everyone  - every member can request
        selected  - only members you allowed (default)
        off       - members can't request at all
    /music allow     (reply to a member, or add their numeric ID)
    /music disallow  (reply to a member, or add their numeric ID)
    /music users     list the members who are allowed
    /music add       (reply to an audio file) save it to the library;
                     optionally /music add Artist - Title
    /music remove <id>   delete a library song
    /music list      show the library

Set AUTO_PLAY = True below to send the audio automatically instead of
waiting for the Play button.

Fonts: Poppins is downloaded once into <panel root>/fonts on first start.
Handler group used: 9 (the card buttons).
"""

import asyncio
import colorsys
import difflib
import hashlib
import html
import json
import logging
import math
import os
import re
import secrets
import shutil
import threading
import time
import unicodedata
from io import BytesIO
from urllib.parse import quote

import requests
import yt_dlp
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

# ---------------------------------------------------------------- config
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))  # .../bot_src/commands/group
_PERSISTENT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))
SETTINGS_FILE = os.path.join(_PERSISTENT_DIR, "music_settings.json")
LIBRARY_FILE = os.path.join(_PERSISTENT_DIR, "music_library.json")
FONT_DIR = os.path.join(_PERSISTENT_DIR, "fonts")
TEMP_DIR = "temp_music"

SEARCH_URL = "https://api.deezer.com/search"
ALBUM_URL = "https://api.deezer.com/album"
TRACK_URL = "https://api.deezer.com/track"

AUTO_PLAY = False            # True = send the audio without waiting for the Play button
CARD_STYLE = "wide"          # "wide" = compact landscape card, "tall" = big portrait card
RESULT_LIMIT = 5
COOLDOWN_SECONDS = 20        # per member, admins/creators are exempt
STATE_CAP = 60               # remembered sessions (each holds up to 5 cards)
MAX_BYTES = 49 * 1024 * 1024 # Telegram bots can upload up to 50 MB
MAX_YT_SECONDS = 15 * 60     # ignore YouTube uploads longer than this
YT_CLIENTS = (None, ["tv"], ["web_safari"], ["mweb"])  # tried in order if YouTube blocks one

FONT_BASE = "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/"
FONT_FILES = {"bold": "Poppins-Bold.ttf", "med": "Poppins-Medium.ttf", "reg": "Poppins-Regular.ttf"}

MODES = ("everyone", "selected", "off")
ADMIN_SUBS = ("allow", "disallow", "access", "users", "add", "remove", "list")

_last_request: dict = {}
_RENDER_SEM = asyncio.Semaphore(2)
_DL_SEM = asyncio.Semaphore(2)
_BG: set = set()  # keeps fire-and-forget tasks alive


# ---------------------------------------------------------------- settings storage
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


# ---------------------------------------------------------------- permissions
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


# ---------------------------------------------------------------- fonts
_font_cache: dict = {}


def _ensure_fonts() -> None:
    """Downloads Poppins once (runs in a background thread at startup)."""
    try:
        os.makedirs(FONT_DIR, exist_ok=True)
    except OSError as e:
        logger.warning(f"Font folder unavailable: {e}")
        return
    for name in FONT_FILES.values():
        path = os.path.join(FONT_DIR, name)
        if os.path.exists(path) and os.path.getsize(path) > 10_000:
            continue
        try:
            r = requests.get(FONT_BASE + name, timeout=20)
            r.raise_for_status()
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(r.content)
            os.replace(tmp, path)
            logger.info(f"Downloaded font {name}")
        except Exception as e:
            logger.warning(f"Could not download font {name}: {e}")


def _f(key: str, size: int):
    ck = (key, size)
    if ck in _font_cache:
        return _font_cache[ck]
    path = os.path.join(FONT_DIR, FONT_FILES[key])
    try:
        font = ImageFont.truetype(path, size)
        _font_cache[ck] = font  # only cache the real thing
        return font
    except Exception:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


# ---------------------------------------------------------------- Deezer
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
        "track_id": item.get("id"),
        "year": "",
        "genre": "",
        "bpm": 0,
        "secs": secs,
        "explicit": bool(item.get("explicit_lyrics")),
        "art": album.get("cover_xl") or album.get("cover_big") or album.get("cover") or "",
        "query": quote(f"{title} {artist}"),
        "enriched": False,
        "tracked": False,
        "task": None,
        "card": None,
        "thumb": None,
        "library": None,
        "file_id": None,
        "yt_id": None,
    }


def _search_all(query: str):
    """Returns (results, error). error is None or 'unavailable'."""
    try:
        r = requests.get(SEARCH_URL, params={"q": query.replace(" - ", " "), "limit": 15}, timeout=10)
        r.raise_for_status()
        body = r.json()
        if "error" in body:  # Deezer reports some errors with a 200 status
            logger.warning(f"Deezer error: {body['error']}")
            return [], "unavailable"
        return [p for p in map(_parse, body.get("data", [])) if p], None
    except Exception as e:
        logger.warning(f"Deezer search failed: {e}")
        return [], "unavailable"


def _enrich_track(t: dict) -> None:
    """Full artist list (features), exact year and BPM."""
    if t["tracked"] or not t.get("track_id"):
        return
    t["tracked"] = True
    try:
        r = requests.get(f"{TRACK_URL}/{t['track_id']}", headers={"Accept-Language": "en"}, timeout=10)
        r.raise_for_status()
        d = r.json()
        names = []
        for c in d.get("contributors") or []:
            n = c.get("name")
            if n and n not in names:
                names.append(n)
        if names:
            t["artist"] = ", ".join(names)
        t["year"] = (d.get("release_date") or "")[:4] or t["year"]
        t["bpm"] = int(d.get("bpm") or 0)
    except Exception as e:
        logger.info(f"Could not fetch track details: {e}")


def _enrich_album(t: dict) -> None:
    """Genre (and year fallback) from the album."""
    if t["enriched"]:
        return
    t["enriched"] = True
    if not t.get("album_id"):
        return
    try:
        r = requests.get(f"{ALBUM_URL}/{t['album_id']}", headers={"Accept-Language": "en"}, timeout=10)
        r.raise_for_status()
        album = r.json()
        t["year"] = t["year"] or (album.get("release_date") or "")[:4]
        genres = (album.get("genres") or {}).get("data") or []
        t["genre"] = genres[0].get("name", "") if genres else ""
    except Exception as e:
        logger.info(f"Could not fetch album details: {e}")


def _tokens(text: str) -> set:
    """Lowercase words with a light plural strip, so 'blessing' ~ 'blessings'."""
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words}


def _rank(query: str, items: list[dict]) -> list[dict]:
    """Best match first: share of the typed words found in title + artists + album.
    Ties keep Deezer's own (popularity) order."""
    q = _tokens(query)
    if not q:
        return items

    def score(t):
        return len(q & _tokens(f"{t['title']} {t['artist']} {t['album']}")) / len(q)

    return sorted(items, key=score, reverse=True)


_FILLER = {"by", "ft", "feat", "featuring", "x", "and", "the"}


def _coverage(q: set, t: dict) -> float:
    q = q - _FILLER
    if not q:
        return 1.0
    return len(q & _tokens(f"{t['title']} {t['artist']} {t['album']}")) / len(q)


def _dedupe(items: list[dict]) -> list[dict]:
    seen, uniq = set(), []  # same song on album + single + compilation -> keep one
    for t in items:
        key = (re.sub(r"[^a-z0-9]+", "", t["title"].lower()), re.sub(r"[^a-z0-9]+", "", t["artist"].lower()))
        if key not in seen:
            seen.add(key)
            uniq.append(t)
    return uniq


async def _find_songs(query: str):
    q = _tokens(query)
    results, err = await asyncio.to_thread(_search_all, query)

    ranked = []
    if results:
        head = _dedupe(results)[:8]
        await asyncio.gather(*(asyncio.to_thread(_enrich_track, t) for t in head))
        ranked = _rank(query, head)[:RESULT_LIMIT]
    if ranked and _coverage(q, ranked[0]) >= 0.99:  # every typed word matched
        return ranked, None

    # weak / no Deezer match (small or local artists): compare with YouTube info
    yt = _rank(query, _dedupe(await asyncio.to_thread(_yt_results, query)))[:RESULT_LIMIT]
    if yt and (not ranked or _coverage(q, yt[0]) > _coverage(q, ranked[0])):
        return yt, None
    if ranked:
        return ranked, None
    return [], err


# ---------------------------------------------------------------- group library
def _norm(text: str) -> str:
    text = re.sub(r"[\(\[].*?[\)\]]", "", text or "")  # drop "(feat. X)" / "[Remix]"
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _lib_load() -> list:
    if not os.path.exists(LIBRARY_FILE):
        return []
    try:
        with open(LIBRARY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _lib_save(tracks: list) -> None:
    tmp = LIBRARY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tracks, f, indent=2)
    os.replace(tmp, LIBRARY_FILE)


def _lib_search(query: str) -> dict | None:
    """A library song the query clearly means: every typed word is in the
    song's title/artist AND the typed words cover a good part of it (so
    'love' doesn't hijack a library song called 'Love Me Like You Do')."""
    q = _tokens(query)
    if not q:
        return None
    best, best_score = None, 0.0
    for e in _lib_load():
        et = _tokens(f"{e['title']} {e['artist']}")
        if not et:
            continue
        common = q & et
        cov_q, cov_e = len(common) / len(q), len(common) / len(et)
        if cov_q == 1.0 and cov_e >= 0.5:
            score = cov_q + cov_e
        else:  # typo tolerance
            nq = _norm(query)
            ratio = max(
                difflib.SequenceMatcher(None, nq, _norm(e["title"])).ratio(),
                difflib.SequenceMatcher(None, nq, _norm(f"{e['title']}{e['artist']}")).ratio(),
            )
            if ratio < 0.85:
                continue
            score = ratio
        if score > best_score:
            best, best_score = e, score
    return best


def _lib_match(t: dict) -> dict | None:
    """The library copy of THIS Deezer result (same title, overlapping artist)."""
    tt, ta = _norm(t["title"]), _tokens(t["artist"])
    for e in _lib_load():
        et = _norm(e["title"])
        if et != tt and difflib.SequenceMatcher(None, et, tt).ratio() < 0.92:
            continue
        ea = _tokens(e["artist"])
        if not ea or (ea & ta):
            return e
    return None


def _from_library(e: dict) -> dict:
    artist = e.get("artist") or "Unknown artist"
    t = _parse({"title": e["title"], "id": None, "duration": e.get("duration") or 0,
                "artist": {"name": artist}, "album": {}}) or {}
    t.update({"enriched": True, "tracked": True, "library": e})
    return t


# ---------------------------------------------------------------- the card
S = 2                      # draw at 2x, shrink at the end = smooth edges
W, H = 1080, 1350


def _sc(v: float) -> int:
    return int(round(v * S))


def _display(text: str) -> str:
    """Poppins lacks the dotted Yoruba/Vietnamese letters (ọ, ẹ, ṣ...) - show the base letter."""
    out = []
    for ch in text or "":
        if 0x1E00 <= ord(ch) <= 0x1EFF:
            ch = unicodedata.normalize("NFD", ch)[0]
        out.append(ch)
    return "".join(out).strip()


def _fit(m, text: str, key: str, size: int, max_w: float, min_size: int):
    """Shrinks the font until the text fits, then truncates with an ellipsis."""
    s = size
    while s >= min_size:
        f = _f(key, s)
        if m.textlength(text, font=f) <= max_w:
            return f, text
        s -= _sc(2)
    f = _f(key, min_size)
    while text and m.textlength(text + "…", font=f) > max_w:
        text = text[:-1]
    return f, text.rstrip() + "…"


def _spaced_w(m, text: str, f, sp: float) -> float:
    return sum(m.textlength(c, font=f) for c in text) + sp * max(len(text) - 1, 0)


def _draw_spaced(d, x: float, y: float, text: str, f, sp: float, fill) -> None:
    for c in text:
        d.text((x, y), c, font=f, fill=fill)
        x += d.textlength(c, font=f) + sp


def _fmt(secs: int) -> str:
    return f"{secs // 60}:{secs % 60:02d}"


def _mix(a, b, k: float):
    return tuple(int(a[i] * (1 - k) + b[i] * k) for i in range(3))


def _palette(cover: Image.Image):
    """(hue, accent_rgb, colorful) from the artwork's most vivid colour."""
    small = cover.convert("RGB").resize((48, 48))
    buckets: dict = {}
    hx = hy = 0.0  # area-weighted average hue, for the background
    for r, g, b in small.getdata():
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if v > 0.15:
            hx += math.cos(2 * math.pi * h) * s
            hy += math.sin(2 * math.pi * h) * s
        w = s * v if v > 0.25 else 0.0
        tot = buckets.setdefault(int(h * 12) % 12, [0.0, 0.0, 0.0, 0.0])
        tot[0] += w
        tot[1] += r * w
        tot[2] += g * w
        tot[3] += b * w
    k = max(buckets, key=lambda i: buckets[i][0])
    w, rs, gs, bs = buckets[k]
    if w < 3.0:  # black & white / very muted artwork
        return 0.0, (238, 238, 242), False
    h, s, v = colorsys.rgb_to_hsv(rs / w / 255, gs / w / 255, bs / w / 255)
    accent = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(h, min(max(s, 0.45), 0.9), max(v, 0.9)))
    bg_hue = (math.atan2(hy, hx) / (2 * math.pi)) % 1.0 if (hx or hy) else h
    return bg_hue, accent, True


def _background(cover: Image.Image, hue: float, colorful: bool, cw: int, ch: int):
    sat = 0.55 if colorful else 0.0
    top = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, sat, 0.36))
    bot = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, sat, 0.07))
    base = ImageOps.colorize(Image.linear_gradient("L").resize((cw, ch)), black=top, white=bot)

    # soft colour wash from the cover itself (blur small, then scale up = cheap + smooth)
    wash = ImageOps.fit(cover, (cw // 10, ch // 10), Image.LANCZOS).filter(ImageFilter.GaussianBlur(5))
    wash = ImageEnhance.Brightness(wash.resize((cw, ch), Image.BICUBIC)).enhance(0.5)
    base = Image.blend(base, wash, 0.45)

    edges = Image.radial_gradient("L").resize((cw, ch)).point(lambda p: int((p / 255) ** 2 * 150))
    base = Image.composite(Image.new("RGB", (cw, ch), (0, 0, 0)), base, edges)
    return base.convert("RGBA")


def _glow(size, rect, radius: int, color, alpha: int, blur: float):
    """Soft coloured shadow, built at 1/4 size so the blur stays cheap."""
    q = 4
    small = Image.new("RGBA", (size[0] // q, size[1] // q), tuple(color) + (0,))
    ImageDraw.Draw(small).rounded_rectangle(
        tuple(v / q for v in rect), radius / q, fill=tuple(color) + (alpha,)
    )
    return small.filter(ImageFilter.GaussianBlur(blur / q)).resize(size, Image.BICUBIC)


def _wave(seed: str, n: int) -> list[float]:
    """Pretty waveform heights (0..1) that stay the same for the same song."""
    raw, h = [], hashlib.sha256(seed.encode()).digest()
    while len(raw) < n + 2:
        raw += list(h)
        h = hashlib.sha256(h).digest()
    vals = [(raw[i] + 2 * raw[i + 1] + raw[i + 2]) / 1020 for i in range(n)]
    return [
        min(max(v * 1.3 * (0.45 + 0.55 * math.sin(math.pi * (i + 0.5) / n)), 0.08), 1.0)
        for i, v in enumerate(vals)
    ]


WIDE_W, WIDE_H, WIDE_OUT = 960, 500, 1.25   # design size, output = x1.25 (1200x625)


def _title_lines(m, text: str, max_w: float):
    """One line if it fits (46 down to 30), otherwise two lines at 34 (2nd one ellipsized)."""
    for size in range(_sc(46), _sc(30) - 1, -_sc(2)):
        f = _f("bold", size)
        if m.textlength(text, font=f) <= max_w:
            return f, [text]
    f = _f("bold", _sc(34))
    words, line1, i = text.split(), "", 0
    while i < len(words) and m.textlength((line1 + " " + words[i]).strip(), font=f) <= max_w:
        line1 = (line1 + " " + words[i]).strip()
        i += 1
    if not line1 or i >= len(words):  # one giant word - shrink + truncate it
        f, one = _fit(m, text, "bold", _sc(34), max_w, _sc(34))
        return f, [one]
    _, line2 = _fit(m, " ".join(words[i:]), "bold", _sc(34), max_w, _sc(34))
    return f, [line1, line2]


def _render_wide(cover_bytes: bytes, t: dict) -> bytes:
    """Compact landscape card: cover on the left, info + player on the right."""
    cover = Image.open(BytesIO(cover_bytes)).convert("RGB")
    CW, CH = _sc(WIDE_W), _sc(WIDE_H)
    X0, X1 = _sc(486), _sc(904)

    hue, accent, colorful = _palette(cover)
    canvas = _background(cover, hue, colorful, CW, CH)

    # --- cover with glow + shadow
    cs, rad = _sc(380), _sc(30)
    cx0, cy0 = _sc(56), _sc(60)
    canvas = Image.alpha_composite(
        canvas, _glow((CW, CH), (cx0 + _sc(14), cy0 + _sc(34), cx0 + cs - _sc(14), cy0 + cs + _sc(34)), rad, accent, 105, _sc(60))
    )
    canvas = Image.alpha_composite(
        canvas, _glow((CW, CH), (cx0, cy0 + _sc(18), cx0 + cs, cy0 + cs + _sc(18)), rad, (0, 0, 0), 150, _sc(24))
    )
    vc, vr = (cx0 + cs // 2 + _sc(40), cy0 + cs // 2), cs // 2 - _sc(6)   # vinyl peeking out behind the cover
    vinyl = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    vd = ImageDraw.Draw(vinyl)
    vd.ellipse((vc[0] - vr, vc[1] - vr, vc[0] + vr, vc[1] + vr), fill=(14, 14, 18, 238))
    for k in range(1, 9):  # grooves
        rr = int(vr * (0.36 + 0.075 * k))
        vd.ellipse((vc[0] - rr, vc[1] - rr, vc[0] + rr, vc[1] + rr), outline=(255, 255, 255, 15), width=_sc(1))
    lr = int(vr * 0.30)
    vd.ellipse((vc[0] - lr, vc[1] - lr, vc[0] + lr, vc[1] + lr), fill=tuple(accent) + (255,))
    hr = _sc(7)
    vd.ellipse((vc[0] - hr, vc[1] - hr, vc[0] + hr, vc[1] + hr), fill=(14, 14, 18, 255))
    canvas = Image.alpha_composite(canvas, vinyl)

    art = ImageOps.fit(cover, (cs, cs), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (cs, cs), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, cs - 1, cs - 1), rad, fill=255)
    canvas.paste(art, (cx0, cy0), mask)

    # --- layout of the text block (title may take 1 or 2 lines)
    m = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    f_title, lines = _title_lines(m, _display(t["title"]), X1 - X0)
    line_h = _sc(44) if len(lines) == 2 else _sc(58)
    title_h = line_h * len(lines)
    top = _sc(96) + (_sc(88) - title_h) // 2
    artist_y = top + title_h + _sc(10)
    badge = _sc(28) if t["explicit"] else 0
    ax = X0 + (badge + _sc(12) if badge else 0)
    f_artist, artist_txt = _fit(m, _display(t["artist"]), "med", _sc(28), X1 - ax, _sc(20))
    chip_y, chip_h = artist_y + _sc(50), _sc(38)

    # --- translucent parts first (cover border, chips, dim waveform bars)
    overlay = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle((cx0, cy0, cx0 + cs, cy0 + cs), rad, outline=(255, 255, 255, 48), width=_sc(2))

    texts, x = [], X0
    chips_src = [
        (t["genre"], False),
        (t["year"], False),
        (f"{t['bpm']} BPM" if t.get("bpm") else "", False),
        (t["album"], True),
    ]
    for raw, may_cut in chips_src:
        raw = _display(raw)
        if not raw:
            continue
        room = X1 - x - _sc(32)
        if may_cut:
            if room < _sc(80):
                break
            f, txt = _fit(m, raw, "med", _sc(20), min(room, _sc(230)), _sc(20))
        else:
            f, txt = _f("med", _sc(20)), raw
            if m.textlength(txt, font=f) > room:
                continue
        w = m.textlength(txt, font=f) + _sc(32)
        od.rounded_rectangle((x, chip_y, x + w, chip_y + chip_h), chip_h // 2, fill=(255, 255, 255, 38))
        texts.append(((x + w / 2, chip_y + chip_h / 2), txt, f, (236, 236, 240), "mm"))
        x += w + _sc(10)

    n, bw = 30, _sc(8)
    gap = (X1 - X0 - n * bw) / (n - 1)
    wave_cy, progress = _sc(346), 0.34
    played = []
    for i, hgt in enumerate(_wave(f"{t['title']}{t['artist']}", n)):
        bh = _sc(10) + hgt * _sc(34)
        x0 = X0 + i * (bw + gap)
        box = (x0, wave_cy - bh / 2, x0 + bw, wave_cy + bh / 2)
        if (i + 0.5) / n <= progress:
            played.append(box)
        else:
            od.rounded_rectangle(box, bw // 2, fill=(255, 255, 255, 70))

    canvas = Image.alpha_composite(canvas, overlay)
    d = ImageDraw.Draw(canvas)

    # --- top label
    base_y, ebw, egap = _sc(66), _sc(5), _sc(4)
    for i, eh in enumerate((10, 18, 13)):
        ex = X0 + i * (ebw + egap)
        d.rounded_rectangle((ex, base_y - _sc(eh), ex + ebw, base_y), _sc(2), fill=accent)
    _draw_spaced(d, X0 + _sc(34), _sc(45), "NOW PLAYING", _f("bold", _sc(17)), _sc(3), accent)
    f_brand, brand = _f("med", _sc(15)), "GODDESS MUSIC"
    _draw_spaced(d, X1 - _spaced_w(m, brand, f_brand, _sc(2)), _sc(47), brand, f_brand, _sc(2), (180, 180, 188))

    # --- title, artist (+ explicit badge)
    for i, line in enumerate(lines):
        d.text((X0, top + i * line_h), line, font=f_title, fill=(255, 255, 255))
    if badge:
        d.rounded_rectangle((X0, artist_y + _sc(7), X0 + badge, artist_y + _sc(7) + badge), _sc(6), fill=(208, 208, 214))
        d.text((X0 + badge / 2, artist_y + _sc(7) + badge / 2), "E", font=_f("bold", _sc(18)), fill=(24, 24, 28), anchor="mm")
    d.text((ax, artist_y), artist_txt, font=f_artist, fill=_mix(accent, (255, 255, 255), 0.5))

    # --- chips text, waveform (played part), times
    for xy, txt, f, fill, anchor in texts:
        d.text(xy, txt, font=f, fill=fill, anchor=anchor)
    for box in played:
        d.rounded_rectangle(box, bw // 2, fill=accent)
    if t["secs"]:
        f, ty = _f("reg", _sc(18)), wave_cy + _sc(28)
        d.text((X0, ty), _fmt(int(t["secs"] * progress)), font=f, fill=(170, 170, 178))
        d.text((X1, ty), _fmt(int(t["secs"])), font=f, fill=(170, 170, 178), anchor="ra")

    # --- controls
    cy, cxm, white = _sc(416), (X0 + X1) // 2, (255, 255, 255)
    r = _sc(30)
    d.ellipse((cxm - r, cy - r, cxm + r, cy + r), fill=white)
    bwid, bhei, off = _sc(8), _sc(24), _sc(7)
    d.rounded_rectangle((cxm - off - bwid, cy - bhei // 2, cxm - off, cy + bhei // 2), _sc(2), fill=(18, 18, 22))
    d.rounded_rectangle((cxm + off, cy - bhei // 2, cxm + off + bwid, cy + bhei // 2), _sc(2), fill=(18, 18, 22))
    px, nx, tri = cxm - _sc(112), cxm + _sc(112), _sc(15)
    d.polygon([(px + _sc(12), cy - tri), (px + _sc(12), cy + tri), (px - _sc(9), cy)], fill=white)
    d.rounded_rectangle((px - _sc(15), cy - tri, px - _sc(10), cy + tri), _sc(2), fill=white)
    d.polygon([(nx - _sc(12), cy - tri), (nx - _sc(12), cy + tri), (nx + _sc(9), cy)], fill=white)
    d.rounded_rectangle((nx + _sc(10), cy - tri, nx + _sc(15), cy + tri), _sc(2), fill=white)
    hx, hr2 = X0 + _sc(14), _sc(8)  # heart
    d.ellipse((hx - 2 * hr2 + _sc(1), cy - hr2 * 1.7, hx + _sc(1), cy - hr2 * 0.1), fill=accent)
    d.ellipse((hx - _sc(1), cy - hr2 * 1.7, hx + 2 * hr2 - _sc(1), cy - hr2 * 0.1), fill=accent)
    d.polygon([(hx - 2 * hr2 + _sc(2), cy - hr2 * 0.75), (hx + 2 * hr2 - _sc(2), cy - hr2 * 0.75), (hx, cy + hr2 * 1.7)], fill=accent)
    qx = X1 - _sc(14)  # queue icon
    for k in range(3):
        qy = cy - _sc(9) + k * _sc(9)
        d.rounded_rectangle((qx - _sc(13), qy - _sc(2), qx + _sc(13), qy + _sc(2)), _sc(2), fill=(200, 200, 206))

    # --- shrink (smooth edges) + a touch of film grain
    ow, oh = int(WIDE_W * WIDE_OUT), int(WIDE_H * WIDE_OUT)
    img = canvas.convert("RGB").resize((ow, oh), Image.LANCZOS)
    grain = Image.effect_noise((ow, oh), 28).convert("RGB")
    img = Image.blend(img, ImageChops.overlay(img, grain), 0.06)
    out = BytesIO()
    img.save(out, "JPEG", quality=90, optimize=True)
    return out.getvalue()


def _render(cover_bytes: bytes, t: dict) -> bytes:
    return (_render_tall if CARD_STYLE == "tall" else _render_wide)(cover_bytes, t)


def _placeholder_cover(t: dict) -> bytes:
    hue = int(hashlib.md5(f"{t['title']}{t['artist']}".encode()).hexdigest()[:4], 16) / 65535
    top = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.5, 0.6))
    bottom = tuple(int(c * 255) for c in colorsys.hsv_to_rgb((hue + 0.08) % 1, 0.65, 0.2))
    img = ImageOps.colorize(Image.linear_gradient("L").resize((1000, 1000)), black=top, white=bottom)
    initial = (_display(t["title"])[:1] or "♪").upper()
    ImageDraw.Draw(img).text((500, 500), initial, font=_f("bold", 460), fill=(255, 255, 255), anchor="mm")
    out = BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue()


def _render_tall(cover_bytes: bytes, t: dict) -> bytes:
    cover = Image.open(BytesIO(cover_bytes)).convert("RGB")
    CW, CH = _sc(W), _sc(H)
    L, R = _sc(90), _sc(990)

    hue, accent, colorful = _palette(cover)
    canvas = _background(cover, hue, colorful, CW, CH)

    # --- cover with glow + shadow
    cs, rad = _sc(680), _sc(44)
    cx0, cy0 = (CW - cs) // 2, _sc(120)
    canvas = Image.alpha_composite(
        canvas, _glow((CW, CH), (cx0 + _sc(20), cy0 + _sc(50), cx0 + cs - _sc(20), cy0 + cs + _sc(50)), rad, accent, 105, _sc(90))
    )
    canvas = Image.alpha_composite(
        canvas, _glow((CW, CH), (cx0, cy0 + _sc(26), cx0 + cs, cy0 + cs + _sc(26)), rad, (0, 0, 0), 150, _sc(34))
    )
    art = ImageOps.fit(cover, (cs, cs), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (cs, cs), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, cs - 1, cs - 1), rad, fill=255)
    canvas.paste(art, (cx0, cy0), mask)

    # --- translucent parts first (chips, dim waveform bars, cover border)
    overlay = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    m = ImageDraw.Draw(Image.new("RGB", (8, 8)))  # just for measuring text
    od.rounded_rectangle((cx0, cy0, cx0 + cs, cy0 + cs), rad, outline=(255, 255, 255, 48), width=_sc(2))

    texts = []  # (xy, text, font, fill, anchor) drawn after the overlay is applied

    chip_y, chip_h, x = _sc(1006), _sc(52), L
    extras = [t["album"], t["year"], t["genre"], f"{t['bpm']} BPM" if t.get("bpm") else ""]
    for raw in extras:
        raw = _display(raw)
        if not raw:
            continue
        f, txt = _fit(m, raw, "med", _sc(25), _sc(400), _sc(25))
        w = m.textlength(txt, font=f) + _sc(44)
        if x + w > R:
            break
        od.rounded_rectangle((x, chip_y, x + w, chip_y + chip_h), chip_h // 2, fill=(255, 255, 255, 38))
        texts.append(((x + w / 2, chip_y + chip_h / 2), txt, f, (236, 236, 240), "mm"))
        x += w + _sc(14)

    n, bw = 46, _sc(10)
    gap = (R - L - n * bw) / (n - 1)
    wave_cy, progress = _sc(1118), 0.34
    played = []
    for i, hgt in enumerate(_wave(f"{t['title']}{t['artist']}", n)):
        bh = _sc(12) + hgt * _sc(50)
        x0 = L + i * (bw + gap)
        box = (x0, wave_cy - bh / 2, x0 + bw, wave_cy + bh / 2)
        if (i + 0.5) / n <= progress:
            played.append(box)
        else:
            od.rounded_rectangle(box, bw // 2, fill=(255, 255, 255, 70))

    canvas = Image.alpha_composite(canvas, overlay)
    d = ImageDraw.Draw(canvas)

    # --- top label
    base_y, ebw, egap = _sc(76), _sc(6), _sc(5)
    for i, eh in enumerate((14, 25, 18)):
        ex = L + i * (ebw + egap)
        d.rounded_rectangle((ex, base_y - _sc(eh), ex + ebw, base_y), _sc(3), fill=accent)
    _draw_spaced(d, L + _sc(46), _sc(59), "NOW PLAYING", _f("bold", _sc(22)), _sc(4), accent)
    f_brand = _f("med", _sc(20))
    brand = "GODDESS MUSIC"
    _draw_spaced(d, R - _spaced_w(m, brand, f_brand, _sc(3)), _sc(61), brand, f_brand, _sc(3), (180, 180, 188))

    # --- title + artist (+ explicit badge)
    f, txt = _fit(m, _display(t["title"]), "bold", _sc(66), R - L, _sc(38))
    d.text((L, _sc(838)), txt, font=f, fill=(255, 255, 255))

    ay, ax = _sc(934), L
    if t["explicit"]:
        bs = _sc(34)
        d.rounded_rectangle((L, ay + _sc(8), L + bs, ay + _sc(8) + bs), _sc(7), fill=(208, 208, 214))
        d.text((L + bs / 2, ay + _sc(8) + bs / 2), "E", font=_f("bold", _sc(22)), fill=(24, 24, 28), anchor="mm")
        ax += _sc(48)
    f, txt = _fit(m, _display(t["artist"]), "med", _sc(38), R - ax, _sc(26))
    d.text((ax, ay), txt, font=f, fill=_mix(accent, (255, 255, 255), 0.5))

    # --- chips text, waveform (played part), times
    for xy, txt, f, fill, anchor in texts:
        d.text(xy, txt, font=f, fill=fill, anchor=anchor)
    for box in played:
        d.rounded_rectangle(box, bw // 2, fill=accent)
    if t["secs"]:
        f = _f("reg", _sc(24))
        ty = wave_cy + _sc(50)
        d.text((L, ty), _fmt(int(t["secs"] * progress)), font=f, fill=(170, 170, 178))
        d.text((R, ty), _fmt(int(t["secs"])), font=f, fill=(170, 170, 178), anchor="ra")

    # --- controls
    cy, cxm, white = _sc(1256), CW // 2, (255, 255, 255)
    r = _sc(46)
    d.ellipse((cxm - r, cy - r, cxm + r, cy + r), fill=white)
    bwid, bhei, off = _sc(11), _sc(34), _sc(11)
    d.rounded_rectangle((cxm - off - bwid, cy - bhei // 2, cxm - off, cy + bhei // 2), _sc(3), fill=(18, 18, 22))
    d.rounded_rectangle((cxm + off, cy - bhei // 2, cxm + off + bwid, cy + bhei // 2), _sc(3), fill=(18, 18, 22))
    px, nx, tri = cxm - _sc(190), cxm + _sc(190), _sc(22)
    d.polygon([(px + _sc(18), cy - tri), (px + _sc(18), cy + tri), (px - _sc(12), cy)], fill=white)
    d.rounded_rectangle((px - _sc(22), cy - tri, px - _sc(15), cy + tri), _sc(3), fill=white)
    d.polygon([(nx - _sc(18), cy - tri), (nx - _sc(18), cy + tri), (nx + _sc(12), cy)], fill=white)
    d.rounded_rectangle((nx + _sc(15), cy - tri, nx + _sc(22), cy + tri), _sc(3), fill=white)

    # --- shrink (smooth edges) + a touch of film grain
    img = canvas.convert("RGB").resize((W, H), Image.LANCZOS)
    grain = Image.effect_noise((W, H), 28).convert("RGB")
    img = Image.blend(img, ImageChops.overlay(img, grain), 0.06)
    out = BytesIO()
    img.save(out, "JPEG", quality=90, optimize=True)
    return out.getvalue()


def _make_card(t: dict):
    """Downloads the cover (or makes a placeholder), builds the card + audio thumbnail."""
    cover = None
    if t["art"]:
        try:
            r = requests.get(t["art"], timeout=20)
            r.raise_for_status()
            cover = r.content
        except Exception as e:
            logger.warning(f"Could not download cover: {e}")
    cover = cover or _placeholder_cover(t)
    try:
        thumb = ImageOps.fit(Image.open(BytesIO(cover)).convert("RGB"), (320, 320), Image.LANCZOS)
        buf = BytesIO()
        thumb.save(buf, "JPEG", quality=85)
        t["thumb"] = buf.getvalue()
    except Exception as e:
        logger.info(f"Could not make audio thumbnail: {e}")
    try:
        return _render(cover, t)
    except Exception as e:
        logger.warning(f"Could not build music card: {e}")
        return None


def _photo(t: dict) -> BytesIO:
    buf = BytesIO(t["card"])
    buf.name = "card.jpg"
    return buf


# ---------------------------------------------------------------- full audio (YouTube via yt-dlp)
_BAD_WORDS = (
    "reaction", "react", "reacts", "cover", "karaoke", "instrumental", "slowed", "reverb",
    "sped up", "nightcore", "8d", "tutorial", "lesson", "remix", "live", "mashup",
    "acoustic", "review", "interview", "behind the scenes",
)


def _cookie_path() -> str | None:
    for p in ("cookies.txt", "../cookies.txt", os.path.join(os.getcwd(), "cookies.txt")):
        if os.path.exists(p):
            return p
    return None


def _safe_remove(path: str | None) -> None:
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _cleanup_stale() -> None:
    if not os.path.isdir(TEMP_DIR):
        return
    cutoff = time.time() - 3600
    for name in os.listdir(TEMP_DIR):
        p = os.path.join(TEMP_DIR, name)
        try:
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                os.remove(p)
        except OSError:
            pass


def _score_yt(t: dict, e: dict) -> float:
    """How likely this YouTube upload is THE song: title/artist match, a duration
    within a few seconds of Deezer's, 'Topic' channels, and no reactions/covers/remixes."""
    title = (e.get("title") or "").lower()
    chan = (e.get("channel") or e.get("uploader") or "").lower()
    have = _tokens(title)
    score = 0.0

    want = _tokens(t["title"])
    if want:
        score += 3 * len(want & have) / len(want)
    artists = _tokens(t["artist"])
    if artists:
        score += 2 * len(artists & (have | _tokens(chan))) / len(artists)

    dur = int(e.get("duration") or 0)
    if t.get("secs") and dur:
        diff = abs(dur - t["secs"])
        score += 3 if diff <= 5 else 2 if diff <= 12 else 0.5 if diff <= 30 else -3

    if chan.endswith("- topic"):
        score += 1.5
    if "official audio" in title or "(audio)" in title:
        score += 1

    own = t["title"].lower()
    for w in _BAD_WORDS:
        if re.search(rf"\b{re.escape(w)}\b", title) and not re.search(rf"\b{re.escape(w)}\b", own):
            score -= 2.5
    return score


def _yt_search_entries(q: str, n: int) -> list[dict]:
    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "extract_flat": True, "socket_timeout": 15,
    }
    cookies = _cookie_path()
    if cookies:
        opts["cookiefile"] = cookies
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{n}:{q}", download=False)
    except Exception as e:
        logger.warning(f"[Music] yt search failed: {e}")
        return []
    return [
        e for e in (info or {}).get("entries") or []
        if e and e.get("id") and (not e.get("duration") or int(e["duration"]) <= MAX_YT_SECONDS)
    ]


def _yt_candidates(t: dict) -> list[str]:
    """Video ids of the 2 best YouTube matches for this song."""
    first_artist = t["artist"].split(",")[0].strip()
    entries = _yt_search_entries(f"{first_artist} {t['title']}", 8)
    entries.sort(key=lambda e: _score_yt(t, e), reverse=True)
    return [e["id"] for e in entries[:2]]


_NOISE_RE = re.compile(
    r"\s*[\(\[][^\)\]]*\b(official|lyrics?|audio|video|visuali[sz]er|hd|hq|4k)\b[^\)\]]*[\)\]]", re.I
)


def _yt_results(query: str) -> list[dict]:
    """Song cards built from YouTube info - for small/local artists Deezer doesn't have."""
    out, ql = [], query.lower()
    for e in _yt_search_entries(query, 8):
        raw = (e.get("title") or "").strip()
        low = raw.lower()
        if any(
            re.search(rf"\b{re.escape(w)}\b", low) and not re.search(rf"\b{re.escape(w)}\b", ql)
            for w in _BAD_WORDS
        ):
            continue
        chan = re.sub(r"\s*-\s*topic$", "", e.get("channel") or e.get("uploader") or "", flags=re.I).strip()
        if " - " in raw:
            artist, title = (p.strip() for p in raw.split(" - ", 1))
        else:
            artist, title = chan or "Unknown artist", raw
        title = _NOISE_RE.sub("", title).strip(" -|–")
        if not title:
            continue
        thumbs = e.get("thumbnails") or []
        art = ""
        if thumbs:
            art = max(thumbs, key=lambda x: (x.get("width") or 0) * (x.get("height") or 0)).get("url", "")
        t = _parse({"title": title, "id": None, "duration": int(e.get("duration") or 0),
                    "artist": {"name": artist}, "album": {}})
        if t:
            t.update({"art": art or f"https://i.ytimg.com/vi/{e['id']}/mqdefault.jpg",
                      "enriched": True, "tracked": True, "yt_id": e["id"]})
            out.append(t)
    return out


def _yt_download(video_id: str, clients=YT_CLIENTS) -> str | None:
    os.makedirs(TEMP_DIR, exist_ok=True)
    stem = secrets.token_hex(6)
    opts = {
        # m4a first (Telegram plays it natively, no ffmpeg needed), then anything
        "format": "ba[ext=m4a]/ba[acodec^=mp4a]/ba/b",
        "outtmpl": os.path.join(TEMP_DIR, f"{stem}.%(ext)s"),
        "noplaylist": True, "quiet": True, "no_warnings": True,
        "max_filesize": MAX_BYTES, "socket_timeout": 20, "retries": 3,
        "js_runtimes": {"deno": {}, "node": {}, "bun": {}},  # needed by newer yt-dlp
        "match_filter": yt_dlp.utils.match_filter_func(f"duration < {MAX_YT_SECONDS}"),
    }
    cookies = _cookie_path()
    if cookies:
        opts["cookiefile"] = cookies

    url = f"https://www.youtube.com/watch?v={video_id}"
    for pc in clients:  # YouTube randomly blocks single clients - try several
        attempt = dict(opts)
        if pc:
            attempt["extractor_args"] = {"youtube": {"player_client": pc}}
        try:
            with yt_dlp.YoutubeDL(attempt) as ydl:
                ydl.download([url])
            for name in os.listdir(TEMP_DIR):
                if name.startswith(stem + "."):
                    return os.path.join(TEMP_DIR, name)
        except Exception as e:
            logger.warning(f"[Music] yt download failed (clients={pc}): {e}")
            for name in os.listdir(TEMP_DIR):  # drop partial files before retrying
                if name.startswith(stem + "."):
                    _safe_remove(os.path.join(TEMP_DIR, name))
    return None


def _get_audio_file(t: dict) -> str | None:
    ids = [t["yt_id"]] if t.get("yt_id") else _yt_candidates(t)
    for n, vid in enumerate(ids):
        path = _yt_download(vid, YT_CLIENTS if n == 0 else YT_CLIENTS[:2])
        if path:
            t["yt_id"] = vid
            return path
    return None


# ---------------------------------------------------------------- messages
def _caption(t: dict) -> str:
    return f"🎵 <b>{html.escape(t['title'])}</b> — {html.escape(t['artist'])}"


def _keyboard(t: dict, idx: int, total: int, playing: bool = False) -> InlineKeyboardMarkup:
    q = t["query"]
    top = [InlineKeyboardButton("⏹ Stop" if playing else "▶ Play", callback_data="music:play")]
    if total > 1:
        top += [
            InlineKeyboardButton("⏮", callback_data="music:prev"),
            InlineKeyboardButton(f"{idx + 1}/{total}", callback_data="music:noop"),
            InlineKeyboardButton("⏭", callback_data="music:next"),
        ]
    top.append(InlineKeyboardButton("✕", callback_data="music:close"))
    links = [
        InlineKeyboardButton("Spotify", url=f"https://open.spotify.com/search/{q}"),
        InlineKeyboardButton("Apple Music", url=f"https://music.apple.com/search?term={q}"),
        InlineKeyboardButton("YT Music", url=f"https://music.youtube.com/search?q={q}"),
    ]
    return InlineKeyboardMarkup([top, links])


async def _prepare(t: dict) -> None:
    """Everything slow, done once per result: album details, card, library match."""
    try:
        async with _RENDER_SEM:
            await asyncio.to_thread(_enrich_album, t)
            t["card"] = await asyncio.to_thread(_make_card, t)
        if not t["library"]:
            t["library"] = await asyncio.to_thread(_lib_match, t)
    except Exception as e:
        logger.warning(f"Prepare failed: {e}")


def _ensure(t: dict):
    """Starts preparing a result once; everyone who needs it awaits the same task."""
    if t["task"] is None:
        t["task"] = asyncio.ensure_future(_prepare(t))
    return t["task"]


def _spawn(coro) -> None:
    task = asyncio.ensure_future(coro)
    _BG.add(task)
    task.add_done_callback(_BG.discard)


async def _flash(msg, text: str, seconds: int = 8) -> None:
    """A short note that removes itself."""
    try:
        note = await msg.reply_text(text)
    except TelegramError:
        return

    async def _later():
        await asyncio.sleep(seconds)
        try:
            await note.delete()
        except TelegramError:
            pass

    _spawn(_later())


async def _remove_audio(context, chat_id: int, state: dict) -> None:
    audio_id = state.pop("audio_id", None)
    if audio_id:
        try:
            await context.bot.delete_message(chat_id, audio_id)
        except TelegramError:
            pass


async def _deliver_audio(context, card_msg, state: dict, t: dict) -> bool:
    """Sends the full song under the card. Library file -> cached Telegram file ->
    fresh YouTube download. Returns True when it was sent."""
    chat_id = card_msg.chat_id
    try:
        await context.bot.send_chat_action(chat_id, ChatAction.UPLOAD_VOICE)
    except TelegramError:
        pass

    common = {
        "title": t["title"][:64],
        "performer": t["artist"][:64],
        "duration": t["secs"] or None,
    }
    sent = None
    try:
        lib = t.get("library")
        if lib:  # the group's own upload - sent straight from Telegram's servers
            sent = await card_msg.reply_audio(audio=lib["file_id"], **common)
        elif t.get("file_id"):  # already downloaded once - instant
            sent = await card_msg.reply_audio(audio=t["file_id"], **common)
        else:
            async with _DL_SEM:
                path = await asyncio.to_thread(_get_audio_file, t)
            if not path:
                return False
            try:
                if os.path.getsize(path) > MAX_BYTES:
                    return False
                ext = os.path.splitext(path)[1].lower()
                with open(path, "rb") as f:
                    if ext in (".mp3", ".m4a"):
                        sent = await card_msg.reply_audio(
                            audio=f, thumbnail=t.get("thumb"), caption="via Goddess Music 🎧",
                            read_timeout=120, write_timeout=120, **common,
                        )
                        t["file_id"] = sent.audio.file_id if sent.audio else None
                    else:  # webm/opus etc. won't show as audio - send as a file instead
                        sent = await card_msg.reply_document(
                            document=f, filename=f"{t['title']}{ext}"[:100],
                            caption="via Goddess Music 🎧", read_timeout=120, write_timeout=120,
                        )
            finally:
                _safe_remove(path)
    except TelegramError as e:
        logger.warning(f"Could not send audio: {e}")
        return False

    if not sent:
        return False
    state["audio_id"] = sent.message_id
    try:
        await card_msg.edit_reply_markup(_keyboard(t, state["idx"], len(state["results"]), playing=True))
    except TelegramError:
        pass
    return True


async def _send_card(m, context, results: list[dict], requester_id: int) -> None:
    t = results[0]
    await _ensure(t)
    for other in results[1:]:  # warm up the rest so the arrows are instant
        _ensure(other)

    caption, kb = _caption(t), _keyboard(t, 0, len(results))
    msg = None
    if t["card"]:
        try:
            msg = await m.reply_photo(_photo(t), caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb)
        except TelegramError as e:
            logger.warning(f"Could not send card: {e}")
    if msg is None:  # no card - plain info message
        msg = await m.reply_text(caption, parse_mode=ParseMode.HTML, reply_markup=kb)

    state = context.bot_data.setdefault("music_state", {})
    key = f"{msg.chat_id}:{msg.message_id}"
    state[key] = {"results": results, "idx": 0, "user": requester_id, "busy": False}
    while len(state) > STATE_CAP:  # forget the oldest sessions
        state.pop(next(iter(state)))

    if AUTO_PLAY:
        st = state[key]
        st["busy"] = True
        try:
            if not await _deliver_audio(context, msg, st, t):
                await _flash(msg, "❌ Couldn't get the audio for this one. Try another result.")
        finally:
            st["busy"] = False


# ---------------------------------------------------------------- /music
async def music_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    m, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await m.reply_text("This only works inside a group.")
        return

    # admin subcommands: /music allow | disallow | access | users | add | remove | list
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
            usage += "\n\nAdmin: /music allow | disallow | access | users | add | remove | list"
        await m.reply_text(usage)
        return

    if not _privileged(user.id):
        wait = COOLDOWN_SECONDS - (time.time() - _last_request.get((chat.id, user.id), 0))
        if wait > 0:
            await m.reply_text(f"Easy, try again in {int(wait) + 1}s.")
            return
    _last_request[(chat.id, user.id)] = time.time()
    if len(_last_request) > 5000:
        _last_request.clear()

    try:
        await context.bot.send_chat_action(chat.id, ChatAction.UPLOAD_PHOTO)
    except TelegramError:
        pass

    lib_hit = await asyncio.to_thread(_lib_search, query)
    if lib_hit:
        results, err = [_from_library(lib_hit)], None
    else:
        results, err = await _find_songs(query)
    if err:
        await m.reply_text("Couldn't reach the music service right now. Try again in a minute.")
        return
    if not results:
        await m.reply_text("Couldn't find that song. Try adding the artist's name.")
        return
    await _send_card(m, context, results, user.id)


async def music_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    msg = q.message
    action = (q.data or "").split(":", 1)[-1]

    if action == "noop":
        await q.answer()
        return

    key = f"{msg.chat_id}:{msg.message_id}"
    state = context.bot_data.get("music_state", {}).get(key)
    if not state:
        await q.answer("This request has expired. Send /music again.", show_alert=True)
        return
    if q.from_user.id != state["user"] and not _privileged(q.from_user.id):
        await q.answer("Only the person who requested this can use these buttons.", show_alert=True)
        return

    total = len(state["results"])
    t = state["results"][state["idx"]]

    if action == "close":
        await q.answer()
        await _remove_audio(context, msg.chat_id, state)
        context.bot_data["music_state"].pop(key, None)
        try:
            await msg.delete()
        except TelegramError:
            pass
        return

    if action == "play":  # also acts as Stop while the song is showing
        if state["busy"]:
            await q.answer("One moment, getting your song... 🎧")
            return
        if state.get("audio_id"):
            await q.answer()
            await _remove_audio(context, msg.chat_id, state)
            try:
                await msg.edit_reply_markup(_keyboard(t, state["idx"], total, playing=False))
            except TelegramError:
                pass
            return
        state["busy"] = True
        try:
            await q.answer("Getting your song... 🎧")
            if not await _deliver_audio(context, msg, state, t):
                await _flash(msg, "❌ Couldn't get the audio for this one. Try another result.")
        finally:
            state["busy"] = False
        return

    if action not in ("next", "prev"):
        await q.answer()
        return

    if state["busy"]:
        await q.answer("Hold on, still getting your song... 🎧")
        return
    await q.answer()
    idx = (state["idx"] + (1 if action == "next" else -1)) % total
    t = state["results"][idx]
    await _ensure(t)
    caption, kb = _caption(t), _keyboard(t, idx, total)

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
    except TelegramError as e:
        logger.warning(f"Could not switch music result: {e}")
        return

    state["idx"] = idx
    await _remove_audio(context, msg.chat_id, state)  # the old song goes away with the old card
    if AUTO_PLAY:
        state["busy"] = True
        try:
            if not await _deliver_audio(context, msg, state, t):
                await _flash(msg, "❌ Couldn't get the audio for this one. Try another result.")
        finally:
            state["busy"] = False


# ---------------------------------------------------------------- admin subcommands
def _target(m, rest: list[str]):
    if m.reply_to_message and m.reply_to_message.from_user:
        u = m.reply_to_message.from_user
        return str(u.id), u.full_name
    if rest and rest[0].isdigit():
        return rest[0], f"member {rest[0]}"
    return None, None


async def _library_admin(update: Update, sub: str, rest: list[str]) -> None:
    m = update.effective_message
    tracks = _lib_load()

    if sub == "list":
        if not tracks:
            await m.reply_text("The library is empty. Reply to an audio file with /music add.")
            return
        lines = [f"{e['id']}. {e['title']} - {e['artist'] or 'unknown'}" for e in tracks[-40:]]
        extra = f"\n(showing the latest 40 of {len(tracks)})" if len(tracks) > 40 else ""
        await m.reply_text("Group library\n\n" + "\n".join(lines) + extra)
        return

    if sub == "remove":
        if not rest or not rest[0].isdigit():
            await m.reply_text("Usage: /music remove <id>  (ids are in /music list)")
            return
        keep = [e for e in tracks if str(e["id"]) != rest[0]]
        if len(keep) == len(tracks):
            await m.reply_text("No library song with that id.")
            return
        _lib_save(keep)
        await m.reply_text("Removed from the library.")
        return

    # add
    src = m.reply_to_message
    audio = getattr(src, "audio", None) if src else None
    if not audio and src and src.document and (src.document.mime_type or "").startswith("audio/"):
        audio = src.document
    if not audio:
        await m.reply_text(
            "Reply to an audio file with /music add (or /music add Artist - Title).\n"
            "Only add songs you own or have the rights to share."
        )
        return

    text = " ".join(rest).strip()
    if " - " in text:
        artist, title = (p.strip() for p in text.split(" - ", 1))
    else:
        title = text or getattr(audio, "title", None) or ""
        artist = getattr(audio, "performer", None) or ""
    if not title:
        name = getattr(audio, "file_name", None) or ""
        title = os.path.splitext(name)[0].replace("_", " ").strip()
    if not title:
        await m.reply_text("I couldn't read a title from that file. Use /music add Artist - Title")
        return

    for e in tracks:
        if e["file_unique_id"] == audio.file_unique_id:
            await m.reply_text(f"Already in the library as #{e['id']}: {e['title']}")
            return
    new_id = max((e["id"] for e in tracks), default=0) + 1
    tracks.append({
        "id": new_id,
        "file_id": audio.file_id,
        "file_unique_id": audio.file_unique_id,
        "title": title,
        "artist": artist,
        "duration": getattr(audio, "duration", 0) or 0,
        "added_by": update.effective_user.id,
    })
    _lib_save(tracks)
    await m.reply_text(
        f"Added to the library as #{new_id}: {title}" + (f" - {artist}" if artist else "")
        + "\n\nAnyone with music access can now request it by name."
    )


async def _admin(update: Update, context: ContextTypes.DEFAULT_TYPE, sub: str, rest: list[str]) -> None:
    if sub in ("add", "remove", "list"):
        await _library_admin(update, sub, rest)
        return
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


# ---------------------------------------------------------------- wiring
def register(app) -> None:
    threading.Thread(target=_ensure_fonts, daemon=True).start()
    os.makedirs(TEMP_DIR, exist_ok=True)
    _cleanup_stale()
    runtimes = [r for r in ("deno", "node", "bun") if shutil.which(r)]
    logger.info(
        f"[Music] yt-dlp {yt_dlp.version.__version__} | cookies: {bool(_cookie_path())} | "
        f"js runtimes: {runtimes or 'NONE'}"
    )
    app.add_handler(CommandHandler("music", music_cmd, filters=filters.ChatType.GROUPS))
    app.add_handler(CallbackQueryHandler(music_callback, pattern="^music:"), group=9)
