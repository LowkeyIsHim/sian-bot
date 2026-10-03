import asyncio
import html
import json
import os
import secrets
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

# Temporary in-memory result cache.
# token -> list of search results
MUSIC_RESULTS: dict[str, list[dict]] = {}

# Multi-mirror fallback list.
API_MIRRORS = [
    "https://saavn.dev/search/songs?query=",
    "https://jiosaavn-api-v3.vercel.app/search?query=",
    "https://saavn-api.vercel.app/search?query=",
]

def _search_music_api(query: str) -> list[dict]:
    """
    Search the music APIs.

    IMPORTANT:
    This function ONLY searches.
    It does not download anything until the user selects a result.
    """
    encoded_query = urllib.parse.quote(query)

    for mirror in API_MIRRORS:
        try:
            api_url = f"{mirror}{encoded_query}"

            req = urllib.request.Request(
                api_url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 "
                        "(Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36"
                    )
                },
            )

            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            results = data.get("data", {}).get("results", [])

            if not results:
                continue

            songs = []

            for song in results[:8]:
                download_urls = song.get("downloadUrl") or []

                if not download_urls:
                    continue

                images = song.get("image") or []

                best_thumb = None
                if images:
                    best_thumb = images[-1].get("url")

                # Keep the original provider URL.
                # We do NOT download it here.
                best_audio_url = download_urls[-1].get("url")

                if not best_audio_url:
                    continue

                songs.append(
                    {
                        "id": str(song.get("id", "")),
                        "title": song.get("name") or "Unknown Track",
                        "artist": song.get("primaryArtists") or "Unknown Artist",
                        "thumbnail": best_thumb,
                        "duration": int(song.get("duration") or 0),
                        "audio_url": best_audio_url,
                    }
                )

            if songs:
                return songs

        except Exception as e:
            print(f"[Music API] Search mirror failed: {mirror} -> {e}")
            continue

    return []


def _download_selected_song(song: dict) -> dict | None:
    """
    Download the exact song selected by the user.

    No FFmpeg processing or speed/pitch modification is performed.
    """
    os.makedirs(TEMP_DIR, exist_ok=True)

    song_id = song.get("id") or secrets.token_hex(8)

    # Try to preserve the provider's audio format.
    extension = ".mp3"

    audio_url = song.get("audio_url")

    if not audio_url:
        return None

    file_path = os.path.join(
        TEMP_DIR,
        f"{song_id}{extension}",
    )

    try:
        req = urllib.request.Request(
            audio_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"
                )
            },
        )

        with urllib.request.urlopen(req, timeout=60) as audio_resp:
            with open(file_path, "wb") as out_file:
                while True:
                    chunk = audio_resp.read(1024 * 1024)

                    if not chunk:
                        break

                    out_file.write(chunk)

        if not os.path.exists(file_path):
            return None

        if os.path.getsize(file_path) == 0:
            os.remove(file_path)
            return None

        return {
            **song,
            "filepath": file_path,
        }

    except Exception as e:
        print(f"[Music] Direct download failed: {e}")

        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass

        return None


def _download_ytdlp_fallback(query: str) -> dict | None:
    """
    Secondary fallback.

    No FFmpeg post-processing is used here.
    The selected media is downloaded in its available audio format.
    """
    os.makedirs(TEMP_DIR, exist_ok=True)

    cookie_path = None

    for path in [
        "cookies.txt",
        "../cookies.txt",
        os.path.join(os.getcwd(), "cookies.txt"),
    ]:
        if os.path.exists(path):
            cookie_path = path
            break

    yt_opts = {
        "format": "bestaudio/best",
        "outtmpl": f"{TEMP_DIR}/%(id)s.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extractor_args": {
            "youtube": {
                "player_client": [
                    "web_embedded",
                    "android",
                ],
            }
        },
    }

    if cookie_path:
        yt_opts["cookiefile"] = cookie_path

    search_query = (
        query
        if query.startswith("http")
        else f"ytsearch1:{query}"
    )

    try:
        with yt_dlp.YoutubeDL(yt_opts) as ydl:
            info = ydl.extract_info(
                search_query,
                download=True,
            )

            if not info:
                return None

            entry = (
                info["entries"][0]
                if "entries" in info
                and info["entries"]
                else info
            )

            if not entry:
                return None

            video_id = entry.get("id")

            matching = []

            if video_id:
                matching = [
                    path
                    for path in os.listdir(TEMP_DIR)
                    if path.startswith(f"{video_id}.")
                ]

            filepath = (
                os.path.join(TEMP_DIR, matching[0])
                if matching
                else None
            )

            if not filepath or not os.path.exists(filepath):
                return None

            return {
                "filepath": filepath,
                "title": entry.get(
                    "title",
                    "Unknown Track",
                ),
                "artist": (
                    entry.get("artist")
                    or entry.get("uploader")
                    or "Unknown Artist"
                ),
                "thumbnail": entry.get("thumbnail"),
                "duration": int(
                    entry.get("duration") or 0
                ),
                "yt_url": entry.get(
                    "webpage_url",
                    "",
                ),
            }

    except Exception as e:
        print(f"[Music] yt-dlp fallback failed: {e}")

    return None


async def music_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.message:
        return

    query = " ".join(context.args).strip()

    if not query:
        await update.message.reply_text(
            "<b>Goddess Music</b>\n\n"
            "<i>Usage:</i> "
            "<code>/music &lt;song title or artist&gt;</code>\n"
            "<i>Example:</i> "
            "<code>/music Starboy The Weeknd</code>",
            parse_mode="HTML",
        )
        return

    status_msg = await update.message.reply_text(
        "🔎 <i>Searching music...</i>",
        parse_mode="HTML",
    )

    results = await asyncio.to_thread(
        _search_music_api,
        query,
    )

    if not results:
        await status_msg.edit_text(
            "❌ <i>No results found.</i>\n\n"
            "Try using the song title and artist.",
            parse_mode="HTML",
        )
        return

    # Generate a short callback token.
    token = secrets.token_urlsafe(8)

    MUSIC_RESULTS[token] = results

    keyboard = []

    for index, song in enumerate(results):
        title = song["title"]
        artist = song["artist"]

        # Keep button text short enough for Telegram.
        button_text = f"🎵 {title} — {artist}"

        if len(button_text) > 60:
            button_text = button_text[:57] + "..."

        keyboard.append(
            [
                InlineKeyboardButton(
                    button_text,
                    callback_data=f"mus_pick:{token}:{index}",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "🗑 Close",
                callback_data=f"mus_close:{token}",
            )
        ]
    )

    reply_markup = InlineKeyboardMarkup(keyboard)

    await status_msg.edit_text(
        f"<b>🎵 Music Search</b>\n\n"
        f"<b>Query:</b> "
        f"<code>{html.escape(query)}</code>\n\n"
        f"<i>Select a track:</i>",
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


async def music_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if not query:
        return

    await query.answer()

    data = query.data or ""

    # -----------------------------
    # CLOSE
    # -----------------------------
    if data.startswith("mus_close:"):
        if query.message:
            await query.message.delete()

        token = data.split(":", 1)[1]
        MUSIC_RESULTS.pop(token, None)
        return

    # -----------------------------
    # PICK SONG
    # -----------------------------
    if not data.startswith("mus_pick:"):
        return

    try:
        _, token, index_str = data.split(":", 2)
        index = int(index_str)
    except (ValueError, TypeError):
        await query.answer(
            "Invalid selection.",
            show_alert=True,
        )
        return

    results = MUSIC_RESULTS.get(token)

    if not results:
        await query.answer(
            "These search results have expired. Search again.",
            show_alert=True,
        )
        return

    if index < 0 or index >= len(results):
        await query.answer(
            "Invalid track.",
            show_alert=True,
        )
        return

    song = results[index]

    title = html.escape(
        song.get("title", "Unknown Track")
    )

    artist = html.escape(
        song.get("artist", "Unknown Artist")
    )

    await query.edit_message_text(
        f"⬇️ <i>Getting your selected track...</i>\n\n"
        f"🎵 <b>{title}</b>\n"
        f"👤 <b>{artist}</b>",
        parse_mode="HTML",
    )

    downloaded = await asyncio.to_thread(
        _download_selected_song,
        song,
    )

    if not downloaded:
        await query.edit_message_text(
            "❌ <b>Couldn't retrieve this track.</b>\n\n"
            "Try selecting another result.",
            parse_mode="HTML",
        )
        return

    filepath = downloaded["filepath"]

    raw_duration = int(
        downloaded.get("duration") or 0
    )

    try:
        minutes, seconds = divmod(
            raw_duration,
            60,
        )

        duration_str = (
            f"{minutes}:{seconds:02d}"
        )

        caption = (
            f"{header('GODDESS MUSIC')}\n\n"
            f"<b>🎵 {title}</b>\n"
            f"👤 <b>Artist:</b> {artist}\n"
            f"⏱ <b>Duration:</b> "
            f"{duration_str} "
            f"{DOT_DIVIDER} "
            f"<b>Original Audio</b>"
        )

        with open(filepath, "rb") as audio_file:
            await query.message.reply_audio(
                audio=audio_file,
                title=downloaded.get(
                    "title",
                    "Unknown Track",
                ),
                performer=downloaded.get(
                    "artist",
                    "Unknown Artist",
                ),
                duration=raw_duration or None,
                caption=caption,
                parse_mode="HTML",
                read_timeout=120,
                write_timeout=120,
            )

        await query.message.delete()

    except Exception as e:
        print(
            f"[Music] Telegram upload failed: {e}"
        )

        await query.edit_message_text(
            "❌ <i>Audio was retrieved, "
            "but Telegram couldn't send it.</i>",
            parse_mode="HTML",
        )

    finally:
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception:
                pass

        MUSIC_RESULTS.pop(token, None)


def register(app: Application) -> None:
    app.add_handler(
        CommandHandler(
            "music",
            music_command,
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            music_callback,
            pattern=r"^mus_(pick|close):",
        )
            )
