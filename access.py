"""
Access control for the bot.

Tiers:
  - CREATOR: hardcoded (via CREATOR_IDS env var), permanent, has every
    permission everywhere - personal chat features AND group admin features.
  - GRANTED: added via /access, removable via /revoke. Lets someone use
    the bot's personal features (poems, stories, chat) in DM. Does NOT
    grant group admin powers - those are a completely separate tier.
  - GROUP_ADMIN: added via /gadmin, removable via /ungadmin. Lets someone
    use group-moderation features (tag-all, spam controls, etc.) in any
    group the bot is in. Having /access does not imply this, and having
    this does not imply /access - they're independent.

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

# Creator Telegram user IDs, read from the CREATOR_IDS environment variable
# (set in secrets.env) as a comma-separated list, e.g. "111111111,222222222".
# Get your own ID by messaging the bot /whoami once it's running.
def _parse_creator_ids() -> set[int]:
    raw = os.environ.get("CREATOR_IDS", "")
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


CREATOR_IDS = _parse_creator_ids()


def _load() -> dict:
    if not os.path.exists(ACCESS_FILE):
        return {"granted": [], "group_admins": []}
    with open(ACCESS_FILE, "r") as f:
        data = json.load(f)
    data.setdefault("granted", [])
    data.setdefault("group_admins", [])
    return data


def _save(data: dict) -> None:
    with open(ACCESS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def init() -> None:
    """Kept for compatibility with main.py - nothing to fetch remotely,
    the file is just read locally on demand."""
    pass


def is_creator(user_id: int) -> bool:
    return user_id in CREATOR_IDS


# ---------------------------------------------------------------------------
# Personal/DM access (poems, stories, chat)
# ---------------------------------------------------------------------------

def has_access(user_id: int) -> bool:
    if is_creator(user_id):
        return True
    data = _load()
    return user_id in data["granted"]


def grant_access(user_id: int) -> bool:
    """Returns True if newly granted, False if already had access."""
    data = _load()
    if user_id in data["granted"] or is_creator(user_id):
        return False
    data["granted"].append(user_id)
    _save(data)
    return True


def revoke_access(user_id: int) -> bool:
    """Returns True if revoked. Creators can never be revoked this way."""
    if is_creator(user_id):
        return False
    data = _load()
    if user_id in data["granted"]:
        data["granted"].remove(user_id)
        _save(data)
        return True
    return False


# ---------------------------------------------------------------------------
# Group admin access (tag-all, spam controls, moderation)
# ---------------------------------------------------------------------------

def is_group_admin(user_id: int) -> bool:
    if is_creator(user_id):
        return True
    data = _load()
    return user_id in data["group_admins"]


def grant_group_admin(user_id: int) -> bool:
    """Returns True if newly granted, False if already had it."""
    data = _load()
    if user_id in data["group_admins"] or is_creator(user_id):
        return False
    data["group_admins"].append(user_id)
    _save(data)
    return True


def revoke_group_admin(user_id: int) -> bool:
    """Returns True if revoked. Creators can never be revoked this way."""
    if is_creator(user_id):
        return False
    data = _load()
    if user_id in data["group_admins"]:
        data["group_admins"].remove(user_id)
        _save(data)
        return True
    return False


def list_access() -> dict:
    data = _load()
    return {
        "creators": list(CREATOR_IDS),
        "granted": data["granted"],
        "group_admins": data["group_admins"],
    }
