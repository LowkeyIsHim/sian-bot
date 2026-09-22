"""
Builds and applies per-user, per-chat command menus (the ☰ list next to
the message box), so people only ever see commands they can actually use.
Telegram lets set_my_commands be scoped per-chat or per-member-of-a-chat,
which is what makes this possible.
"""

import logging

from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeChatMember
from telegram.error import TelegramError

import access

logger = logging.getLogger(__name__)

PUBLIC_COMMANDS = [
    BotCommand("start", "Say hello"),
    BotCommand("help", "See what I can do"),
    BotCommand("menu", "See what I can do"),
    BotCommand("whoami", "Get your Telegram ID"),
    BotCommand("developer", "Who built this bot"),
    BotCommand("confess", "Anonymously confess to an enabled group"),
]

PERSONAL_COMMANDS = [
    BotCommand("poem", "Ask for a poem"),
    BotCommand("story", "Ask for a short story"),
    BotCommand("aesthetic", "A short quote + matching photo"),
    BotCommand("reset", "Clear our conversation"),
]

CREATOR_DM_COMMANDS = [
    BotCommand("access", "Grant access"),
    BotCommand("revoke", "Revoke access"),
    BotCommand("listaccess", "List who has access"),
    BotCommand("confessionlog", "Private log of who sent each confession"),
    BotCommand("clearconfessionlog", "Wipe the confession log"),
]

GROUP_PUBLIC_COMMANDS = [
    BotCommand("gmenu", "Group tools overview"),
    BotCommand("listadmin", "List this group's admins"),
    BotCommand("roast", "Roast someone (reply to their message)"),
    BotCommand("tictactoe", "Challenge someone (reply to their message)"),
    BotCommand("rps", "Rock-paper-scissors (reply to their message)"),
    BotCommand("trivia", "Start a trivia round"),
    BotCommand("startwcg", "Start a word chain game"),
    BotCommand("endwcg", "End the word chain game"),
    BotCommand("report", "Report a message to admins (reply to it)"),
    BotCommand("userinfo", "See someone's profile (reply to their message)"),
    BotCommand("rules", "See this group's rules"),
    BotCommand("leaderboard", "See who's winning the games"),
]

GROUP_ADMIN_COMMANDS = [
    BotCommand("tagall", "Mention everyone in this group"),
    BotCommand("mute", "Mute a member (reply to their message)"),
    BotCommand("unmute", "Unmute a member (reply to their message)"),
    BotCommand("ban", "Ban a member (reply to their message)"),
    BotCommand("unban", "Unban a member by ID"),
    BotCommand("warn", "Warn a member (reply to their message)"),
    BotCommand("clearwarns", "Clear a member's warnings"),
    BotCommand("warnings", "Check a member's warning count"),
    BotCommand("purge", "Delete a range of messages (reply to the start)"),
    BotCommand("lockdown", "Toggle admins-only messaging"),
    BotCommand("setrules", "Set this group's rules"),
    BotCommand("approve", "Approve a pending join request"),
    BotCommand("decline", "Decline a pending join request"),
    BotCommand("antiflood", "Configure flood protection"),
    BotCommand("antilink", "Configure link protection"),
    BotCommand("antiword", "Configure banned words"),
]

CREATOR_GROUP_COMMANDS = [
    BotCommand("promote", "Promote a member (reply to their message)"),
    BotCommand("demote", "Demote a member (reply to their message)"),
    BotCommand("setconfessions", "Enable/disable confessions for this group"),
]


def _dedupe(commands: list[BotCommand]) -> list[BotCommand]:
    seen = set()
    result = []
    for c in commands:
        if c.command not in seen:
            seen.add(c.command)
            result.append(c)
    return result


async def refresh_private_menu(bot, user_id: int) -> None:
    """Sets the ☰ menu for one user's private chat with the bot."""
    commands = list(PUBLIC_COMMANDS)
    if access.is_creator(user_id):
        commands += PERSONAL_COMMANDS + CREATOR_DM_COMMANDS
    elif access.has_access(user_id):
        commands += PERSONAL_COMMANDS
    try:
        await bot.set_my_commands(_dedupe(commands), scope=BotCommandScopeChat(chat_id=user_id))
    except TelegramError as e:
        logger.warning(f"Could not set private menu for {user_id}: {e}")


async def refresh_group_menu(bot, chat_id: int, user_id: int) -> None:
    """Sets the ☰ menu for one specific member inside one specific group."""
    commands = list(PUBLIC_COMMANDS) + list(GROUP_PUBLIC_COMMANDS)
    if access.is_creator(user_id):
        commands += GROUP_ADMIN_COMMANDS + CREATOR_GROUP_COMMANDS
    elif access.is_group_admin(user_id):
        commands += GROUP_ADMIN_COMMANDS
    try:
        await bot.set_my_commands(
            _dedupe(commands), scope=BotCommandScopeChatMember(chat_id=chat_id, user_id=user_id)
        )
    except TelegramError as e:
        logger.warning(f"Could not set group menu for {user_id} in {chat_id}: {e}")
