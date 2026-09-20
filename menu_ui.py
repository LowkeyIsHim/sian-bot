"""
Button-driven /menu (and /help, same thing). Every button press edits the
SAME message in place - like moving through pages of one app - instead of
sending a new message each time.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, filters

from ai import reset_history
from branding import header

MAIN_TEXT = (
    f"{header('menu')}\n\n"
    "choose something below, or just talk to me like a person - "
    "no command needed for that."
)

POEMS_TEXT = (
    f"{header('poems')}\n\n"
    "send */poem* to get one, or */poem heartbreak* (or any theme) "
    "for something specific.\n\n"
    "each one comes with a photo to match."
)

STORIES_TEXT = (
    f"{header('stories')}\n\n"
    "send */story* for one, or */story home* (or any theme) "
    "for something specific."
)

AESTHETIC_TEXT = (
    f"{header('aesthetic')}\n\n"
    "send */aesthetic* for a short quote + a matching photo - "
    "quicker than a full poem, same vibe."
)

DEV_TEXT = (
    f"{header('the person behind this')}\n\n"
    "built by *lowkey*\n\n"
    "telegram: [message me here](https://t.me/Im_just_l0wkey)\n\n"
    "_reach out for bugs, feature ideas, or just to say hi._"
)


def _main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✒️ Poems", callback_data="m:poems"),
         InlineKeyboardButton("📖 Stories", callback_data="m:stories")],
        [InlineKeyboardButton("✨ Aesthetic", callback_data="m:aesthetic"),
         InlineKeyboardButton("🕊️ Reset chat", callback_data="m:reset")],
        [InlineKeyboardButton("🪶 Who am I", callback_data="m:whoami"),
         InlineKeyboardButton("🛠️ Developer", callback_data="m:dev")],
    ])


def _back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⟵ Back", callback_data="m:main")]])


async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        await update.message.reply_text("This is for DM - try /gmenu here instead.")
        return
    await update.message.reply_text(MAIN_TEXT, parse_mode="Markdown", reply_markup=_main_keyboard())


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action = query.data

    if action == "m:main":
        await query.edit_message_text(MAIN_TEXT, parse_mode="Markdown", reply_markup=_main_keyboard())
    elif action == "m:poems":
        await query.edit_message_text(POEMS_TEXT, parse_mode="Markdown", reply_markup=_back_keyboard())
    elif action == "m:stories":
        await query.edit_message_text(STORIES_TEXT, parse_mode="Markdown", reply_markup=_back_keyboard())
    elif action == "m:aesthetic":
        await query.edit_message_text(AESTHETIC_TEXT, parse_mode="Markdown", reply_markup=_back_keyboard())
    elif action == "m:dev":
        await query.edit_message_text(
            DEV_TEXT, parse_mode="Markdown", reply_markup=_back_keyboard(),
            disable_web_page_preview=True,
        )
    elif action == "m:whoami":
        text = f"{header('your id')}\n\nyour telegram id is:\n`{query.from_user.id}`"
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=_back_keyboard())
    elif action == "m:reset":
        reset_history(query.message.chat_id)
        text = f"{header('reset')}\n\nconversation cleared. starting fresh."
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=_back_keyboard())


def register(app) -> None:
    app.add_handler(CommandHandler("help", menu_cmd, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("menu", menu_cmd, filters=filters.ChatType.PRIVATE))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^m:"))
