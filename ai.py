"""
Wrapper around the Google Gemini API, using the Sian personality prompt.

Gemini's free tier has no credit card requirement and doesn't expire like a
one-time credit balance - it just resets daily. Good fit for a personal bot.
"""

import os
import time
import requests

from personality import SYSTEM_PROMPT

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
MODEL = "gemini-3.5-flash"
API_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{MODEL}:generateContent?key={GEMINI_API_KEY}"
)


class RateLimitError(Exception):
    """Raised when Gemini returns a 429 (too many requests) after retries
    are exhausted - lets command handlers show a friendlier message."""
    pass


def _post_with_retry(payload: dict, retries: int = 2, backoff: float = 3.0) -> dict:
    """POSTs to the Gemini API, retrying briefly on 429s (rate limits
    are usually per-minute and clear up fast) before giving up."""
    for attempt in range(retries + 1):
        resp = requests.post(API_URL, json=payload, timeout=60)
        if resp.status_code == 429:
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            raise RateLimitError("Gemini rate limit hit after retries")
        resp.raise_for_status()
        return resp.json()

# Keeps a short rolling conversation per chat_id so replies stay in context.
_conversations: dict[int, list[dict]] = {}
MAX_HISTORY = 15  # messages kept per chat (user+assistant combined)


def _get_history(chat_id: int) -> list[dict]:
    return _conversations.setdefault(chat_id, [])


def ask_sian(chat_id: int, user_message: str) -> str:
    history = _get_history(chat_id)
    history.append({"role": "user", "text": user_message})

    if len(history) > MAX_HISTORY:
        del history[: len(history) - MAX_HISTORY]

    # Gemini's roles are "user" and "model" (not "assistant")
    contents = [
        {
            "role": "user" if turn["role"] == "user" else "model",
            "parts": [{"text": turn["text"]}],
        }
        for turn in history
    ]

    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": contents,
        "generationConfig": {
            # Sweet spot for creative/poetic writing: high enough for voice
            # and imagery to feel alive, not so high it goes incoherent.
            "temperature": 1.0,
            "topP": 0.95,
            # Generous budget - on 3.5-tier models, internal "thinking"
            # tokens are drawn from this same pool before the visible reply
            # is written, so a low limit here can truncate the actual poem.
            "maxOutputTokens": 2048,
        },
    }

    data = _post_with_retry(payload)

    candidate = data["candidates"][0]
    if candidate.get("finishReason") == "MAX_TOKENS":
        print("[ai] WARNING: response was cut off by maxOutputTokens.")

    reply_text = candidate["content"]["parts"][0]["text"]

    history.append({"role": "model", "text": reply_text})
    return reply_text


def reset_history(chat_id: int) -> None:
    _conversations.pop(chat_id, None)


TITLE_PREFIX = "TITLE:"


def split_title(text: str) -> tuple[str | None, str]:
    """If the reply starts with a 'TITLE: ...' line, splits it off and
    returns (title, remaining_body). Otherwise returns (None, text)."""
    if text.startswith(TITLE_PREFIX):
        first_line, _, rest = text.partition("\n")
        title = first_line[len(TITLE_PREFIX):].strip()
        body = rest.lstrip("\n")
        return title, body
    return None, text


def get_short_quote() -> str:
    """One-off call: a short standalone quote (1-2 lines, not a full poem)
    in her voice, for /aesthetic."""
    prompt = (
        "Write ONE short, standalone quote (1-2 lines only, not a full "
        "poem) in your voice - the kind of caption that would sit under "
        "an aesthetic photo on your channel. No title, no preamble, just "
        "the quote itself."
    )
    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 1.0, "topP": 0.95, "maxOutputTokens": 2048},
    }
    data = _post_with_retry(payload)
    candidate = data["candidates"][0]
    if candidate.get("finishReason") == "MAX_TOKENS":
        print("[ai] WARNING: get_short_quote response was cut off by maxOutputTokens.")
    return candidate["content"]["parts"][0]["text"].strip()


ROAST_PROMPT = """You are generating a comedic "roast battle" style burn - blunt, dark, savage, burn the kitchen, funny-mean. This is NOT in Goddess's usual poetic voice - drop the poetry entirely.

STRICT LENGTH RULE: Maximum ONE, at most TWO sentences. This is a quick burn, not a comedy routine, not a paragraph, not a list of separate jokes. Sharp line that lands hard beats three medium ones. If you're tempted to write more than two sentences, cut it down instead.

Hard limits, never cross these:
- No slurs, no attacks based on race, ethnicity, religion, gender, sexual orientation, disability, or any protected characteristic.
- No real threats of violence, no content sexualizing anyone, no targeting appearance in a way that promotes body-shaming as a serious message (jokes about it in a roast-battle context are fine, cruelty as if meant to actually wound someone is not).
- This is comedy between people who are in on the joke, not real harassment. Stay in "roast battle" territory, not "genuine abuse" territory.

Write ONE savage, funny burn (1-2 sentences max) roasting the person named below. Blunt, dark humor, no poetic imagery, no softness, no redemptive turn, no preamble, no "here's a roast for you" - just the burn itself and nothing else."""


def get_roast(target_name: str) -> str:
    """One-off call, completely separate persona/prompt from the main
    Goddess voice - deliberately blunt and unpoetic, for /roast."""
    prompt = f"Roast this person: {target_name}"
    payload = {
        "system_instruction": {"parts": [{"text": ROAST_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 1.05, "topP": 0.95, "maxOutputTokens": 2048},
    }
    data = _post_with_retry(payload)
    candidate = data["candidates"][0]
    if candidate.get("finishReason") == "MAX_TOKENS":
        print("[ai] WARNING: get_roast response was cut off by maxOutputTokens.")
    return candidate["content"]["parts"][0]["text"].strip()


def get_image_search_phrase(poem_text: str) -> str:
    """One-off call (not part of the ongoing conversation) that reads a
    finished poem and returns a short aesthetic photo search phrase to
    pair with it - e.g. 'moody rain window aesthetic'."""
    prompt = (
        "Read this poem and output ONLY a short photo search phrase "
        "(3-6 words, no punctuation, no explanation) describing the kind "
        "of moody, soft, aesthetic photograph that would pair well with "
        "it on a poetry page - think solitary figures, quiet interiors, "
        "melancholic natural light, muted tones. Avoid party, nightlife, "
        "drinking, or overtly social/upbeat imagery, even if the poem "
        "mentions something adjacent - keep the mood reflective and "
        "solitary.\n\nPoem:\n" + poem_text
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 30},
    }
    try:
        resp = requests.post(API_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        return "moody aesthetic soft light"
