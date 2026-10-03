"""commands/group/music_menu.py

The Music category inside /gmenu, plus the pieces that make /music a button:

  * 🎵 Music page in /gmenu (everyone): how it works + "Find a song" button.
    The button asks for the song name with a reply prompt, then runs the
    normal /music request - same access rules, cooldown and card.
  * Admins and creators also see button controls on that page:
        Access mode (tap to cycle: selected -> everyone -> off)
        Library list, and the list of members allowed to request music.
    Allowing / removing a specific member or adding library songs still
    uses /music allow | disallow | add (they need a reply, so no button).
  * /song works as a shortcut for /music.

Nothing in music.py is changed - this module only calls into it.
Handler group used: -1 (catches replies to the "Find a song" prompt, then
stops, so the freeform chat handler doesn't also answer the song name).
"""

import html
from types import SimpleNamespace

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from branding import BOT_NAME, LINE
from commands.group import music

PROMPT_CAP = 100
MODE_LABEL = {
    "selected": "selected members only",
    "everyone": "everyone",
    "off": "off",
}


def _title(sub: str) -> str:
    return f"✦{LINE}✦\n{BOT_NAME}\n<i>{html.escape(sub)}</i>\n✦{LINE}✦"


def _back(target: str = "g:music") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⟵ Back", callback_data=target)]])


# ---------------------------------------------------------------- pages
def _main_page(chat_id: int, admin: bool):
    lines = [
        _title("music"),
        "",
        "<b>/music</b> <code>song name</code>",
        "<b>/music</b> <code>artist - song</code>",
        "",
        "<i>a now-playing card for the song. tap ▶ Play and the full audio drops "
        "right under it. ⏮ ⏭ browse the other matches, ✕ closes it.</i>",
    ]
    rows = [[InlineKeyboardButton("🔎 Find a song", callback_data="g:music:ask")]]

    if admin:
        conf = music._conf(music._load(), chat_id)
        songs = len(music._lib_load())
        allowed = len(conf["allowed"])
        lines += [
            "",
            f"access: <b>{MODE_LABEL.get(conf['mode'], conf['mode'])}</b>",
            f"allowed members: {allowed}",
            f"library: {songs} song(s)",
        ]
        rows.append(
            [InlineKeyboardButton(f"🔐 Access: {conf['mode']} (tap to change)", callback_data="g:music:mode")]
        )
        rows.append(
            [
                InlineKeyboardButton(f"📚 Library ({songs})", callback_data="g:music:lib"),
                InlineKeyboardButton(f"👥 Allowed ({allowed})", callback_data="g:music:users"),
            ]
        )

    rows.append([InlineKeyboardButton("⟵ Back", callback_data="g:main")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _library_page():
    tracks = music._lib_load()
    lines = [_title("library"), ""]
    if not tracks:
        lines.append("<i>empty.</i>")
    else:
        for e in tracks[-25:]:
            who = f" — {html.escape(e['artist'])}" if e.get("artist") else ""
            lines.append(f"{e['id']}. {html.escape(e['title'])}{who}")
        if len(tracks) > 25:
            lines.append(f"<i>latest 25 of {len(tracks)}</i>")
    lines += [
        "",
        "<i>add: reply to an audio file with</i> <code>/music add</code>",
        "<i>remove:</i> <code>/music remove id</code>",
    ]
    return "\n".join(lines), _back()


def _users_page(chat_id: int):
    conf = music._conf(music._load(), chat_id)
    lines = [_title("allowed members"), ""]
    if not conf["allowed"]:
        lines.append("<i>nobody on the list yet.</i>")
    else:
        lines += [f"• {html.escape(n)}" for n in conf["allowed"].values()]
    lines += [
        "",
        f"access mode: <b>{MODE_LABEL.get(conf['mode'], conf['mode'])}</b>",
        "<i>allow / remove: reply to a member with</i> <code>/music allow</code> "
        "<i>or</i> <code>/music disallow</code>",
    ]
    return "\n".join(lines), _back()


async def _edit(q, page) -> None:
    text, kb = page
    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except TelegramError:
        pass  # "message is not modified" etc. - nothing to do


# ---------------------------------------------------------------- /gmenu button handling
async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called by gmenu_callback for every 'g:music...' button. Answers the
    callback itself (so it can show alerts)."""
    q = update.callback_query
    data = q.data
    chat_id = q.message.chat_id
    user = q.from_user
    admin = music._privileged(user.id)

    if data == "g:music":
        await q.answer()
        await _edit(q, _main_page(chat_id, admin))
        return

    if data == "g:music:ask":
        ok, reason = music._check_access(chat_id, user.id)
        if not ok:
            await q.answer(reason, show_alert=True)
            return
        await q.answer()
        mention = f'<a href="tg://user?id={user.id}">{html.escape(user.first_name or "you")}</a>'
        try:
            prompt = await q.message.reply_text(
                f"🎵 {mention}, reply to this message with a song name.\n"
                "<i>tip: artist - song is the most accurate</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=ForceReply(selective=True, input_field_placeholder="song or artist - song"),
            )
        except TelegramError:
            return
        prompts = context.bot_data.setdefault("music_prompts", {})
        prompts[f"{chat_id}:{prompt.message_id}"] = {"user": user.id}
        while len(prompts) > PROMPT_CAP:
            prompts.pop(next(iter(prompts)))
        return

    # everything below is admin-only
    if not admin:
        await q.answer("Admins only.", show_alert=True)
        return
    await q.answer()

    if data == "g:music:mode":
        store = music._load()
        conf = music._conf(store, chat_id)
        i = music.MODES.index(conf["mode"]) if conf["mode"] in music.MODES else 0
        conf["mode"] = music.MODES[(i + 1) % len(music.MODES)]
        music._save(store)
        await _edit(q, _main_page(chat_id, admin))
    elif data == "g:music:lib":
        await _edit(q, _library_page())
    elif data == "g:music:users":
        await _edit(q, _users_page(chat_id))


# ---------------------------------------------------------------- reply to the "Find a song" prompt
async def on_prompt_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    m = update.effective_message
    replied = m.reply_to_message
    if not replied or not m.text or not m.from_user:
        return
    prompts = context.bot_data.get("music_prompts", {})
    key = f"{m.chat_id}:{replied.message_id}"
    prompt = prompts.get(key)
    if not prompt or prompt["user"] != m.from_user.id:
        return  # not ours - let every other handler see it

    prompts.pop(key, None)
    try:
        await replied.delete()
    except TelegramError:
        pass

    # run the normal /music request, as if "/music <text>" had been typed
    ctx = SimpleNamespace(args=m.text.split(), bot=context.bot, bot_data=context.bot_data)
    await music.music_cmd(update, ctx)
    raise ApplicationHandlerStop  # don't let the freeform chat handler answer it too


# ---------------------------------------------------------------- wiring
def register(app) -> None:
    app.add_handler(CommandHandler("song", music.music_cmd, filters=filters.ChatType.GROUPS))
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.REPLY & filters.TEXT & ~filters.COMMAND,
            on_prompt_reply,
        ),
        group=-1,
    )
