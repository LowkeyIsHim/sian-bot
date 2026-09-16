"""
Wrapper around the Google Gemini API, using the Sian personality prompt.

Gemini's free tier has no credit card requirement and doesn't expire like a
one-time credit balance - it just resets daily. Good fit for a personal bot.
"""

import os
import requests

from personality import SYSTEM_PROMPT

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
MODEL = "gemini-3.5-flash"
API_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{MODEL}:generateContent?key={GEMINI_API_KEY}"
)

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

    resp = requests.post(API_URL, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    candidate = data["candidates"][0]
    if candidate.get("finishReason") == "MAX_TOKENS":
        print("[ai] WARNING: response was cut off by maxOutputTokens.")

    reply_text = candidate["content"]["parts"][0]["text"]

    history.append({"role": "model", "text": reply_text})
    return reply_text


def reset_history(chat_id: int) -> None:
    _conversations.pop(chat_id, None)
