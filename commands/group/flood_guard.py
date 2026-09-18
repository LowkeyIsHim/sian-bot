"""
Passive flood detection: if someone sends 10+ messages within 10 seconds
in a group, the configured antiflood action kicks in. In-memory only -
losing the counters on a restart is fine, they rebuild within seconds.
"""

import time
from collections import defaultdict, deque

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from commands.group.enforcement import enforce, is_exempt
from commands.group.settings import get_rule

FLOOD_WINDOW_SECONDS = 10
FLOOD_LIMIT = 10

# (chat_id, user_id) -> deque of message timestamps
_recent: dict[tuple[int, int], deque] = defaultdict(deque)


async def _check_flood(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or chat.type not in ("group", "supergroup") or user is None or user.is_bot:
        return

    rule = get_rule(chat.id, "antiflood")
    if not rule["enabled"]:
        return

    if await is_exempt(context, chat.id, user.id):
        return

    key = (chat.id, user.id)
    now = time.time()
    timestamps = _recent[key]
    timestamps.append(now)

    while timestamps and now - timestamps[0] > FLOOD_WINDOW_SECONDS:
        timestamps.popleft()

    if len(timestamps) >= FLOOD_LIMIT:
        timestamps.clear()
        await enforce(
            context, chat.id, user.id, update.message.message_id,
            "antiflood", rule, f"sending {FLOOD_LIMIT}+ messages in {FLOOD_WINDOW_SECONDS}s",
        )


def register(app) -> None:
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, _check_flood), group=2)
