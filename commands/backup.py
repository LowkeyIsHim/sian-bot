"""
/backup - creator-only, DM-only. Sends a zip of every JSON data file the
bot keeps (access list, group settings, warnings, confessions, leaderboard,
rules, etc.) so a host wipe or migration doesn't lose everything.

Deliberately EXCLUDES secrets.env - a zip of your bot token and API key
sitting in a Telegram chat is a real leak risk (forwarding, synced
devices, a compromised account). Data files only.

To restore: unzip and upload the JSON files back into the folder ABOVE
bot_src/ (the same folder that holds app.py) using your host's file
manager, then restart.
"""

import glob
import io
import os
import zipfile
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

import access

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))        # .../bot_src/commands
_PERSISTENT_DIR = os.path.dirname(os.path.dirname(_THIS_DIR))  # folder above bot_src


async def backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        return  # silent - this contains sensitive data, only ever send in DM
    if not access.is_creator(update.effective_user.id):
        return  # silent

    files = sorted(glob.glob(os.path.join(_PERSISTENT_DIR, "*.json")))
    if not files:
        await update.message.reply_text("Nothing to back up yet - no data files exist.")
        return

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, arcname=os.path.basename(path))
    buffer.seek(0)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    buffer.name = f"goddess-backup-{stamp}.zip"

    names = "\n".join(f"• {os.path.basename(p)}" for p in files)
    await update.message.reply_document(
        document=buffer,
        caption=(
            f"🗄️ backup - {len(files)} file(s)\n\n{names}\n\n"
            "contains private data (including the confession log) - "
            "store it somewhere safe, don't forward it."
        ),
    )


def register(app) -> None:
    app.add_handler(CommandHandler("backup", backup))
