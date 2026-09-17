"""
Fetches an aesthetic photo to pair with a poem, using the Pexels API
(free, no cost, strong at moody/aesthetic photography), then converts it
to black-and-white to match her channel's consistent desaturated look.
"""

import io
import os
import random

import requests
from PIL import Image, ImageEnhance

PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")
SEARCH_URL = "https://api.pexels.com/v1/search"


def _to_moody_aesthetic(image_bytes: bytes) -> io.BytesIO:
    """Mutes a photo's colors and boosts contrast slightly - matching her
    actual range (warm sunsets, near-monochrome portraits, etc.) rather
    than forcing every photo into pure black-and-white."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = ImageEnhance.Color(img).enhance(0.45)       # mute saturation, keep some color
    img = ImageEnhance.Contrast(img).enhance(1.12)    # slightly moodier contrast
    img = ImageEnhance.Brightness(img).enhance(0.96)  # a touch darker/softer
    output = io.BytesIO()
    img.save(output, format="JPEG", quality=90)
    output.seek(0)
    output.name = "photo.jpg"
    return output


def get_matching_photo(query: str) -> io.BytesIO | None:
    """Returns a black-and-white photo (as an in-memory file, ready to pass
    straight to Telegram's reply_photo) matching the query, or None if
    unavailable - callers should treat None as 'skip the photo, send the
    poem anyway'."""
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
        photo_url = random.choice(photos)["src"]["large"]

        image_resp = requests.get(photo_url, timeout=20)
        image_resp.raise_for_status()
        return _to_moody_aesthetic(image_resp.content)
    except Exception:
        return None
