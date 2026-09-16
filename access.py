"""
Access control for the bot.

Tiers:
  - CREATOR: hardcoded, permanent, can't be revoked via commands
  - GRANTED: added via /access, removable via /revoke

Persistence:
  access_list.json is stored ONE FOLDER ABOVE this file (i.e. outside
  bot_src/). The launcher (app.py) re-downloads bot_src/ on every update,
  but never touches anything outside it - so this file survives restarts,
  crashes, and auto-updates without needing any external service.
"""

import json
import os

# This file lives inside bot_src/. Go one level up so the access list
# sits outside the folder that gets wiped/recreated on every update.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PERSISTENT_DIR = os.path.dirname(_THIS_DIR)
ACCESS_FILE = os.path.join(_PERSISTENT_DIR, "access_list.json")

# Hardcoded creator Telegram user IDs - fill these in.
# Get your own ID by messaging the bot /whoami once it's running.
CREATOR_IDS = {
    6546958276,  # replace with your Telegram user ID
    8856537163,  # replace with her Telegram user ID
}


def _load() -> dict:
    if not os.path.exists(ACCESS_FILE):
        return {"granted": []}
    with open(ACCESS_FILE, "r") as f:
        return json.load(f)


def _save(data: dict) -> None:
    with open(ACCESS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def init() -> None:
    """Kept for compatibility with main.py - nothing to fetch remotely,
    the file is just read locally on demand."""
    pass


def is_creator(user_id: int) -> bool:
    return user_id in CREATOR_IDS


def has_access(user_id: int) -> bool:
    if is_creator(user_id):
        return True
    data = _load()
    return user_id in data.get("granted", [])


def grant_access(user_id: int) -> bool:
    """Returns True if newly granted, False if already had access."""
    data = _load()
    if user_id in data.get("granted", []) or is_creator(user_id):
        return False
    data.setdefault("granted", []).append(user_id)
    _save(data)
    return True


def revoke_access(user_id: int) -> bool:
    """Returns True if revoked. Creators can never be revoked this way."""
    if is_creator(user_id):
        return False
    data = _load()
    granted = data.get("granted", [])
    if user_id in granted:
        granted.remove(user_id)
        data["granted"] = granted
        _save(data)
        return True
    return False


def list_access() -> dict:
    data = _load()
    return {
        "creators": list(CREATOR_IDS),
        "granted": data.get("granted", []),
    }
