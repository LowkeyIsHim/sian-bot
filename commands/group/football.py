"""commands/group/football.py

Live football updates for the big competitions. ONE command: /football

    /football              today's big matches: kick-off times, live scores, results
    /football on | off     (admins) switch automatic updates on / off for this group
    /football status       (admins) plan, requests used today, last API problem

When updates are ON the bot posts, on its own:
    - "kick-off in 30 minutes"                                   (text)
    - kick-off                                                   (text)
    - every goal, half-time and full-time        (SCOREBOARD CARD: both team
      logos, big score, scorers and minutes for each side, plus a caption)
    - red cards and goals ruled out by VAR                       (text)

Competitions: Premier League, La Liga, Serie A, Bundesliga, Ligue 1,
Champions League, World Cup, Euros, AFCON.

Data: API-Football (api-sports.io). Free plan = 100 requests a day, so the bot
polls ONE call for all live matches, only while a covered match is on or about
to start, and stretches the gap between polls when the day's requests run low.
Free-plan updates are therefore a few minutes behind real time.

secrets.env:
    FOOTBALL_API_KEY=...            (required)
    FOOTBALL_TZ=Africa/Lagos        (optional - kick-off times are shown in this zone)
    FOOTBALL_LEAGUES=39,140,...     (optional - override the competition ids)
    FOOTBALL_DAILY_BUDGET=95        (optional - requests the bot may use per day)
    FOOTBALL_POLL_SECONDS=180       (optional - fastest gap between live polls)

bot.py: start the engine in _post_init, next to the news loop:
    asyncio.create_task(football.start_background_loop(app.bot))
Handler group used: none (command only).
"""

import asyncio
import colorsys
import html
import json
import logging
import os
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from io import BytesIO

import requests
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import CommandHandler, ContextTypes, filters

import access

logger = logging.getLogger(__name__)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))  # .../bot_src/commands/group
_PERSISTENT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))
DATA_FILE = os.path.join(_PERSISTENT_DIR, "football_data.json")

API_BASE = "https://v3.football.api-sports.io"

# API-Football competition ids (order = order in /football)
COVER = {
    39: "Premier League",
    140: "La Liga",
    135: "Serie A",
    78: "Bundesliga",
    61: "Ligue 1",
    2: "Champions League",
    1: "World Cup",
    4: "Euros",
    6: "AFCON",
}

TICK_SECONDS = 30
RESERVE_CALLS = 6              # always keep a few requests for schedule refreshes
SCHEDULE_REFRESH_SECONDS = 6 * 3600
PRE_MATCH_SECONDS = 30 * 60
WINDOW_BEFORE = 90             # start watching a match 90s before kick-off
WINDOW_AFTER = 8100            # ...until 2h15m after (covers extra time)
KEEP_FIXTURE_STATE_SECONDS = 3 * 86400

LIVE = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT"}
FINISHED = {"FT", "AET", "PEN", "PST", "CANC", "ABD", "AWD", "WO"}
ENDED_PLAYED = {"FT", "AET", "PEN"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _cover_ids() -> list[int]:
    raw = os.environ.get("FOOTBALL_LEAGUES", "")
    try:
        ids = [int(x) for x in raw.replace(" ", "").split(",") if x]
    except ValueError:
        ids = []
    return ids or list(COVER)


DAILY_BUDGET = _env_int("FOOTBALL_DAILY_BUDGET", 95)
BASE_POLL_SECONDS = _env_int("FOOTBALL_POLL_SECONDS", 180)


def _now() -> float:
    return time.time()


# ---------------------------------------------------------------- storage
_data: dict | None = None


def _db() -> dict:
    global _data
    if _data is None:
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                _data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            _data = {}
        _data.setdefault("chats", {})
        _data.setdefault("fixtures", {})
        _data.setdefault("schedule", {"fetched": 0, "date": "", "fixtures": {}})
        _data.setdefault("usage", {"date": "", "calls": 0, "remaining": None})
        _data.setdefault("last_error", "")
    return _data


def _save() -> None:
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_db(), f)
    os.replace(tmp, DATA_FILE)  # atomic


def _enabled_chats() -> list[int]:
    return [int(c) for c, conf in _db()["chats"].items() if conf.get("enabled")]


# ---------------------------------------------------------------- time
def _tz():
    name = os.environ.get("FOOTBALL_TZ", "Africa/Lagos")
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name), name
    except Exception:
        return timezone(timedelta(hours=1)), "UTC+1"


def _local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, _tz()[0])


def _clock(ts: float) -> str:
    d = _local(ts)
    label = d.strftime("%Z") or _tz()[1]
    return f"{d:%H:%M} {label}"


def _utc_date(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- API (blocking; run in a thread)
def _api(path: str, params: dict) -> dict:
    """Returns {'data': list|None, 'err': str|None, 'remaining': int|None}."""
    key = os.environ.get("FOOTBALL_API_KEY")
    if not key:
        return {"data": None, "err": "FOOTBALL_API_KEY is not set", "remaining": None}
    try:
        r = requests.get(
            API_BASE + path, params=params, headers={"x-apisports-key": key}, timeout=15
        )
        remaining = r.headers.get("x-ratelimit-requests-remaining")
        remaining = int(remaining) if remaining and remaining.isdigit() else None
        r.raise_for_status()
        body = r.json()
        errs = body.get("errors")
        if errs:  # API-Football reports problems here, even with a 200 status
            msg = next(iter(errs.values())) if isinstance(errs, dict) else str(errs[0])
            return {"data": None, "err": str(msg), "remaining": remaining}
        return {"data": body.get("response", []), "err": None, "remaining": remaining}
    except Exception as e:
        return {"data": None, "err": f"{type(e).__name__}: {e}", "remaining": None}


async def _call(path: str, params: dict):
    """Counts the request against today's budget. Returns (data, err)."""
    db = _db()
    if not os.environ.get("FOOTBALL_API_KEY"):
        db["last_error"] = "FOOTBALL_API_KEY is not set in secrets.env"
        return None, db["last_error"]
    today = _utc_date(_now())
    if db["usage"]["date"] != today:
        db["usage"] = {"date": today, "calls": 0, "remaining": None}
    res = await asyncio.to_thread(_api, path, params)
    db["usage"]["calls"] += 1
    if res["remaining"] is not None:
        db["usage"]["remaining"] = res["remaining"]
    if res["err"]:
        db["last_error"] = f"{_clock(_now())}: {res['err']}"
        logger.warning(f"[Football] API problem: {res['err']}")
    return res["data"], res["err"]


def _calls_left() -> int:
    u = _db()["usage"]
    if u["date"] != _utc_date(_now()):
        return DAILY_BUDGET
    left = DAILY_BUDGET - u["calls"]
    if u["remaining"] is not None:  # trust the API's own count when we have it
        left = min(left, u["remaining"])
    return left


# ---------------------------------------------------------------- fixtures
def _slim(fx: dict) -> dict:
    f, lg, tm, gl = fx["fixture"], fx["league"], fx["teams"], fx.get("goals") or {}
    return {
        "id": f["id"],
        "ts": f["timestamp"],
        "league_id": lg["id"],
        "league": lg.get("name", ""),
        "home": tm["home"]["name"],
        "away": tm["away"]["name"],
        "status": f["status"]["short"],
        "elapsed": f["status"].get("elapsed"),
        "hg": gl.get("home"),
        "ag": gl.get("away"),
        "hl": tm["home"].get("logo", ""),
        "al": tm["away"].get("logo", ""),
    }


def _scoreline(f: dict) -> str:
    if f["hg"] is None or f["ag"] is None:
        return f"{html.escape(f['home'])} vs {html.escape(f['away'])}"
    return f"{html.escape(f['home'])} {f['hg']}–{f['ag']} {html.escape(f['away'])}"


_last_sched_try = 0.0


async def _refresh_schedule(force: bool = False) -> None:
    global _last_sched_try
    sched = _db()["schedule"]
    now = _now()
    today = _local(now).strftime("%Y-%m-%d")
    if not force and sched["date"] == today and now - sched["fetched"] < SCHEDULE_REFRESH_SECONDS:
        return
    if now - _last_sched_try < 300 or _calls_left() <= 0:  # after a failure, wait 5 min
        return
    _last_sched_try = now
    data, err = await _call("/fixtures", {"date": today, "timezone": _tz()[1]})
    if err or data is None:
        return
    ids = set(_cover_ids())
    fresh = {}
    for fx in data:
        try:
            if fx["league"]["id"] in ids:
                s = _slim(fx)
                fresh[str(s["id"])] = s
        except (KeyError, TypeError):
            continue
    old = sched["fixtures"] if sched["date"] == today else {}
    for fid, s in fresh.items():  # keep the freshest live status we already know
        if fid in old and old[fid]["status"] in LIVE | FINISHED and s["status"] == "NS":
            fresh[fid] = old[fid]
    sched.update({"date": today, "fetched": now, "fixtures": fresh})
    _save()


def _fx_state(fid, ts: float) -> dict:
    states = _db()["fixtures"]
    st = states.get(str(fid))
    if st is None:
        st = {"ts": ts, "alerted": False, "sent": [], "events": [], "live": False, "seen": False}
        states[str(fid)] = st
    return st


def _prune() -> None:
    states = _db()["fixtures"]
    cutoff = _now() - KEEP_FIXTURE_STATE_SECONDS
    for fid in [k for k, v in states.items() if v.get("ts", 0) < cutoff]:
        states.pop(fid, None)


# ---------------------------------------------------------------- messages
def _minute(ev: dict) -> str:
    t = ev.get("time") or {}
    el, extra = t.get("elapsed"), t.get("extra")
    if el is None:
        return "?"
    return f"{el}+{extra}'" if extra else f"{el}'"


def _sort_key(ev: dict):
    t = ev.get("time") or {}
    return (t.get("elapsed") or 0, t.get("extra") or 0)


def _ev_key(ev: dict) -> str:
    t = ev.get("time") or {}
    return "|".join(
        str(x)
        for x in (
            ev.get("type"), ev.get("detail"), t.get("elapsed"), t.get("extra"),
            (ev.get("team") or {}).get("name"), (ev.get("player") or {}).get("name"),
        )
    )


def _relevant(ev: dict) -> str | None:
    """'goal' | 'red' | 'var' | None"""
    typ, det = ev.get("type"), (ev.get("detail") or "")
    if typ == "Goal" and "Missed" not in det:
        return "goal"
    if typ == "Card" and ("Red" in det or "Second Yellow" in det):
        return "red"
    if typ == "Var" and ("cancel" in det.lower() or "disallow" in det.lower()):
        return "var"
    return None


def _goal_line(ev: dict) -> str:
    who = html.escape((ev.get("player") or {}).get("name") or "Unknown")
    team = html.escape((ev.get("team") or {}).get("name") or "")
    det = ev.get("detail") or ""
    tag = " (pen)" if "Penalty" in det else " (og)" if "Own" in det else ""
    assist = (ev.get("assist") or {}).get("name")
    extra = f" — assist: {html.escape(assist)}" if assist else ""
    return f"⚽ {_minute(ev)} <b>{who}</b>{tag} · {team}{extra}"


def _events_message(f: dict, new_events: list[dict]) -> str:
    kinds = [_relevant(e) for e in new_events]
    goals = [e for e, k in zip(new_events, kinds) if k == "goal"]
    lines = []
    if goals:
        lines.append(f"⚽ <b>GOAL!</b>  {_scoreline(f)}" if len(goals) == 1
                     else f"⚽⚽ <b>GOALS!</b>  {_scoreline(f)}")
    else:
        lines.append(f"<b>{_scoreline(f)}</b>")
    for ev, kind in zip(new_events, kinds):
        if kind == "goal":
            lines.append(_goal_line(ev))
        elif kind == "red":
            who = html.escape((ev.get("player") or {}).get("name") or "Unknown")
            team = html.escape((ev.get("team") or {}).get("name") or "")
            lines.append(f"🟥 {_minute(ev)} <b>{who}</b> sent off · {team}")
        elif kind == "var":
            lines.append(f"🖥 {_minute(ev)} <b>VAR:</b> {html.escape(ev.get('detail') or 'decision')}")
    lines.append(f"<i>{html.escape(f['league'])}</i>")
    return "\n".join(lines)


def _summary(f: dict, events: list[dict], title: str, score: dict | None = None) -> str:
    goals = sorted((e for e in events if _relevant(e) == "goal"), key=_sort_key)
    lines = [title, f"<b>{_scoreline(f)}</b>"]
    if score and f["status"] == "PEN":
        pen = score.get("penalty") or {}
        if pen.get("home") is not None:
            lines.append(f"<i>penalties {pen['home']}–{pen['away']}</i>")
    elif f["status"] == "AET":
        lines.append("<i>after extra time</i>")
    if goals:
        lines.append("")
        lines += [_goal_line(e) for e in goals]
    lines.append(f"<i>{html.escape(f['league'])}</i>")
    return "\n".join(lines)


def _caption_summary(f: dict, title: str, score: dict | None = None) -> str:
    lines = [title, f"<b>{_scoreline(f)}</b>"]
    if score and f["status"] == "PEN":
        pen = score.get("penalty") or {}
        if pen.get("home") is not None:
            lines.append(f"<i>penalties {pen['home']}–{pen['away']}</i>")
    lines.append(f"<i>{html.escape(f['league'])}</i>")
    return "\n".join(lines)


async def _broadcast(bot, text: str) -> None:
    for chat_id in _enabled_chats():
        try:
            await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML,
                                   disable_web_page_preview=True)
        except TelegramError as e:
            logger.warning(f"[Football] could not post to {chat_id}: {e}")


# ---------------------------------------------------------------- the scoreboard card
FONT_DIR = os.path.join(_PERSISTENT_DIR, "fonts")  # the music card downloads Poppins here
_FONT_FILES = {"bold": "Poppins-Bold.ttf", "med": "Poppins-Medium.ttf", "reg": "Poppins-Regular.ttf"}
_font_cache: dict = {}
_logo_cache: dict = {}
CARD = 1080
# The API lists an own goal under the team of the player who scored it, so the
# goal belongs on the OTHER side of the scoreboard. Flip this if it looks wrong.
OWN_GOAL_CREDITED_TO_OPPONENT = True


def _font(key: str, size: int):
    ck = (key, size)
    if ck in _font_cache:
        return _font_cache[ck]
    try:
        font = ImageFont.truetype(os.path.join(FONT_DIR, _FONT_FILES[key]), size)
        _font_cache[ck] = font
        return font
    except Exception:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


def _plain(text: str) -> str:
    """Poppins lacks dotted Yoruba/Vietnamese letters - show the base letter instead."""
    out = []
    for ch in text or "":
        if 0x1E00 <= ord(ch) <= 0x1EFF:
            ch = unicodedata.normalize("NFD", ch)[0]
        out.append(ch)
    return "".join(out).strip()


def _short(name: str) -> str:
    """'Bukayo Saka' -> 'B. Saka' (single names and initials stay as they are)."""
    parts = _plain(name).split()
    if len(parts) < 2 or len(parts[0]) <= 2:
        return " ".join(parts) or "Unknown"
    return f"{parts[0][0]}. {' '.join(parts[1:])}"


def _logo(url: str):
    if not url:
        return None
    if url in _logo_cache:
        return _logo_cache[url]
    img = None
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        img = Image.open(BytesIO(r.content)).convert("RGBA")
    except Exception as e:
        logger.info(f"[Football] could not load logo: {e}")
    if len(_logo_cache) > 80:
        _logo_cache.clear()
    _logo_cache[url] = img
    return img


def _team_color(logo) -> tuple:
    """Most vivid colour of a crest, for the glow behind that team."""
    fallback = (120, 130, 160)
    if logo is None:
        return fallback
    best, best_w = fallback, 0.0
    for r, g, b, a in logo.resize((32, 32)).getdata():
        if a < 200:
            continue
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        w = s * v if v > 0.25 else 0.0
        if w > best_w:
            best_w = w
            best = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(h, min(max(s, 0.5), 0.9), max(v, 0.85)))
    return best


def _fit(d, text: str, key: str, size: int, max_w: float, min_size: int = 20):
    s = size
    while s > min_size:
        f = _font(key, s)
        if d.textlength(text, font=f) <= max_w:
            return f, text
        s -= 2
    f = _font(key, min_size)
    while text and d.textlength(text + "…", font=f) > max_w:
        text = text[:-1]
    return f, text.rstrip() + "…"


def _scorers(f: dict, events: list[dict]):
    """(home_lines, away_lines) - e.g. 'B. Saka 23'' / 'C. Palmer 70' (P)'."""
    home, away = [], []
    for e in sorted((e for e in events if _relevant(e) == "goal"), key=_sort_key):
        det = e.get("detail") or ""
        is_home = (e.get("team") or {}).get("name") == f["home"]
        if "Own" in det and OWN_GOAL_CREDITED_TO_OPPONENT:
            is_home = not is_home
        tag = " (P)" if "Penalty" in det else " (OG)" if "Own" in det else ""
        line = f"{_short((e.get('player') or {}).get('name') or '')} {_minute(e)}{tag}"
        (home if is_home else away).append(line)
    return home, away


def _glow(color, cx: int, cy: int, radius: int, alpha: int):
    layer = Image.new("RGBA", (CARD, CARD), (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=tuple(color) + (alpha,))
    return layer.filter(ImageFilter.GaussianBlur(radius * 0.55))


def _render_card(f: dict, events: list[dict], chip: str):
    """Scoreboard card as JPEG bytes (None if anything goes wrong)."""
    try:
        W = CARD
        hl, al = _logo(f.get("hl", "")), _logo(f.get("al", ""))
        hc, ac = _team_color(hl), _team_color(al)

        top, bot = (16, 18, 28), (5, 6, 9)
        grad = Image.linear_gradient("L").resize((W, W))
        canvas = Image.merge("RGB", [grad.point(lambda p, a=top[i], b=bot[i]: int(a + (b - a) * p / 255)) for i in range(3)]).convert("RGBA")
        canvas = Image.alpha_composite(canvas, _glow(hc, 215, 390, 210, 150))
        canvas = Image.alpha_composite(canvas, _glow(ac, 865, 390, 210, 150))
        edges = Image.radial_gradient("L").resize((W, W)).point(lambda p: int((p / 255) ** 2 * 170))
        canvas = Image.composite(Image.new("RGBA", (W, W), (0, 0, 0, 255)), canvas, edges)

        # translucent circles behind the crests (so dark crests stay visible)
        over = Image.new("RGBA", (W, W), (0, 0, 0, 0))
        od = ImageDraw.Draw(over)
        for cx in (215, 865):
            od.ellipse((cx - 130, 260, cx + 130, 520), fill=(255, 255, 255, 26))
        canvas = Image.alpha_composite(canvas, over)

        for logo, cx in ((hl, 215), (al, 865)):
            if logo is not None:
                lg = logo.copy()
                lg.thumbnail((190, 190), Image.LANCZOS)
                canvas.alpha_composite(lg, (cx - lg.width // 2, 390 - lg.height // 2))

        d = ImageDraw.Draw(canvas)
        white, grey = (255, 255, 255), (165, 168, 180)

        # header
        league = _plain(f["league"]).upper()
        fl, txt = _fit(d, " ".join(league), "med", 28, 900, 18)
        d.text((W // 2, 70), txt, font=fl, fill=(210, 212, 222), anchor="mm")
        d.text((W // 2, 112), f"{_local(f['ts']):%a %d %b}  ·  {_clock(f['ts'])}", font=_font("reg", 24), fill=grey, anchor="mm")

        # team names
        for name, cx in ((f["home"], 215), (f["away"], 865)):
            fn, txt = _fit(d, _plain(name), "bold", 40, 380, 24)
            d.text((cx, 590), txt, font=fn, fill=white, anchor="mm")

        # score + status chip
        score = f"{f['hg'] if f['hg'] is not None else 0}  –  {f['ag'] if f['ag'] is not None else 0}"
        d.text((W // 2, 372), score, font=_font("bold", 112), fill=white, anchor="mm")
        fc = _font("bold", 26)
        cw = d.textlength(chip, font=fc) + 56
        d.rounded_rectangle((W // 2 - cw / 2, 466, W // 2 + cw / 2, 516), 25, fill=(255, 255, 255, 235))
        d.text((W // 2, 491), chip, font=fc, fill=(14, 16, 22), anchor="mm")

        # scorers, one column per side
        d.line((80, 660, W - 80, 660), fill=(255, 255, 255, 40), width=2)
        home_s, away_s = _scorers(f, events)
        fs = _font("med", 31)
        for lines, x, anchor, color in ((home_s, 80, "la", hc), (away_s, W - 80, "ra", ac)):
            y = 700
            for line in lines[:6]:
                d.text((x, y), line, font=fs, fill=(235, 236, 242), anchor=anchor)
                y += 50
            if len(lines) > 6:
                d.text((x, y), f"+{len(lines) - 6} more", font=_font("reg", 26), fill=grey, anchor=anchor)
        if not home_s and not away_s:
            d.text((W // 2, 740), "no goals yet", font=_font("reg", 28), fill=grey, anchor="mm")

        # footer
        d.text((W // 2, 1036), "G O D D E S S   F O O T B A L L", font=_font("med", 20), fill=(110, 113, 126), anchor="mm")

        img = canvas.convert("RGB")
        grain = Image.effect_noise((W, W), 24).convert("RGB")
        img = Image.blend(img, ImageChops.overlay(img, grain), 0.05)
        out = BytesIO()
        img.save(out, "JPEG", quality=90, optimize=True)
        return out.getvalue()
    except Exception as e:
        logger.warning(f"[Football] could not render the card: {e}")
        return None


async def _post(bot, text: str, f: dict | None = None, events=None, chip: str = "") -> None:
    """Posts a scoreboard card with `text` as its caption when a fixture is given;
    any problem with the card falls back to the plain text message."""
    card = await asyncio.to_thread(_render_card, f, events or [], chip) if f else None
    for chat_id in _enabled_chats():
        try:
            if card:
                buf = BytesIO(card)
                buf.name = "match.jpg"
                await bot.send_photo(chat_id, buf, caption=text[:1000], parse_mode=ParseMode.HTML)
            else:
                await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML,
                                       disable_web_page_preview=True)
        except TelegramError as e:
            logger.warning(f"[Football] could not post to {chat_id}: {e}")
            if card:  # photo failed - still deliver the news as text
                try:
                    await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML,
                                           disable_web_page_preview=True)
                except TelegramError:
                    pass


# ---------------------------------------------------------------- the engine
async def _process(bot, fx: dict) -> None:
    """Handles one fixture object from the API: updates the cache and posts
    whatever is new (kick-off, goals/cards/VAR, half-time, full-time)."""
    f = _slim(fx)
    sched = _db()["schedule"]["fixtures"]
    if str(f["id"]) in sched or _local(_now()).strftime("%Y-%m-%d") == _local(f["ts"]).strftime("%Y-%m-%d"):
        sched[str(f["id"])] = f
    st = _fx_state(f["id"], f["ts"])
    events = fx.get("events") or []
    relevant = [e for e in events if _relevant(e)]

    # first time we see this match already well under way (bot started late):
    # don't replay the past, just remember it
    if not st["seen"]:
        st["seen"] = True
        late = f["status"] in {"HT", "2H", "ET", "BT", "P"} or (f["elapsed"] or 0) > 5
        if late:
            st["events"] = [_ev_key(e) for e in relevant]
            st["sent"] += [s for s in ("KO", "HT") if s not in st["sent"]]

    if f["status"] in LIVE:
        st["live"] = True

    if f["status"] == "1H" and "KO" not in st["sent"]:
        st["sent"].append("KO")
        await _broadcast(bot, f"🟢 <b>Kick-off!</b>\n{_scoreline(f)}\n<i>{html.escape(f['league'])}</i>")

    new = sorted((e for e in relevant if _ev_key(e) not in st["events"]), key=_sort_key)
    if new:
        st["events"] += [_ev_key(e) for e in new]
        new_goals = [e for e in new if _relevant(e) == "goal"]
        if new_goals:  # a goal gets the scoreboard card
            await _post(bot, _events_message(f, new), f, events, f"GOAL  ·  {_minute(new_goals[-1])}")
        else:  # cards / VAR stay as quick text
            await _broadcast(bot, _events_message(f, new))

    if f["status"] == "HT" and "HT" not in st["sent"]:
        st["sent"].append("HT")
        await _post(bot, _caption_summary(f, "⏸ <b>Half-time</b>"), f, events, "HALF TIME")

    if f["status"] in ENDED_PLAYED and "FT" not in st["sent"]:
        st["sent"].append("FT")
        st["live"] = False
        chip = {"AET": "AFTER EXTRA TIME", "PEN": "PENALTIES"}.get(f["status"], "FULL TIME")
        await _post(bot, _caption_summary(f, "🏁 <b>Full-time</b>", fx.get("score")), f, events, chip)
    elif f["status"] in FINISHED:
        st["live"] = False


def _active_seconds_left(now: float) -> float:
    """Seconds of watch-worthy match time left before the API's daily reset (00:00 UTC)."""
    end_of_day = (datetime.fromtimestamp(now, timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()
    spans = []
    for f in _db()["schedule"]["fixtures"].values():
        if f["status"] in FINISHED:
            continue
        start, stop = max(now, f["ts"] - WINDOW_BEFORE), min(end_of_day, f["ts"] + WINDOW_AFTER)
        if stop > start:
            spans.append((start, stop))
    spans.sort()
    total, cur_s, cur_e = 0.0, None, None
    for s, e in spans:  # merge overlapping matches so they aren't counted twice
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s
    return total


def _poll_interval(now: float) -> float:
    """Spread the remaining requests across the match time still to come."""
    spare = _calls_left() - RESERVE_CALLS
    if spare <= 0:
        return float("inf")
    return max(BASE_POLL_SECONDS, _active_seconds_left(now) / spare)


def _watching(now: float) -> bool:
    for f in _db()["schedule"]["fixtures"].values():
        if f["status"] in FINISHED:
            continue
        if f["status"] in LIVE or f["ts"] - WINDOW_BEFORE <= now <= f["ts"] + WINDOW_AFTER:
            return True
    return False


_last_poll = 0.0


async def _live_poll(bot) -> None:
    ids = set(_cover_ids())
    data, err = await _call("/fixtures", {"live": "all"})
    if err or data is None:
        return
    live_now = set()
    for fx in data:
        try:
            if fx["league"]["id"] not in ids:
                continue
            live_now.add(str(fx["fixture"]["id"]))
            await _process(bot, fx)
        except (KeyError, TypeError) as e:
            logger.warning(f"[Football] skipped a malformed fixture: {e}")

    # matches leave the live feed the moment they end - fetch their final state
    gone = [fid for fid, st in _db()["fixtures"].items() if st.get("live") and fid not in live_now]
    for fid in gone[:3]:
        if _calls_left() <= 0:
            break
        detail, derr = await _call("/fixtures", {"id": fid})
        if derr or not detail:
            continue
        try:
            await _process(bot, detail[0])
        except (KeyError, TypeError) as e:
            logger.warning(f"[Football] skipped a malformed fixture: {e}")
    _save()


async def _pre_match_alerts(bot) -> None:
    now = _now()
    changed = False
    for f in _db()["schedule"]["fixtures"].values():
        if f["status"] not in ("NS", "TBD"):
            continue
        delta = f["ts"] - now
        if 120 <= delta <= PRE_MATCH_SECONDS:
            st = _fx_state(f["id"], f["ts"])
            if st["alerted"]:
                continue
            st["alerted"] = True
            changed = True
            mins = max(1, round(delta / 60))
            await _broadcast(
                bot,
                f"⏰ <b>Kick-off in {mins} minutes</b>\n"
                f"{html.escape(f['home'])} vs {html.escape(f['away'])}\n"
                f"<i>{html.escape(f['league'])} · {_clock(f['ts'])}</i>",
            )
    if changed:
        _save()


async def _tick(bot) -> None:
    global _last_poll
    if not _enabled_chats():
        return  # nobody wants updates: spend zero requests
    await _refresh_schedule()
    await _pre_match_alerts(bot)
    now = _now()
    if _watching(now) and now - _last_poll >= _poll_interval(now):
        _last_poll = now
        await _live_poll(bot)
        _prune()
        _save()


async def start_background_loop(bot) -> None:
    """Call once from bot.py's post_init - runs forever."""
    await asyncio.sleep(10)
    while True:
        try:
            await _tick(bot)
        except Exception:
            logger.exception("Error in football background loop")
        await asyncio.sleep(TICK_SECONDS)


# ---------------------------------------------------------------- /football
def _privileged(user_id: int) -> bool:
    return access.is_creator(user_id) or access.is_group_admin(user_id)


def _line(f: dict) -> str:
    st = f["status"]
    if st in LIVE:
        if st == "HT":
            return f"⏸ HT  {_scoreline(f)}"
        mins = f"{f['elapsed']}'" if f.get("elapsed") else "live"
        return f"🔴 {mins}  {_scoreline(f)}"
    if st in ENDED_PLAYED:
        return f"✅ FT  {_scoreline(f)}"
    if st in FINISHED:
        return f"⛔ {st}  {html.escape(f['home'])} vs {html.escape(f['away'])}"
    return f"{_local(f['ts']):%H:%M}  {html.escape(f['home'])} vs {html.escape(f['away'])}"


def _today_text() -> str:
    fixtures = list(_db()["schedule"]["fixtures"].values())
    if not fixtures:
        return "No big matches today ⚽"
    order = {lid: i for i, lid in enumerate(_cover_ids())}
    fixtures.sort(key=lambda f: (order.get(f["league_id"], 99), f["ts"]))
    lines = [f"⚽ <b>Today's matches</b> <i>({_tz()[1]})</i>"]
    league = None
    for f in fixtures:
        if f["league"] != league:
            league = f["league"]
            lines += ["", f"<b>{html.escape(league)}</b>"]
        lines.append(_line(f))
    return "\n".join(lines)


async def football_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    m, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await m.reply_text("This only works inside a group.")
        return

    sub = context.args[0].lower() if context.args else ""
    db = _db()

    if sub in ("on", "off", "status"):
        if not _privileged(user.id):
            return  # silent, like the other admin commands
        conf = db["chats"].setdefault(str(chat.id), {"enabled": False})
        if sub == "on":
            conf["enabled"] = True
            _save()
            await m.reply_text(
                "⚽ Football updates are ON.\n"
                "I'll post kick-off alerts, goals, cards, half-time and full-time for the big matches."
            )
        elif sub == "off":
            conf["enabled"] = False
            _save()
            await m.reply_text("Football updates are OFF.")
        else:
            u = db["usage"]
            used = u["calls"] if u["date"] == _utc_date(_now()) else 0
            names = ", ".join(COVER.get(i, str(i)) for i in _cover_ids())
            await m.reply_text(
                f"Football updates: {'ON' if conf['enabled'] else 'OFF'}\n"
                f"Competitions: {names}\n"
                f"Requests used today: {used} of {DAILY_BUDGET} "
                f"(API says {u['remaining']} left)\n"
                f"Last problem: {db['last_error'] or 'none'}"
            )
        return

    # /football  - today's matches (from the cache, no request unless it's stale)
    await _refresh_schedule()
    await m.reply_text(_today_text(), parse_mode=ParseMode.HTML)


def register(app) -> None:
    app.add_handler(CommandHandler("football", football_cmd, filters=filters.ChatType.GROUPS))
