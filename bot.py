"""
Wires together the command modules, the freeform chat handler, and the
Telegram command menu (the ☰ button next to the message box).

Menus are now permission-scoped (see menus.py) rather than one flat list -
someone only sees the commands they can actually use.

To add a new feature: create a new file in commands/ (or commands/group/
for group-only features) with an async handler and a register(app)
function, add it to COMMAND_MODULES below, and add its BotCommand entry
to the relevant list in menus.py.

IMPORTANT: any passive MessageHandler registered with the SAME filter
(e.g. filters.ChatType.GROUPS) must use a DIFFERENT PTB handler group
number in its own register(app) call - PTB only runs one handler per
group per update, so identical filters in the same group silently mask
each other. member_tracker/flood_guard/link_guard/word_guard each use
their own group number (1-4) for exactly this reason.
"""

import logging
import os

from telegram.ext import Application

import access
import menu_ui
from menus import PUBLIC_COMMANDS, refresh_private_menu
from commands import (
    access_commands,
    aesthetic,
    chat,
    developer,
    poem,
    reset,
    start,
    story,
    whoami,
)
from commands.group import (
    admin_sync,
    confess,
    flood_guard,
    group_menu,
    link_guard,
    lockdown,
    join_requests,
    leaderboard,
    member_tracker,
    moderation,
    promote_commands,
    purge,
    report,
    roast,
    settings as group_settings,
    tagall,
    userinfo,
    verification,
    word_guard,
)
from commands.group.games import rps, tictactoe, trivia, word_chain

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Every command module registered here gets wired up automatically.
# Order matters only in that "chat" (the freeform catch-all) should stay last.
COMMAND_MODULES = [
    start,
    menu_ui,
    poem,
    story,
    aesthetic,
    reset,
    whoami,
    developer,
    access_commands,
    promote_commands,
    admin_sync,
    confess,
    roast,
    tictactoe,
    word_chain,
    rps,
    trivia,
    group_menu,
    tagall,
    moderation,
    purge,
    lockdown,
    report,
    userinfo,
    verification,
    join_requests,
    leaderboard,
    group_settings,
    flood_guard,
    link_guard,
    word_guard,
    member_tracker,
    chat,
]

BOT_SHORT_DESCRIPTION = "Poems, stories, and conversation - written the way Goddess would write them."
BOT_DESCRIPTION = (
    "I'm Goddess. I write poems and short stories the way I actually would - "
    "raw, imagery-heavy, and always finding a thread of resilience. "
    "Talk to me, or use /help to see what I can do."
)


async def _post_init(app: Application) -> None:
    """Runs once after the bot connects - sets the safe default menu,
    the profile text, and pre-seeds creators' private menus so they see
    everything immediately without needing to send /start first."""
    await app.bot.set_my_commands(PUBLIC_COMMANDS)  # default/fallback scope
    try:
        await app.bot.set_my_short_description(BOT_SHORT_DESCRIPTION)
        await app.bot.set_my_description(BOT_DESCRIPTION)
    except Exception:
        logger.exception("Could not set bot profile description (non-fatal)")

    for creator_id in access.CREATOR_IDS:
        await refresh_private_menu(app.bot, creator_id)


async def _error_handler(update, context) -> None:
    """Catches anything a specific handler didn't - so a weird/malformed
    update from someone trying to break the bot gets logged clearly
    instead of failing silently somewhere."""
    logger.error("Unhandled exception while processing an update", exc_info=context.error)


def build_app() -> Application:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).post_init(_post_init).build()

    for module in COMMAND_MODULES:
        module.register(app)

    app.add_error_handler(_error_handler)

    return app
