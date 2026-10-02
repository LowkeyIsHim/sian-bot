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


def _download_audio_sync(query: str) -> dict | None:
    """Downloads audio using yt-dlp with iOS/Music client spoofing to bypass YouTube datacenter format blocks."""
    os.makedirs(TEMP_DIR, exist_ok=True)

    cookie_path = None
    if os.path.exists("cookies.txt"):
        cookie_path = "cookies.txt"
    elif os.path.exists("../cookies.txt"):
        cookie_path = "../cookies.txt"

    search_term = query if query.startswith("http") else f"ytsearch1:{query}"

    # Using ios / android_music clients bypasses datacenter format extraction blocks
    ydl_opts = {
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
                "player_client": ["ios", "android_music", "mweb"],
                "player_skip": ["configs", "webpage"],
            }
        },
    }

    if cookie_path:
        ydl_opts["cookiefile"] = cookie_path

    # Attempt 1: Standard download with configured options
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_term, download=True)
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
                            "duration": entry.get("duration", 0),
                            "yt_url": entry.get("webpage_url", f"https://www.youtube.com/watch?v={video_id}"),
                        }
    except Exception as e:
        print(f"[Music] Primary download failed: {e}")

    # Attempt 2: Fallback without cookiefile if cookies are expired or rejected
    if cookie_path and "cookiefile" in ydl_opts:
        del ydl_opts["cookiefile"]
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(search_term, download=True)
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
                                "duration": entry.get("duration", 0),
                                "yt_url": entry.get("webpage_url", f"https://www.youtube.com/watch?v={video_id}"),
                            }
        except Exception as e:
            print(f"[Music] Fallback download failed: {e}")

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

    minutes, seconds = divmod(song["duration"], 60)
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
            InlineKeyboardButton("🔴 YouTube", url=song["yt_url"]),
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
                duration=song["duration"],
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
