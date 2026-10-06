"""commands/group/football_menu.py

The Football category inside /gmenu:

  * Everyone: what the bot posts, the competitions covered, whether updates
    are ON in this group, a "Today's matches" button (kick-off times, live
    scores, results) and a "Tables" button (standings cards).
  * Admins and creators also get buttons to switch updates on / off for this
    group and to see the status (requests used today, last API problem).

Nothing in football.py changes - this module only calls into it. It has no
register(); group_menu.py routes every 'g:football...' button here.
"""

import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from branding import BOT_NAME, LINE
from commands.group import football


def _title(sub: str) -> str:
    return f"✦{LINE}✦\n{BOT_NAME}\n<i>{html.escape(sub)}</i>\n✦{LINE}✦"


def _back(target: str = "g:football") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⟵ Back", callback_data=target)]])


def _conf(chat_id: int) -> dict:
    return football._db()["chats"].setdefault(str(chat_id), {"enabled": False})


def _main_page(chat_id: int, admin: bool):
    on = _conf(chat_id)["enabled"]
    comps = ", ".join(football.COVER.get(i, str(i)) for i in football._cover_ids())
    lines = [
        _title("football"),
        "",
        "<b>/football</b>  today's big matches, live scores and results",
        "",
        "<i>with updates on, the bot posts by itself:</i>",
        "• kick-off alert 30 minutes before",
        "• goals, half-time and full-time scoreboard cards",
        "• red cards and VAR decisions",
        "",
        f"competitions: {html.escape(comps)}",
        "",
        f"updates in this group: <b>{'ON' if on else 'OFF'}</b>",
    ]
    rows = [[
        InlineKeyboardButton("📅 Today's matches", callback_data="g:football:today"),
        InlineKeyboardButton("🏆 Tables", callback_data="g:football:tables"),
    ]]

    if admin:
        rows.append([
            InlineKeyboardButton(
                "🔕 Turn updates off" if on else "🔔 Turn updates on", callback_data="g:football:toggle"
            )
        ])
        rows.append([InlineKeyboardButton("📊 Status", callback_data="g:football:status")])

    rows.append([InlineKeyboardButton("⟵ Back", callback_data="g:main")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _tables_page():
    """League buttons - each one sends that table as a card (handled in football.py)."""
    items = list(football.TABLE_LEAGUES.items())
    rows = [
        [InlineKeyboardButton(n, callback_data=f"fbt:{i}") for i, n in items[k:k + 2]]
        for k in range(0, len(items), 2)
    ]
    rows.append([InlineKeyboardButton("⟵ Back", callback_data="g:football")])
    text = "\n".join([_title("tables"), "", "pick a league - the table arrives as a card."])
    return text, InlineKeyboardMarkup(rows)


def _status_page():
    db = football._db()
    u = db["usage"]
    used = u["calls"] if u["date"] == football._utc_date(football._now()) else 0
    problem = html.escape(db["last_error"]) if db["last_error"] else "none"
    chats = len(football._enabled_chats())
    text = "\n".join([
        _title("football status"),
        "",
        "source: ESPN public data <i>(unofficial)</i>",
        f"requests made today: <b>{used}</b>",
        f"groups with updates on: {chats}",
        f"last problem: {problem}",
    ])
    return text, _back()


async def _edit(q, page) -> None:
    text, kb = page
    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True)
    except TelegramError:
        pass  # "message is not modified" etc.


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called by gmenu_callback for every 'g:football...' button. Answers the
    callback itself (so it can show alerts)."""
    q = update.callback_query
    data = q.data
    chat_id = q.message.chat_id
    admin = football._privileged(q.from_user.id)

    if data == "g:football":
        await q.answer()
        await _edit(q, _main_page(chat_id, admin))
        return

    if data == "g:football:today":
        await q.answer()
        await football._refresh_schedule()
        await _edit(q, (football._today_text(), _back()))
        return

    if data == "g:football:tables":
        await q.answer()
        await _edit(q, _tables_page())
        return

    # everything below is admin-only
    if not admin:
        await q.answer("Admins only.", show_alert=True)
        return
    await q.answer()

    if data == "g:football:toggle":
        conf = _conf(chat_id)
        conf["enabled"] = not conf["enabled"]
        football._save()
        await _edit(q, _main_page(chat_id, admin))
    elif data == "g:football:status":
        await _edit(q, _status_page())
