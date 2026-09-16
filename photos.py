"""
Fetches an aesthetic photo to pair with a poem, using the Pexels API
(free, no cost, strong at moody/aesthetic photography).
"""

import os
import random

import requests

PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")
SEARCH_URL = "https://api.pexels.com/v1/search"


def get_matching_photo_url(query: str) -> str | None:
    """Returns a photo URL matching the query, or None if unavailable
    (missing key, no results, or a request error - callers should treat
    None as 'skip the photo, send the poem anyway')."""
    if not PEXELS_API_KEY:
        return None
    try:
        resp = requests.get(
            SEARCH_URL,
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": query, "per_page": 6, "orientation": "portrait"},
            timeout=15,
        )
        resp.raise_for_status()
        photos = resp.json().get("photos", [])
        if not photos:
            return None
        return random.choice(photos)["src"]["large"]
    except Exception:
        return None
