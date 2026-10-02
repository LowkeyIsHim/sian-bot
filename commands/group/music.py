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
    """Synchronous yt-dlp download helper run via asyncio.to_thread."""
    os.makedirs(TEMP_DIR, exist_ok=True)
    
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
        "default_search": "ytsearch1",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=True)
            if not info:
                return None

            # Handle search results list vs single video info
            if "entries" in info and len(info["entries"]) > 0:
                entry = info["entries"][0]
            else:
                entry = info

            video_id = entry.get("id")
            title = entry.get("title", "Unknown Track")
            artist = entry.get("artist") or entry.get("uploader") or "Unknown Artist"
            thumbnail = entry.get("thumbnail")
            duration = entry.get("duration", 0)

            # Locate the extracted MP3 file
            filepath = os.path.join(TEMP_DIR, f"{video_id}.mp3")
            if not os.path.exists(filepath):
                matching = glob.glob(os.path.join(TEMP_DIR, f"{video_id}.*"))
                filepath = matching[0] if matching else None

            if not filepath or not os.path.exists(filepath):
                return None

            return {
                "filepath": filepath,
                "title": title,
                "artist": artist,
                "thumbnail": thumbnail,
                "duration": duration,
                "yt_url": entry.get("webpage_url", f"https://www.youtube.com/watch?v={video_id}"),
            }
    except Exception as e:
        print(f"[Music] Download error: {e}")
        return None


async def music_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for /music <song or artist name> - full track downloader."""
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

    # Run blocking yt-dlp download in a separate thread
    song = await asyncio.to_thread(_download_audio_sync, query)

    if not song:
        await status_msg.edit_text("❌ <i>Failed to retrieve audio. Please try another search term.</i>", parse_mode="HTML")
        return

    await status_msg.delete()

    title = html.escape(song["title"])
    artist = html.escape(song["artist"])
    
    # Calculate duration
    minutes, seconds = divmod(song["duration"], 60)
    duration_str = f"{minutes}:{seconds:02d}"

    # Modern aesthetic caption
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

    # Send banner image with details
    if song["thumbnail"]:
        await update.message.reply_photo(
            photo=song["thumbnail"],
            caption=caption,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    else:
        await update.message.reply_text(
            text=caption,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )

    # Send full MP3 audio file into chat
    try:
        with open(song["filepath"], "rb") as audio_file:
            await update.message.reply_audio(
                audio=audio_file,
                title=song["title"],
                performer=song["artist"],
                duration=song["duration"],
                caption=f"🎧 <b>{title}</b>",
                parse_mode="HTML",
            )
    finally:
        # Always delete local temp MP3 file to keep host disk space clean
        if os.path.exists(song["filepath"]):
            os.remove(song["filepath"])


async def music_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles close button callback."""
    query = update.callback_query
    if not query:
        return

    await query.answer()
    if query.data == "mus_close" and query.message:
        await query.message.delete()


def register(app: Application) -> None:
    """Register music command handlers."""
    app.add_handler(CommandHandler("music", music_command))
    app.add_handler(CallbackQueryHandler(music_callback, pattern="^mus_close$"))
