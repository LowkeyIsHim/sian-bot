import asyncio
import glob
import html
import json
import os
import urllib.parse
import urllib.request
import yt_dlp

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from branding import header, DOT_DIVIDER

TEMP_DIR = "temp_music"

# Multi-mirror fallback list to prevent single point of failure (Errno -2)
API_MIRRORS = [
    "https://jio-saavn-api-sigma.vercel.app/api/search/songs?query=",
    "https://saavn.me/api/search/songs?query=",
    "https://saavn.dev/api/search/songs?query=",
]


def _search_music_api(query: str) -> dict | None:
    """Tries multiple API mirrors to fetch 320kbps audio directly."""
    encoded_query = urllib.parse.quote(query)

    for mirror in API_MIRRORS:
        try:
            api_url = f"{mirror}{encoded_query}"
            req = urllib.request.Request(
                api_url, 
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))

                if data.get("success") and data.get("data", {}).get("results"):
                    song = data["data"]["results"][0]
                    download_urls = song.get("downloadUrl", [])

                    if not download_urls:
                        continue

                    # Select highest quality stream (320kbps)
                    best_audio_url = download_urls[-1]["url"]

                    images = song.get("image", [])
                    best_thumb = images[-1]["url"] if images else None

                    os.makedirs(TEMP_DIR, exist_ok=True)
                    file_path = os.path.join(TEMP_DIR, f"{song.get('id', 'track')}.mp3")

                    dl_req = urllib.request.Request(
                        best_audio_url, 
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                    )
                    with urllib.request.urlopen(dl_req, timeout=30) as audio_resp, open(file_path, "wb") as out_file:
                        out_file.write(audio_resp.read())

                    if os.path.exists(file_path):
                        return {
                            "filepath": file_path,
                            "title": song.get("name", "Unknown Track"),
                            "artist": song.get("primaryArtists") or "Unknown Artist",
                            "thumbnail": best_thumb,
                            "duration": int(song.get("duration") or 0),
                            "yt_url": f"https://open.spotify.com/search/{encoded_query}",
                        }
        except Exception as e:
            print(f"[Music API] Mirror {mirror} failed: {e}")
            continue

    return None


def _download_ytdlp_fallback(query: str) -> dict | None:
    """Secondary Fallback with embedded client fix to bypass YouTube reload errors."""
    os.makedirs(TEMP_DIR, exist_ok=True)

    cookie_path = None
    for path in ["cookies.txt", "../cookies.txt", os.path.join(os.getcwd(), "cookies.txt")]:
        if os.path.exists(path):
            cookie_path = path
            break

    yt_opts = {
        "format": "ba/b",
        "outtmpl": f"{TEMP_DIR}/%(id)s.%(ext)s",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["web_embedded", "android"],
            }
        },
    }

    if cookie_path:
        yt_opts["cookiefile"] = cookie_path

    search_query = query if query.startswith("http") else f"ytsearch1:{query}"

    try:
        with yt_dlp.YoutubeDL(yt_opts) as ydl:
            info = ydl.extract_info(search_query, download=True)
            if info:
                entry = info["entries"][0] if "entries" in info and info["entries"] else info
                if entry:
                    video_id = entry.get("id")
                    filepath = os.path.join(TEMP_DIR, f"{video_id}.mp3")
                    if not os.path.exists(filepath):
                        matching = glob.glob(os.path.join(TEMP_DIR, f"{video_id}.*"))
                        filepath = matching[0] if matching else None

                    if filepath and os.path.exists(filepath):
                        return {
                            "filepath": filepath,
                            "title": entry.get("title", "Unknown Track"),
                            "artist": entry.get("artist") or entry.get("uploader") or "Unknown Artist",
                            "thumbnail": entry.get("thumbnail"),
                            "duration": int(entry.get("duration") or 0),
                            "yt_url": entry.get("webpage_url", f"https://www.youtube.com/watch?v={video_id}"),
                        }
    except Exception as e:
        print(f"[Music] yt-dlp fallback failed: {e}")

    return None


def _get_audio_sync(query: str) -> dict | None:
    song = _search_music_api(query)
    if song:
        return song
    return _download_ytdlp_fallback(query)


async def music_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    query = " ".join(context.args) if context.args else ""
    if not query.strip():
        await update.message.reply_text(
            "<b>Goddess Music</b>\n\n"
            "<i>Usage:</i> <code>/music &lt;song title or artist&gt;</code>\n"
            "<i>Example:</i> <code>/music Starboy The Weeknd</code>",
            parse_mode="HTML",
        )
        return

    status_msg = await update.message.reply_text("<i>🎶 Fetching studio audio, please wait...</i>", parse_mode="HTML")

    song = await asyncio.to_thread(_get_audio_sync, query)

    if not song:
        await status_msg.edit_text("❌ <i>Failed to retrieve audio. Please try another search term.</i>", parse_mode="HTML")
        return

    await status_msg.delete()

    title = html.escape(song["title"])
    artist = html.escape(song["artist"])

    raw_duration = int(song.get("duration") or 0)
    minutes, seconds = divmod(raw_duration, 60)
    duration_str = f"{minutes}:{seconds:02d}"

    caption = (
        f"{header('GODDESS MUSIC')}\n\n"
        f"<b>🎵 {title}</b>\n"
        f"👤 <b>Artist:</b> {artist}\n"
        f"⏱ <b>Duration:</b> {duration_str} {DOT_DIVIDER} <b>Quality:</b> 320 kbps\n\n"
        f"<i>Full track attached below.</i>"
    )

    encoded_search = urllib.parse.quote(f"{song['title']} {song['artist']}")
    spotify_url = f"https://open.spotify.com/search/{encoded_search}"

    keyboard = [
        [
            InlineKeyboardButton("🟢 Spotify", url=spotify_url),
            InlineKeyboardButton("🔗 Track Link", url=song["yt_url"]),
        ],
        [InlineKeyboardButton("🗑 Close", callback_data="mus_close")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if song["thumbnail"]:
        await update.message.reply_photo(
            photo=song["thumbnail"],
            caption=caption,
            parse_mode="HTML",
            reply_markup=reply_markup,
            read_timeout=60,
            write_timeout=60,
        )
    else:
        await update.message.reply_text(
            text=caption,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )

    try:
        with open(song["filepath"], "rb") as audio_file:
            await update.message.reply_audio(
                audio=audio_file,
                title=song["title"],
                performer=song["artist"],
                duration=raw_duration,
                caption=f"🎧 <b>{title}</b>",
                parse_mode="HTML",
                read_timeout=120,
                write_timeout=120,
            )
    finally:
        if os.path.exists(song["filepath"]):
            os.remove(song["filepath"])


async def music_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await query.answer()
    if query.data == "mus_close" and query.message:
        await query.message.delete()


def register(app: Application) -> None:
    app.add_handler(CommandHandler("music", music_command))
    app.add_handler(CallbackQueryHandler(music_callback, pattern="^mus_close$"))
