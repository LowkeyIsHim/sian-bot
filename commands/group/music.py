import asyncio
import glob
import html
import os
import urllib.parse
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

# Keywords used by fan uploaders for pitched/altered tracks on SoundCloud
BAD_KEYWORDS = ["sped up", "slowed", "reverb", "pitch", "nightcore", "edit", "8d", "boosted"]


def _is_clean_track(title: str) -> bool:
    """Checks if a track title contains unwanted edit indicators."""
    title_lower = title.lower()
    return not any(kw in title_lower for kw in BAD_KEYWORDS)


def _download_audio_sync(query: str) -> dict | None:
    """Downloads audio using yt-dlp with YouTube Music priority and clean SoundCloud fallback."""
    os.makedirs(TEMP_DIR, exist_ok=True)

    cookie_path = None
    for path in ["cookies.txt", "../cookies.txt", os.path.join(os.getcwd(), "cookies.txt")]:
        if os.path.exists(path):
            cookie_path = path
            break

    # Strategy 1: YouTube Music Search (Guarantees Official Audio)
    yt_opts = {
        "format": "bestaudio/best",
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
                "player_client": ["mweb", "ios", "android"],
            }
        },
    }

    if cookie_path:
        yt_opts["cookiefile"] = cookie_path

    search_queries = [
        query if query.startswith("http") else f"ytmusicsearch1:{query}",
        query if query.startswith("http") else f"ytsearch1:{query} official audio",
    ]

    for search_q in search_queries:
        try:
            with yt_dlp.YoutubeDL(yt_opts) as ydl:
                info = ydl.extract_info(search_q, download=True)
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
            print(f"[Music] YouTube query '{search_q}' failed: {e}")

    # Strategy 2: SoundCloud Fallback (Filters out pitched/edited tracks)
    sc_opts = {
        "format": "bestaudio/best",
        "outtmpl": f"{TEMP_DIR}/%(id)s.%(ext)s",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }

    try:
        sc_query = query if query.startswith("http") else f"scsearch5:{query}"
        with yt_dlp.YoutubeDL(sc_opts) as ydl:
            info = ydl.extract_info(sc_query, download=False)
            if info and "entries" in info:
                # Pick the first result that isn't a fan edit/sped-up track
                selected_entry = None
                for entry in info["entries"]:
                    if entry and _is_clean_track(entry.get("title", "")):
                        selected_entry = entry
                        break

                if not selected_entry and info["entries"]:
                    selected_entry = info["entries"][0]

                if selected_entry:
                    dl_info = ydl.extract_info(selected_entry["webpage_url"], download=True)
                    track_id = dl_info.get("id")
                    filepath = os.path.join(TEMP_DIR, f"{track_id}.mp3")
                    if not os.path.exists(filepath):
                        matching = glob.glob(os.path.join(TEMP_DIR, f"{track_id}.*"))
                        filepath = matching[0] if matching else None

                    if filepath and os.path.exists(filepath):
                        return {
                            "filepath": filepath,
                            "title": dl_info.get("title", "Unknown Track"),
                            "artist": dl_info.get("uploader") or "Unknown Artist",
                            "thumbnail": dl_info.get("thumbnail"),
                            "duration": int(dl_info.get("duration") or 0),
                            "yt_url": dl_info.get("webpage_url", "https://soundcloud.com"),
                        }
    except Exception as e:
        print(f"[Music] SoundCloud strategy failed: {e}")

    return None


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

    status_msg = await update.message.reply_text("<i>🎶 Downloading full track, please wait...</i>", parse_mode="HTML")

    song = await asyncio.to_thread(_download_audio_sync, query)

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
        f"⏱ <b>Duration:</b> {duration_str} {DOT_DIVIDER} <b>Quality:</b> 192 kbps\n\n"
        f"<i>Full track attached below.</i>"
    )

    encoded_search = urllib.parse.quote(f"{song['title']} {song['artist']}")
    spotify_url = f"https://open.spotify.com/search/{encoded_search}"

    keyboard = [
        [
            InlineKeyboardButton("🟢 Spotify", url=spotify_url),
            InlineKeyboardButton("🔴 Link", url=song["yt_url"]),
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
