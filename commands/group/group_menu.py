"""
/gmenu - button-driven overview of group tools. Same in-place-editing
navigation as the personal /menu. The auto-protection pages let you
toggle rules and cycle their action directly with buttons, no typing
needed for the common case (fine-tuning warn limits/mute minutes/words
still uses the text commands - see settings.py).
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

import access
from branding import header
from commands.group.settings import get_rule, set_enabled, cycle_action

MAIN_TEXT = f"{header('group tools')}\n\npick something below."

TAGALL_TEXT = (
    f"{header('tag all')}\n\n"
    "send */tagall* `[message]` to mention everyone who's posted here.\n\n"
    "group admin access needed."
)

MOD_TEXT = (
    f"{header('moderation')}\n\n"
    "*/mute* */unmute* */ban* */unban*\n"
    "_reply to someone's message to act on them_\n\n"
    "*/warn* */clearwarns* */warnings*\n"
    "_track and manage warnings_\n\n"
    "*/purge*\n"
    "_reply to a message, deletes from there to your command_\n\n"
    "*/lockdown*\n"
    "_toggle admins-only messaging - emergency switch_\n\n"
    "group admin access needed."
)

FUN_TEXT = (
    f"{header('fun')}\n\n"
    "*/roast*\n"
    "_reply to someone's message - blunt, dark, no mercy 💀_\n\n"
    "*/confess* `<text>`\n"
    "_DM me this one - posts anonymously here, no one will know it's you_\n\n"
    "*/report*\n"
    "_reply to a bad message, quietly flags it to admins_\n\n"
    "*/userinfo*\n"
    "_reply to someone to see their profile_\n\n"
    "open to everyone."
)

GAMES_TEXT = (
    f"{header('games')}\n\n"
    "*/tictactoe*\n"
    "_reply to someone's message to challenge them_\n\n"
    "*/startwcg* `[word]`\n"
    "_word chain game - each word starts with the last letter of the one before_\n"
    "*/endwcg* to stop\n\n"
    "*/leaderboard*\n"
    "_see who's winning_\n\n"
    "open to everyone."
)

PROTECT_TEXT = f"{header('auto-protection')}\n\nchoose a rule to configure."


def _main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏷️ Tag All", callback_data="g:tagall"),
         InlineKeyboardButton("👑 Admins", callback_data="g:admins")],
        [InlineKeyboardButton("🎭 Fun", callback_data="g:fun"),
         InlineKeyboardButton("🎮 Games", callback_data="g:games")],
        [InlineKeyboardButton("🔨 Moderation", callback_data="g:mod")],
        [InlineKeyboardButton("🛡️ Auto-Protection", callback_data="g:protect")],
    ])


def _back(target: str = "g:main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⟵ Back", callback_data=target)]])


def _protect_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌊 Antiflood", callback_data="g:rule:antiflood")],
        [InlineKeyboardButton("🔗 Antilink", callback_data="g:rule:antilink")],
        [InlineKeyboardButton("🚫 Antiword", callback_data="g:rule:antiword")],
        [InlineKeyboardButton("⟵ Back", callback_data="g:main")],
    ])


def _rule_text(kind: str, chat_id: int) -> str:
    rule = get_rule(chat_id, kind)
    lines = [
        header(kind), "",
        f"enabled: {'✅ yes' if rule['enabled'] else '❌ no'}",
        f"action: *{rule['action']}*",
    ]
    if rule["action"] == "warn":
        lines.append(f"warn limit: {rule['warn_limit']} _(/{kind} warnlimit <n> to change)_")
    if rule["action"] == "mute":
        lines.append(f"mute minutes: {rule['mute_minutes']} _(/{kind} muteminutes <n> to change)_")
    if kind == "antiword":
        words = rule.get("words", [])
        lines.append(f"words: {', '.join(words) if words else '(none - /antiword addword <word>)'}")
    return "\n".join(lines)


def _rule_keyboard(kind: str, chat_id: int) -> InlineKeyboardMarkup:
    rule = get_rule(chat_id, kind)
    toggle_label = "🔴 Turn off" if rule["enabled"] else "🟢 Turn on"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(toggle_label, callback_data=f"g:rule:{kind}:toggle")],
        [InlineKeyboardButton(f"action: {rule['action']} (tap to cycle)", callback_data=f"g:rule:{kind}:cycle")],
        [InlineKeyboardButton("⟵ Back", callback_data="g:protect")],
    ])


async def gmenu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("This only works inside a group.")
        return
    await update.message.reply_text(MAIN_TEXT, parse_mode="Markdown", reply_markup=_main_keyboard())


async def _describe_admin(member) -> str:
    user = member.user
    if user.username:
        return f"[@{user.username}](https://t.me/{user.username})"
    return f"[{user.first_name}](tg://user?id={user.id})"


async def gmenu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    is_privileged = access.is_creator(user_id) or access.is_group_admin(user_id)

    if data == "g:main":
        await query.edit_message_text(MAIN_TEXT, parse_mode="Markdown", reply_markup=_main_keyboard())
        return

    if data == "g:tagall":
        await query.edit_message_text(TAGALL_TEXT, parse_mode="Markdown", reply_markup=_back())
        return

    if data == "g:mod":
        await query.edit_message_text(MOD_TEXT, parse_mode="Markdown", reply_markup=_back())
        return

    if data == "g:fun":
        await query.edit_message_text(FUN_TEXT, parse_mode="Markdown", reply_markup=_back())
        return

    if data == "g:games":
        await query.edit_message_text(GAMES_TEXT, parse_mode="Markdown", reply_markup=_back())
        return

    if data == "g:admins":
        try:
            members = await context.bot.get_chat_administrators(chat_id)
            lines = [await _describe_admin(m) for m in members]
            text = f"{header('group admins')}\n\n" + "\n".join(f"• {line}" for line in lines)
        except TelegramError as e:
            text = f"Couldn't fetch admins: {e}"
        await query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=_back(), disable_web_page_preview=True
        )
        return

    if data == "g:protect":
        if not is_privileged:
            await query.answer("You don't have access to this.", show_alert=True)
            return
        await query.edit_message_text(PROTECT_TEXT, parse_mode="Markdown", reply_markup=_protect_keyboard())
        return

    if data.startswith("g:rule:"):
        if not is_privileged:
            await query.answer("You don't have access to this.", show_alert=True)
            return
        parts = data.split(":")  # ["g","rule",kind] or [...,"toggle"/"cycle"]
        kind = parts[2]
        if len(parts) >= 4:
            if parts[3] == "toggle":
                set_enabled(chat_id, kind, not get_rule(chat_id, kind)["enabled"])
            elif parts[3] == "cycle":
                cycle_action(chat_id, kind)
        await query.edit_message_text(
            _rule_text(kind, chat_id), parse_mode="Markdown", reply_markup=_rule_keyboard(kind, chat_id)
        )


def register(app) -> None:
    app.add_handler(CommandHandler("gmenu", gmenu))
    app.add_handler(CallbackQueryHandler(gmenu_callback, pattern="^g:"))
