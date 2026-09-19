"""
Lightweight in-memory rate limiting to protect the bot (and the shared
Gemini quota) from spam or abuse. Resets on restart - that's fine, a
few seconds of fresh headroom after a deploy isn't a real risk.
"""

import time
from collections import defaultdict, deque

_hits: dict[tuple, deque] = defaultdict(deque)


def is_rate_limited(key, limit: int, window_seconds: float) -> bool:
    """Returns True if this call should be BLOCKED - the given key has
    already hit `limit` calls within the last `window_seconds`."""
    now = time.time()
    hits = _hits[key]
    hits.append(now)
    while hits and now - hits[0] > window_seconds:
        hits.popleft()
    return len(hits) > limit


def user_is_rate_limited(user_id: int, scope: str, limit: int, window_seconds: float) -> bool:
    return is_rate_limited((user_id, scope), limit, window_seconds)


def global_is_rate_limited(scope: str, limit: int, window_seconds: float) -> bool:
    return is_rate_limited(("__global__", scope), limit, window_seconds)
