"""
Builds and applies per-user, per-chat command menus (the ☰ list next to
the message box), so people only ever see commands they can actually use.
Telegram lets set_my_commands be scoped per-chat or per-member-of-a-chat,
which is what makes this possible.
"""

from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeChatMember
from telegram.error import TelegramError

import access

PUBLIC_COMMANDS = [
    BotCommand("start", "Say hello"),
    BotCommand("help", "See what I can do"),
    BotCommand("menu", "See what I can do"),
    BotCommand("whoami", "Get your Telegram ID"),
    BotCommand("developer", "Who built this bot"),
]

PERSONAL_COMMANDS = [
    BotCommand("poem", "Ask for a poem"),
    BotCommand("story", "Ask for a short story"),
    BotCommand("reset", "Clear our conversation"),
]

CREATOR_DM_COMMANDS = [
    BotCommand("access", "Grant access"),
    BotCommand("revoke", "Revoke access"),
    BotCommand("listaccess", "List who has access"),
]

GROUP_PUBLIC_COMMANDS = [
    BotCommand("gmenu", "Group tools overview"),
    BotCommand("listadmin", "List this group's admins"),
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
    BotCommand("antiflood", "Configure flood protection"),
    BotCommand("antilink", "Configure link protection"),
    BotCommand("antiword", "Configure banned words"),
]

CREATOR_GROUP_COMMANDS = [
    BotCommand("promote", "Promote a member (reply to their message)"),
    BotCommand("demote", "Demote a member (reply to their message)"),
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
    except TelegramError:
        pass


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
    except TelegramError:
        pass
