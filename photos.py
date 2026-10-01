"""
Fetches an aesthetic photo to pair with a poem, using the Pexels API, then
grades it to match her channel's look: near-monochrome, soft contrast,
dark vignette, light film grain. Warm subjects (sunsets, golden hour) keep
most of their colour instead of being forced to black and white.
"""

import io
import os
import random
from collections import deque

import requests
from PIL import Image, ImageChops, ImageEnhance

PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")
SEARCH_URL = "https://api.pexels.com/v1/search"

MAX_WIDTH = 1080
POOL_SIZE = 20          # results requested per search
PICK_FROM = 12          # choose randomly among the top N
_recent_ids = deque(maxlen=40)  # don't reuse the same photo back to back

WARM_WORDS = ("sunset", "sunrise", "golden", "dawn", "dusk", "warm", "glow", "amber")
MOOD_SUFFIX = "moody black and white"


def _is_warm(query: str) -> bool:
    q = query.lower()
    return any(w in q for w in WARM_WORDS)


def _vignette(img: Image.Image, strength: float) -> Image.Image:
    """Darkens the edges so attention falls to the centre."""
    radial = Image.radial_gradient("L").resize(img.size)  # black centre -> white edges
    mask = radial.point(lambda p: int(255 * ((p / 255) ** 2.2) * strength))
    darker = ImageEnhance.Brightness(img).enhance(0.45)
    return Image.composite(darker, img, mask)


def _grain(img: Image.Image, amount: float) -> Image.Image:
    noise = Image.effect_noise(img.size, 22).convert("RGB")
    return Image.blend(img, ImageChops.overlay(img, noise), amount)


def _to_moody_aesthetic(image_bytes: bytes, warm: bool = False) -> io.BytesIO:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    if img.width > MAX_WIDTH:
        ratio = MAX_WIDTH / img.width
        img = img.resize((MAX_WIDTH, int(img.height * ratio)))

    if warm:
        img = ImageEnhance.Color(img).enhance(0.85)       # keep the sunset
        img = ImageEnhance.Contrast(img).enhance(1.08)
        img = ImageEnhance.Brightness(img).enhance(0.95)
        img = _vignette(img, 0.55)
    else:
        img = ImageEnhance.Color(img).enhance(0.10)       # near-monochrome
        img = ImageEnhance.Contrast(img).enhance(1.15)
        img = ImageEnhance.Brightness(img).enhance(0.92)
        img = _vignette(img, 0.80)
        img = _grain(img, 0.35)

    output = io.BytesIO()
    img.save(output, format="JPEG", quality=90)
    output.seek(0)
    output.name = "photo.jpg"
    return output


def _search(query: str) -> list[dict]:
    resp = requests.get(
        SEARCH_URL,
        headers={"Authorization": PEXELS_API_KEY},
        params={"query": query, "per_page": POOL_SIZE, "orientation": "portrait"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("photos", [])


def get_matching_photo(query: str) -> io.BytesIO | None:
    """Returns a graded photo (in-memory file, ready for reply_photo)
    matching the query, or None if unavailable - callers should treat None
    as 'skip the photo, send the poem anyway'.

    Tries the moody version of the query first, then the plain query, so a
    too-narrow search still finds something."""
    if not PEXELS_API_KEY:
        return None

    warm = _is_warm(query)
    attempts = [query] if warm else [f"{query} {MOOD_SUFFIX}", query]

    try:
        for q in attempts:
            photos = [p for p in _search(q) if p["id"] not in _recent_ids]
            if not photos:
                continue
            pick = random.choice(photos[:PICK_FROM])
            _recent_ids.append(pick["id"])

            image_resp = requests.get(pick["src"]["large"], timeout=20)
            image_resp.raise_for_status()
            return _to_moody_aesthetic(image_resp.content, warm=warm)
    except Exception:
        return None
    return None
