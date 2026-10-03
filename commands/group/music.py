"""
Goddess Music v2

What's better than v1:
- Mirrors are raced in parallel (fastest good answer wins) and dead ones are
  benched for 10 minutes instead of costing 6s on every search.
- Handles both old and new Saavn response shapes + HTML entities.
- YouTube fallback is a proper picker (search only, no arbitrary URLs),
  needs no ffmpeg, caps duration and file size.
- Only the person who searched can press the buttons; double-taps are ignored.
- Per-user cooldown, download concurrency limit, TTL cache with oldest-first
  eviction, temp files always cleaned up.
- If a Saavn download fails, it auto-retries via YouTube.

Requirements: python-telegram-bot (brings httpx), yt-dlp (keep updated).
Optional env: MUSIC_MIRRORS="https://a/api/search/songs,https://b/api/search/songs"
"""

import asyncio
import html
import os
import secrets
import time
import urllib.parse

import httpx
import yt_dlp

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from branding import header, DOT_DIVIDER

# ───────────────────────── config ─────────────────────────

TEMP_DIR = "temp_music"
MAX_BYTES = 49 * 1024 * 1024       # Telegram bot upload limit is 50MB
MAX_YT_SECONDS = 15 * 60           # skip YouTube results longer than this
CACHE_TTL = 15 * 60                # search results live 15 min
MAX_CACHE = 200
USER_COOLDOWN = 5                  # seconds between /music per user
MIRROR_BENCH = 10 * 60             # bench a failing mirror for 10 min
MAX_RESULTS = 8

DEFAULT_MIRRORS = [
    "https://jio-saavn-api-sigma.vercel.app/api/search/songs",
    "https://jiosaavn-api-v3.vercel.app/api/search/songs",
    "https://saavn.dev/api/search/songs",
]
MIRRORS = [
    m.strip()
    for m in os.getenv("MUSIC_MIRRORS", "").split(",")
    if m.strip()
] or DEFAULT_MIRRORS

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# ───────────────────────── state ─────────────────────────

_CACHE: dict[str, dict] = {}
_LAST_USE: dict[int, float] = {}
_MIRROR_DOWN_UNTIL: dict[str, float] = {}
_DL_SEM = asyncio.Semaphore(2)
_HTTP: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _HTTP
    if _HTTP is None or _HTTP.is_closed:
        _HTTP = httpx.AsyncClient(
            headers=UA,
            follow_redirects=True,
            timeout=httpx.Timeout(45.0, connect=8.0),
        )
    return _HTTP


# ───────────────────────── helpers ─────────────────────────

def _clean(text) -> str:
    """Unescape HTML entities from APIs (&amp; etc). Escape again at display."""
    return html.unescape(str(text or "")).strip()


def _fmt_duration(seconds: int) -> str:
    m, s = divmod(int(seconds or 0), 60)
    return f"{m}:{s:02d}"


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


def _cache_put(uid: int, results: list[dict]) -> str:
    now = time.time()
    for t in [t for t, e in _CACHE.items() if now - e["ts"] > CACHE_TTL]:
        _CACHE.pop(t, None)
    while len(_CACHE) >= MAX_CACHE:
        _CACHE.pop(next(iter(_CACHE)))
    token = secrets.token_urlsafe(6)
    _CACHE[token] = {"uid": uid, "results": results, "ts": now, "busy": False}
    return token


def _cache_get(token: str) -> dict | None:
    e = _CACHE.get(token)
    if not e:
        return None
    if time.time() - e["ts"] > CACHE_TTL:
        _CACHE.pop(token, None)
        return None
    return e


# ───────────────────────── Saavn search ─────────────────────────

def _parse_saavn(data: dict) -> list[dict]:
    """Handles old (primaryArtists/downloadUrl[].link) and new
    (artists.primary[]/downloadUrl[].url) response shapes."""
    block = data.get("data") or {}
    results = block.get("results") if isinstance(block, dict) else block
    if not isinstance(results, list):
        return []

    songs = []
    for s in results[:MAX_RESULTS]:
        urls = s.get("downloadUrl") or []
        if not urls:
            continue
        audio = urls[-1].get("url") or urls[-1].get("link")
        if not audio:
            continue

        artist = s.get("primaryArtists")
        if not (isinstance(artist, str) and artist.strip()):
            primary = (s.get("artists") or {}).get("primary") or []
            artist = ", ".join(p.get("name", "") for p in primary if p.get("name"))

        try:
            duration = int(s.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0

        songs.append({
            "source": "saavn",
            "id": str(s.get("id", "")),
            "title": _clean(s.get("name")) or "Unknown Track",
            "artist": _clean(artist) or "Unknown Artist",
            "duration": duration,
            "audio_url": audio,
        })
    return songs


async def _query_mirror(base: str, query: str) -> list[dict]:
    """Returns songs (maybe empty). Raises only when the mirror is broken."""
    try:
        r = await _client().get(base, params={"query": query, "limit": MAX_RESULTS}, timeout=7.0)
        r.raise_for_status()
        songs = _parse_saavn(r.json())
        _MIRROR_DOWN_UNTIL.pop(base, None)
        return songs
    except Exception:
        _MIRROR_DOWN_UNTIL[base] = time.time() + MIRROR_BENCH
        raise


async def _search_saavn(query: str) -> list[dict]:
    now = time.time()
    live = [m for m in MIRRORS if _MIRROR_DOWN_UNTIL.get(m, 0) < now] or MIRRORS
    tasks = [asyncio.create_task(_query_mirror(m, query)) for m in live]
    try:
        for fut in asyncio.as_completed(tasks):
            try:
                songs = await fut
            except Exception:
                continue
            if songs:
                return songs
        return []
    finally:
        for t in tasks:
            t.cancel()


# ───────────────────────── YouTube (yt-dlp) ─────────────────────────

def _yt_search(query: str) -> list[dict]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
        "socket_timeout": 15,
    }
    cookies = _cookie_path()
    if cookies:
        opts["cookiefile"] = cookies

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{MAX_RESULTS}:{query}", download=False)
    except Exception as e:
        print(f"[Music] yt search failed: {e}")
        return []

    songs = []
    for e in (info or {}).get("entries") or []:
        if not e or not e.get("id"):
            continue
        dur = int(e.get("duration") or 0)
        if dur and dur > MAX_YT_SECONDS:
            continue
        songs.append({
            "source": "yt",
            "id": e["id"],
            "title": _clean(e.get("title")) or "Unknown Track",
            "artist": _clean(e.get("channel") or e.get("uploader")) or "Unknown Artist",
            "duration": dur,
        })
    return songs[:MAX_RESULTS]


def _yt_download(song: dict) -> str | None:
    os.makedirs(TEMP_DIR, exist_ok=True)
    stem = secrets.token_hex(6)
    opts = {
        # m4a first (Telegram plays it natively, no ffmpeg needed), then anything
        "format": "ba[ext=m4a]/ba[acodec^=mp4a]/ba/b",
        "outtmpl": os.path.join(TEMP_DIR, f"{stem}.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": MAX_BYTES,
        "socket_timeout": 20,
        "retries": 3,
        "match_filter": yt_dlp.utils.match_filter_func(f"duration < {MAX_YT_SECONDS}"),
    }
    cookies = _cookie_path()
    if cookies:
        opts["cookiefile"] = cookies

    url = f"https://www.youtube.com/watch?v={song['id']}"

    # YouTube randomly blocks individual clients ("page needs to be reloaded",
    # "format not available"), so try several until one works.
    for clients in (None, ["tv"], ["web_safari"], ["mweb"], ["android_vr"]):
        attempt = dict(opts)
        if clients:
            attempt["extractor_args"] = {"youtube": {"player_client": clients}}
        try:
            with yt_dlp.YoutubeDL(attempt) as ydl:
                ydl.download([url])
            if any(n.startswith(stem + ".") for n in os.listdir(TEMP_DIR)):
                break
        except Exception as e:
            print(f"[Music] yt download failed (clients={clients}): {e}")
            for n in os.listdir(TEMP_DIR):  # drop partial files before retrying
                if n.startswith(stem + "."):
                    _safe_remove(os.path.join(TEMP_DIR, n))

    for name in os.listdir(TEMP_DIR):
        if name.startswith(stem + "."):
            return os.path.join(TEMP_DIR, name)
    return None


# ───────────────────────── downloads ─────────────────────────

async def _download_http(song: dict) -> str | None:
    url = song.get("audio_url")
    if not url:
        return None

    os.makedirs(TEMP_DIR, exist_ok=True)
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
    if ext in ("", ".mp4"):
        ext = ".m4a"
    path = os.path.join(TEMP_DIR, f"{secrets.token_hex(6)}{ext}")

    try:
        total = 0
        async with _client().stream("GET", url) as r:
            r.raise_for_status()
            with open(path, "wb") as f:
                async for chunk in r.aiter_bytes(256 * 1024):
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError("file too large")
                    f.write(chunk)
        if total > 0:
            return path
    except Exception as e:
        print(f"[Music] direct download failed: {e}")

    _safe_remove(path)
    return None


async def _fetch(song: dict) -> str | None:
    if song["source"] == "yt":
        return await asyncio.to_thread(_yt_download, song)

    path = await _download_http(song)
    if path:
        return path

    # Saavn CDN failed -> try the same track on YouTube
    alt = await asyncio.to_thread(_yt_search, f"{song['title']} {song['artist']}")
    if alt:
        return await asyncio.to_thread(_yt_download, alt[0])
    return None


# ───────────────────────── handlers ─────────────────────────

async def music_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg or not update.effective_user:
        return

    query = " ".join(context.args).strip()[:100]
    if not query:
        await msg.reply_text(
            "<b>Goddess Music</b>\n\n"
            "<i>Usage:</i> <code>/music &lt;song title or artist&gt;</code>\n"
            "<i>Example:</i> <code>/music Starboy The Weeknd</code>",
            parse_mode="HTML",
        )
        return

    uid = update.effective_user.id
    now = time.time()
    wait = USER_COOLDOWN - (now - _LAST_USE.get(uid, 0))
    if wait > 0:
        await msg.reply_text(f"⏳ <i>Easy there, try again in {int(wait) + 1}s.</i>", parse_mode="HTML")
        return
    _LAST_USE[uid] = now
    if len(_LAST_USE) > 5000:
        _LAST_USE.clear()

    status = await msg.reply_text("🔎 <i>Searching music...</i>", parse_mode="HTML")

    results = await _search_saavn(query)
    if not results:
        await status.edit_text("🔎 <i>Checking YouTube...</i>", parse_mode="HTML")
        results = await asyncio.to_thread(_yt_search, query)

    if not results:
        await status.edit_text("❌ <i>No results found. Try another search query.</i>", parse_mode="HTML")
        return

    token = _cache_put(uid, results)

    rows = []
    for i, s in enumerate(results):
        tag = "🎵" if s["source"] == "saavn" else "▶️"
        text = f"{tag} {s['title']} — {s['artist']}"
        if len(text) > 60:
            text = text[:57] + "..."
        rows.append([InlineKeyboardButton(text, callback_data=f"mus_pick:{token}:{i}")])
    rows.append([InlineKeyboardButton("🗑 Close", callback_data=f"mus_close:{token}")])

    await status.edit_text(
        f"<b>🎵 Music Search</b>\n\n<b>Query:</b> <code>{html.escape(query)}</code>\n\n<i>Select a track:</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def music_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data:
        return

    parts = q.data.split(":")
    action = parts[0]
    token = parts[1] if len(parts) > 1 else ""
    entry = _cache_get(token)

    # only the person who searched can use the buttons
    if entry and entry["uid"] != q.from_user.id:
        await q.answer("This search isn't yours. Run /music yourself 🎧", show_alert=True)
        return

    if action == "mus_close":
        await q.answer()
        _CACHE.pop(token, None)
        try:
            await q.message.delete()
        except Exception:
            pass
        return

    if action != "mus_pick" or len(parts) < 3:
        await q.answer()
        return

    try:
        index = int(parts[2])
    except ValueError:
        await q.answer("Invalid selection.", show_alert=True)
        return

    if not entry:
        await q.answer("These results expired. Please search again.", show_alert=True)
        return
    if not 0 <= index < len(entry["results"]):
        await q.answer("Invalid track.", show_alert=True)
        return
    if entry["busy"]:
        await q.answer("Already getting your track 🎧")
        return

    entry["busy"] = True
    await q.answer()

    song = entry["results"][index]
    title = html.escape(song["title"])
    artist = html.escape(song["artist"])
    chat = update.effective_chat
    path = None

    try:
        await q.edit_message_text(
            f"⬇️ <i>Getting your selected track...</i>\n\n🎵 <b>{title}</b>\n👤 <b>{artist}</b>",
            parse_mode="HTML",
        )

        async with _DL_SEM:
            path = await _fetch(song)

        if not path:
            entry["busy"] = False
            await q.edit_message_text(
                "❌ <b>Couldn't retrieve this track.</b>\n\nTry another result.",
                parse_mode="HTML",
                reply_markup=q.message.reply_markup,
            )
            return

        if os.path.getsize(path) > MAX_BYTES:
            entry["busy"] = False
            await q.edit_message_text(
                "❌ <b>That file is too big for Telegram (50MB limit).</b>\n\nTry another result.",
                parse_mode="HTML",
                reply_markup=q.message.reply_markup,
            )
            return

        label = "Studio Audio" if song["source"] == "saavn" else "YouTube Audio"
        caption = (
            f"{header('GODDESS MUSIC')}\n\n"
            f"<b>🎵 {title}</b>\n"
            f"👤 <b>Artist:</b> {artist}\n"
            f"⏱ <b>Duration:</b> {_fmt_duration(song['duration'])} {DOT_DIVIDER} <b>{label}</b>"
        )

        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as f:
            if ext in (".mp3", ".m4a"):
                await chat.send_audio(
                    audio=f,
                    title=song["title"],
                    performer=song["artist"],
                    duration=song["duration"] or None,
                    caption=caption,
                    parse_mode="HTML",
                    read_timeout=120,
                    write_timeout=120,
                )
            else:
                # webm/opus etc. won't render as audio, send as a file instead
                await chat.send_document(
                    document=f,
                    filename=f"{song['title']}{ext}"[:100],
                    caption=caption,
                    parse_mode="HTML",
                    read_timeout=120,
                    write_timeout=120,
                )

        _CACHE.pop(token, None)
        try:
            await q.message.delete()
        except Exception:
            pass  # sent fine, just couldn't tidy up

    except Exception as e:
        print(f"[Music] pick failed: {e}")
        entry["busy"] = False
        try:
            await q.edit_message_text(
                "❌ <i>Something went wrong sending that track. Try another one.</i>",
                parse_mode="HTML",
                reply_markup=q.message.reply_markup,
            )
        except Exception:
            pass
    finally:
        _safe_remove(path)


def register(app: Application) -> None:
    os.makedirs(TEMP_DIR, exist_ok=True)
    _cleanup_stale()
    print(f"[Music] yt-dlp version: {yt_dlp.version.__version__} | cookies: {bool(_cookie_path())}")
    app.add_handler(CommandHandler("music", music_command))
    app.add_handler(CallbackQueryHandler(music_callback, pattern=r"^mus_(pick|close):"))
