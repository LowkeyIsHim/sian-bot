"""
Wires together the command modules, the freeform chat handler, and the
Telegram command menu (the ☰ button next to the message box).

To add a new feature: create a new file in commands/ with an async
handler and a register(app) function, add it to COMMAND_MODULES below,
and (if it should show in the ☰ menu) add it to MENU_COMMANDS too.
Nothing else needs to change.
"""

import logging
import os

from telegram import BotCommand
from telegram.ext import Application

from commands import (
    access_commands,
    chat,
    developer,
    help as help_cmd,
    poem,
    reset,
    start,
    story,
    whoami,
)
from commands.group import (
    flood_guard,
    group_admin_commands,
    group_menu,
    link_guard,
    member_tracker,
    moderation,
    settings as group_settings,
    tagall,
    word_guard,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Every command module registered here gets wired up automatically.
# Order matters only in that "chat" (the freeform catch-all) should stay last.
COMMAND_MODULES = [
    start,
    help_cmd,
    poem,
    story,
    reset,
    whoami,
    developer,
    access_commands,
    group_admin_commands,
    group_menu,
    tagall,
    moderation,
    group_settings,
    flood_guard,
    link_guard,
    word_guard,
    member_tracker,
    chat,
]

# Shown in Telegram's ☰ menu next to the message box.
MENU_COMMANDS = [
    BotCommand("start", "Say hello"),
    BotCommand("help", "See what I can do"),
    BotCommand("menu", "See what I can do"),
    BotCommand("poem", "Ask for a poem"),
    BotCommand("story", "Ask for a short story"),
    BotCommand("reset", "Clear our conversation"),
    BotCommand("whoami", "Get your Telegram ID"),
    BotCommand("developer", "Who built this bot"),
    BotCommand("access", "Grant access (creators only)"),
    BotCommand("revoke", "Revoke access (creators only)"),
    BotCommand("listaccess", "List who has access (creators only)"),
    BotCommand("tagall", "Mention everyone in a group (group admins only)"),
    BotCommand("gadmin", "Grant group admin access (creators only)"),
    BotCommand("ungadmin", "Revoke group admin access (creators only)"),
    BotCommand("gadminlist", "List group admins (creators only)"),
    BotCommand("gmenu", "Group tools overview"),
    BotCommand("mute", "Mute a member (reply to their message)"),
    BotCommand("unmute", "Unmute a member (reply to their message)"),
    BotCommand("ban", "Ban a member (reply to their message)"),
    BotCommand("unban", "Unban a member by ID"),
    BotCommand("warn", "Warn a member (reply to their message)"),
    BotCommand("clearwarns", "Clear a member's warnings"),
    BotCommand("warnings", "Check a member's warning count"),
    BotCommand("antiflood", "Configure flood protection (group admins only)"),
    BotCommand("antilink", "Configure link protection (group admins only)"),
    BotCommand("antiword", "Configure banned words (group admins only)"),
]

BOT_SHORT_DESCRIPTION = "Poems, stories, and conversation - written the way Goddess would write them."
BOT_DESCRIPTION = (
    "I'm Goddess. I write poems and short stories the way I actually would - "
    "raw, imagery-heavy, and always finding a thread of resilience. "
    "Talk to me, or use /help to see what I can do."
)


async def _post_init(app: Application) -> None:
    """Runs once after the bot connects - sets up the menu and profile text."""
    await app.bot.set_my_commands(MENU_COMMANDS)
    try:
        await app.bot.set_my_short_description(BOT_SHORT_DESCRIPTION)
        await app.bot.set_my_description(BOT_DESCRIPTION)
    except Exception:
        logger.exception("Could not set bot profile description (non-fatal)")


def build_app() -> Application:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).post_init(_post_init).build()

    for module in COMMAND_MODULES:
        module.register(app)

    return app
