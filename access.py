"""
Access control for the bot.

Tiers:
  - CREATOR: hardcoded, permanent, can't be revoked via commands
  - GRANTED: added via /access, removable via /revoke

Persistence:
  access_list.json is read from and written to directly on GitHub via the
  Contents API. This makes GitHub the source of truth for granted users,
  so access survives redeploys even if the panel wipes local disk and
  re-pulls the repo on every deploy.

Required env vars:
  GITHUB_TOKEN     - a GitHub Personal Access Token with repo contents write access
  GITHUB_REPO      - "username/reponame"
  GITHUB_BRANCH    - branch name, defaults to "main"
"""

import base64
import json
import os

import requests

CREATOR_IDS = {
    6546958276,  # replace with your Telegram user ID
    8856537163,  # replace with her Telegram user ID
}

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = os.environ["GITHUB_REPO"]  # e.g. "lowkey/sian-bot"
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
FILE_PATH = "access_list.json"

API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{FILE_PATH}"
HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}

# Local cache so every message doesn't hit the GitHub API - refreshed on
# every grant/revoke, and read fresh at startup.
_cache = {"granted": []}
_cache_sha = None


def _fetch_from_github() -> None:
    global _cache, _cache_sha
    resp = requests.get(API_URL, headers=HEADERS, params={"ref": GITHUB_BRANCH})
    if resp.status_code == 404:
        # File doesn't exist yet on GitHub - start empty, will be created on first write.
        _cache = {"granted": []}
        _cache_sha = None
        return
    resp.raise_for_status()
    data = resp.json()
    _cache_sha = data["sha"]
    content = base64.b64decode(data["content"]).decode("utf-8")
    _cache = json.loads(content)


def _push_to_github(data: dict) -> None:
    global _cache_sha
    content_b64 = base64.b64encode(
        json.dumps(data, indent=2).encode("utf-8")
    ).decode("utf-8")

    payload = {
        "message": "Update access list",
        "content": content_b64,
        "branch": GITHUB_BRANCH,
    }
    if _cache_sha:
        payload["sha"] = _cache_sha

    resp = requests.put(API_URL, headers=HEADERS, json=payload)
    resp.raise_for_status()
    _cache_sha = resp.json()["content"]["sha"]


def init() -> None:
    """Call once at bot startup to load the current access list from GitHub."""
    _fetch_from_github()


def is_creator(user_id: int) -> bool:
    return user_id in CREATOR_IDS


def has_access(user_id: int) -> bool:
    if is_creator(user_id):
        return True
    return user_id in _cache.get("granted", [])


def grant_access(user_id: int) -> bool:
    """Returns True if newly granted, False if already had access."""
    if is_creator(user_id) or user_id in _cache.get("granted", []):
        return False
    _cache.setdefault("granted", []).append(user_id)
    _push_to_github(_cache)
    return True


def revoke_access(user_id: int) -> bool:
    """Returns True if revoked. Creators can never be revoked this way."""
    if is_creator(user_id):
        return False
    granted = _cache.get("granted", [])
    if user_id in granted:
        granted.remove(user_id)
        _cache["granted"] = granted
        _push_to_github(_cache)
        return True
    return False


def list_access() -> dict:
    return {
        "creators": list(CREATOR_IDS),
        "granted": _cache.get("granted", []),
    }
