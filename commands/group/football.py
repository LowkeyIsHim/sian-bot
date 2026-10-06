"""commands/group/football.py

Live football updates for the big competitions. ONE command: /football

    /football              today's big matches: kick-off times, live scores, results
    /football table [league]   standings card - premier league, la liga, serie a,
                           bundesliga, ligue 1, champions league
    /football on | off     (admins) switch automatic updates on / off for this group
    /football status       (admins) source, requests made today, last problem
    /football probe        (admins) test what ESPN returns for every competition

When updates are ON the bot posts, on its own:
    - "kick-off in 30 minutes"                                   (text)
    - kick-off                                                   (text)
    - every goal, half-time and full-time        (SCOREBOARD CARD: both team
      logos, big score, scorers and minutes for each side, plus a caption)
    - red cards and goals ruled out by VAR                       (text)

Competitions: Premier League, La Liga, Serie A, Bundesliga, Ligue 1,
Champions League, World Cup, Euros, AFCON.

Data: ESPN's public JSON (site.api.espn.com). It needs no key and no account,
but it is UNOFFICIAL and undocumented: ESPN can change or block it without
warning. The bot only asks while a covered match is on or about to start
(one small request per active competition, every minute), caches tables for
30 minutes, and keeps a plain-text fallback for everything. If it ever stops
working, /football probe shows exactly what ESPN returned.

secrets.env (all optional):
    FOOTBALL_TZ=Africa/Lagos        kick-off times are shown in this zone
    FOOTBALL_LEAGUES=39,140,...     override the competition list
    FOOTBALL_POLL_SECONDS=60        gap between live polls

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
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO

import requests
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

import access

logger = logging.getLogger(__name__)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))  # .../bot_src/commands/group
_PERSISTENT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))
DATA_FILE = os.path.join(_PERSISTENT_DIR, "football_data.json")

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
ESPN_STANDINGS = "https://site.api.espn.com/apis/v2/sports/soccer/{slug}/standings"  # /apis/v2/, not /apis/site/v2/
# our competition ids -> ESPN slugs. eng.1 esp.1 ita.1 ger.1 fra.1 uefa.champions fifa.world
# are confirmed; uefa.euro and caf.nations are best guesses - /football probe checks them.
ESPN_SLUGS = {
    39: "eng.1", 140: "esp.1", 135: "ita.1", 78: "ger.1", 61: "fra.1",
    2: "uefa.champions", 1: "fifa.world", 4: "uefa.euro", 6: "caf.nations",
}
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; GoddessBot/1.0)", "Accept": "application/json"}

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


BASE_POLL_SECONDS = _env_int("FOOTBALL_POLL_SECONDS", 60)


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
        _data.setdefault("tables", {})
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
def _fetch_json(url: str, params: dict | None = None) -> dict:
    """Blocking GET (run in a thread). Returns {'data': json|None, 'err': str|None}."""
    try:
        r = requests.get(url, params=params or {}, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return {"data": r.json(), "err": None}
    except Exception as e:
        return {"data": None, "err": f"{type(e).__name__}: {e}"}


async def _call(url: str, params: dict | None = None):
    """Returns (data, err) and keeps a per-day request count for /football status."""
    db = _db()
    today = _utc_date(_now())
    if db["usage"]["date"] != today:
        db["usage"] = {"date": today, "calls": 0, "remaining": None}
    res = await asyncio.to_thread(_fetch_json, url, params)
    db["usage"]["calls"] += 1
    if res["err"]:
        db["last_error"] = f"{_clock(_now())}: {res['err']}"
        logger.warning(f"[Football] request problem: {res['err']}")
    return res["data"], res["err"]


# ---------------------------------------------------------------- ESPN adapter
def _parse_iso(text: str):
    t = (text or "").replace("Z", "").replace("+00:00", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return int(datetime.strptime(t, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    return None


def _espn_status(stype: dict) -> str:
    """ESPN status -> the short codes the engine uses (NS 1H HT 2H ET P FT AET PEN ...)."""
    name = (stype.get("name") or "").upper()
    state = (stype.get("state") or "").lower()
    if "POSTPONED" in name:
        return "PST"
    if "CANCEL" in name:
        return "CANC"
    if "ABANDON" in name:
        return "ABD"
    if state == "pre":
        return "NS"
    if state == "in":
        if any(w in name for w in ("DELAY", "SUSPEND", "INTERRUPT")):
            return "INT"
        if "HALFTIME" in name and "EXTRA" not in name:
            return "HT"
        if "SHOOTOUT" in name or "PENALT" in name:
            return "P"
        if "EXTRA" in name:
            return "ET"
        if "FIRST_HALF" in name:
            return "1H"
        if "SECOND_HALF" in name:
            return "2H"
        return "LIVE"
    if state == "post":
        if "PEN" in name or "SHOOTOUT" in name:
            return "PEN"
        if "AET" in name or "EXTRA" in name:
            return "AET"
        return "FT"
    return "NS"


def _espn_minute(status: dict):
    disp = status.get("displayClock") or (status.get("type") or {}).get("shortDetail") or ""
    m = re.match(r"\s*(\d+)", str(disp))
    if m:
        return int(m.group(1))
    clock = status.get("clock")
    return int(clock // 60) if isinstance(clock, (int, float)) and clock else None


def _espn_events(comp: dict, names: dict) -> list[dict]:
    """ESPN 'details' -> goals and red cards in the engine's event format."""
    out = []
    for d in comp.get("details") or []:
        if d.get("shootout"):
            continue  # penalty shoot-out kicks are not goals
        clock = str((d.get("clock") or {}).get("displayValue") or "")
        m = re.match(r"\s*(\d+)'?(?:\s*\+\s*(\d+))?", clock)
        elapsed = int(m.group(1)) if m else None
        extra = int(m.group(2)) if m and m.group(2) else None
        athletes = d.get("athletesInvolved") or [{}]
        player = athletes[0].get("displayName") or athletes[0].get("fullName") or ""
        team = names.get(str((d.get("team") or {}).get("id")), "")
        if d.get("scoringPlay"):
            kind, detail = "Goal", "Own Goal" if d.get("ownGoal") else "Penalty" if d.get("penaltyKick") else "Normal Goal"
        elif d.get("redCard"):
            kind, detail = "Card", "Red Card"
        else:
            continue
        out.append({
            "type": kind, "detail": detail, "time": {"elapsed": elapsed, "extra": extra},
            "team": {"name": team}, "player": {"name": player}, "assist": {},
        })
    return out


def _from_espn(ev: dict, league_id: int, league_name: str):
    """One ESPN scoreboard event -> the fixture dict the engine understands (or None)."""
    try:
        comp = ev["competitions"][0]
        sides = {c.get("homeAway"): c for c in comp["competitors"]}
        home, away = sides["home"], sides["away"]
        ts = _parse_iso(ev.get("date"))
        if ts is None:
            return None
        status = ev.get("status") or comp.get("status") or {}
        short = _espn_status(status.get("type") or {})

        def team(c):
            t = c.get("team") or {}
            return {"id": str(t.get("id")), "name": t.get("displayName") or t.get("name") or "?",
                    "logo": t.get("logo") or ""}

        def score(c):
            try:
                return int(c.get("score"))
            except (TypeError, ValueError):
                return None

        h, a = team(home), team(away)
        hg, ag = (None, None) if short == "NS" else (score(home), score(away))
        elapsed = _espn_minute(status) if short in LIVE else (90 if short in ENDED_PLAYED else None)
        fx = {
            "fixture": {"id": str(ev["id"]), "timestamp": ts, "status": {"short": short, "elapsed": elapsed}},
            "league": {"id": league_id, "name": league_name},
            "teams": {"home": {"name": h["name"], "logo": h["logo"]}, "away": {"name": a["name"], "logo": a["logo"]}},
            "goals": {"home": hg, "away": ag},
            "events": _espn_events(comp, {h["id"]: h["name"], a["id"]: a["name"]}),
            "score": {},
        }
        sh, sa = home.get("shootoutScore"), away.get("shootoutScore")
        if sh is not None and sa is not None:
            fx["score"] = {"penalty": {"home": sh, "away": sa}}
        return fx
    except (KeyError, IndexError, TypeError):
        return None


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
    if now - _last_sched_try < 300:  # after a failure, wait 5 minutes
        return
    _last_sched_try = now

    # ESPN groups matches by day, so ask for today and tomorrow (UTC) and keep
    # the ones that fall on today's date in the bot's timezone
    dates = [_utc_date(now).replace("-", ""), _utc_date(now + 86400).replace("-", "")]
    fresh, tried, failed = {}, 0, 0
    for lid in _cover_ids():
        slug = ESPN_SLUGS.get(lid)
        if not slug:
            continue
        for d in dates:
            tried += 1
            data, err = await _call(ESPN_SCOREBOARD.format(slug=slug), {"dates": d})
            if err or not isinstance(data, dict):
                failed += 1
                continue
            name = COVER.get(lid) or ((data.get("leagues") or [{}])[0].get("name")) or str(lid)
            for ev in data.get("events") or []:
                fx = _from_espn(ev, lid, name)
                if fx is None:
                    continue
                s = _slim(fx)
                if _local(s["ts"]).strftime("%Y-%m-%d") == today:
                    fresh[str(s["id"])] = s
            await asyncio.sleep(0.3)  # be gentle with a free public service
    if tried and failed == tried:
        return  # everything failed - keep what we have and retry later

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
# ESPN lists a goal under the team that is CREDITED with it, so an own goal is
# already on the right side. Set this to True if own goals show on the wrong side.
OWN_GOAL_CREDITED_TO_OPPONENT = False


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


def _glow(color, cx: int, cy: int, radius: int, alpha: int, size=None):
    layer = Image.new("RGBA", size or (CARD, CARD), (0, 0, 0, 0))
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
        if late or f["status"] in FINISHED:
            st["events"] = [_ev_key(e) for e in relevant]
            st["sent"] += [s for s in ("KO", "HT") if s not in st["sent"]]
            if f["status"] in FINISHED and "FT" not in st["sent"]:
                st["sent"].append("FT")  # an old result - don't re-announce it

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


def _poll_interval(now: float) -> float:
    return BASE_POLL_SECONDS


def _watching(now: float) -> bool:
    for f in _db()["schedule"]["fixtures"].values():
        if f["status"] in FINISHED:
            continue
        if f["status"] in LIVE or f["ts"] - WINDOW_BEFORE <= now <= f["ts"] + WINDOW_AFTER:
            return True
    return False


_last_poll = 0.0


async def _live_poll(bot) -> None:
    """One small request per competition that has a match on or about to start."""
    now = _now()
    targets: dict = {}
    for f in _db()["schedule"]["fixtures"].values():
        if f["status"] in FINISHED:
            continue
        if f["status"] in LIVE or f["ts"] - WINDOW_BEFORE <= now <= f["ts"] + WINDOW_AFTER:
            dt = datetime.fromtimestamp(f["ts"], timezone.utc)
            days = targets.setdefault(f["league_id"], set())
            days.add(dt.strftime("%Y%m%d"))
            if dt.hour < 5:  # ESPN files very early UTC kick-offs under the previous day
                days.add((dt - timedelta(days=1)).strftime("%Y%m%d"))
    for lid, days in targets.items():
        slug = ESPN_SLUGS.get(lid)
        if not slug:
            continue
        for d in sorted(days):
            data, err = await _call(ESPN_SCOREBOARD.format(slug=slug), {"dates": d})
            if err or not isinstance(data, dict):
                continue
            name = COVER.get(lid) or ((data.get("leagues") or [{}])[0].get("name")) or str(lid)
            for ev in data.get("events") or []:
                fx = _from_espn(ev, lid, name)
                if fx is None:
                    continue
                try:
                    await _process(bot, fx)
                except (KeyError, TypeError) as e:
                    logger.warning(f"[Football] skipped a malformed match: {e}")
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


# ---------------------------------------------------------------- league tables
TABLE_LEAGUES = {
    39: "Premier League", 140: "La Liga", 135: "Serie A",
    78: "Bundesliga", 61: "Ligue 1", 2: "Champions League",
}
_ALIASES = {
    39: ("premier league", "premierleague", "epl", "pl", "england", "english"),
    140: ("la liga", "laliga", "spain", "spanish"),
    135: ("serie a", "seriea", "italy", "italian"),
    78: ("bundesliga", "germany", "german"),
    61: ("ligue 1", "ligue1", "france", "french"),
    2: ("champions league", "championsleague", "champions", "ucl", "cl"),
}
TABLE_CACHE_SECONDS = 30 * 60
TABLE_ROW_H = 46
UCL_MAX_ROWS = 24          # the league phase has 36 teams - show the top 24
ZONE_COLORS = {"ucl": (60, 130, 255), "uel": (255, 150, 40), "uecl": (60, 200, 120), "rel": (235, 70, 70)}
ZONE_LABELS = {"ucl": "Champions League", "uel": "Europa League", "uecl": "Conference League", "rel": "Relegation"}
UCL_LABELS = {"ucl": "Last 16", "uel": "Play-offs", "rel": "Eliminated"}


def _league_from_text(text: str):
    t = " ".join((text or "").lower().split())
    if not t:
        return None
    if t.isdigit() and int(t) in TABLE_LEAGUES:
        return int(t)
    for lid, names in _ALIASES.items():
        if t in names or t.replace(" ", "") in names:
            return lid
    return None


def _season_for(now: float) -> int:
    """European seasons start in August: Oct 2026 is the 2026/27 season (id 2026)."""
    d = datetime.fromtimestamp(now, timezone.utc)
    return d.year if d.month >= 7 else d.year - 1


def _zone(desc: str, league_id: int) -> str:
    d = (desc or "").lower()
    if league_id == 2:
        if "eliminat" in d or "relegat" in d:
            return "rel"
        if "play" in d:
            return "uel"
        if "round of 16" in d or "last 16" in d or "promotion" in d:
            return "ucl"
        return ""
    if "relegat" in d:
        return "rel"
    if "conference" in d:
        return "uecl"
    if "europa" in d:
        return "uel"
    if "champions" in d:
        return "ucl"
    return ""


def _parse_table(data, league_id: int):
    """ESPN standings -> table rows (children[].standings.entries[])."""
    entries = []
    for ch in (data.get("children") or []):
        entries = (ch.get("standings") or {}).get("entries") or []
        if entries:
            break
    rows = []
    for i, e in enumerate(entries):
        stats = {s.get("name"): s.get("value") for s in (e.get("stats") or []) if isinstance(s, dict)}
        team = e.get("team") or {}
        logos = team.get("logos") or []
        note = e.get("note") or {}

        def num(*keys, default=0):
            for k in keys:
                v = stats.get(k)
                if v is not None:
                    try:
                        return int(v)
                    except (TypeError, ValueError):
                        pass
            return default

        gd = stats.get("pointDifferential")
        if gd is None:
            gd = num("pointsFor") - num("pointsAgainst")
        rows.append({
            "rank": num("rank", default=int(note.get("rank") or i + 1)),
            "team": team.get("displayName") or team.get("name") or "?",
            "logo": (logos[0].get("href") if logos and isinstance(logos[0], dict) else "") or team.get("logo") or "",
            "p": num("gamesPlayed"), "w": num("wins"), "d": num("ties", "draws"), "l": num("losses"),
            "gd": int(gd), "pts": num("points"),
            "zone": _zone(note.get("description"), league_id),
        })
    rows.sort(key=lambda r: r["rank"])
    return rows


def _table_season(data) -> int | None:
    for node in [data] + list(data.get("children") or []):
        s = node.get("season")
        if isinstance(s, dict) and isinstance(s.get("year"), int):
            return s["year"]
        if isinstance(s, int):
            return s
        st = (node.get("standings") or {}).get("season")
        if isinstance(st, int):
            return st
    return None


async def _get_table(league_id: int):
    """Returns (rows, season, fetched_ts, note). Cached for 30 minutes so a busy
    group can't hammer ESPN. note = None, 'stale' or an error text."""
    now = _now()
    cache = _db()["tables"].get(str(league_id))
    if cache and now - cache["fetched"] < TABLE_CACHE_SECONDS:
        return cache["rows"], cache["season"], cache["fetched"], None
    slug = ESPN_SLUGS.get(league_id)
    data, err = await _call(ESPN_STANDINGS.format(slug=slug), {}) if slug else (None, "unknown competition")
    try:
        if err or not isinstance(data, dict):
            raise ValueError(err or "no table published yet")
        rows = _parse_table(data, league_id)
        if not rows:
            raise ValueError("the table came back empty - /football probe shows what ESPN sent")
    except (KeyError, IndexError, TypeError, ValueError) as e:
        if cache:
            return cache["rows"], cache["season"], cache["fetched"], "stale"
        return None, None, None, str(e)
    season = _table_season(data) or _season_for(now)
    _db()["tables"][str(league_id)] = {"fetched": now, "season": season, "rows": rows}
    _save()
    return rows, season, now, None


def _render_table(name: str, season: int, rows: list[dict], updated: float, league_id: int, total: int):
    """The standings card (JPEG bytes) or None."""
    try:
        W, y0, rh = 1080, 205, TABLE_ROW_H
        n = len(rows)
        H = y0 + 44 + n * rh + 150
        with ThreadPoolExecutor(8) as ex:  # crests download in parallel (cached afterwards)
            list(ex.map(_logo, [r["logo"] for r in rows if r["logo"]]))
        accent = _team_color(_logo(rows[0]["logo"]) if rows and rows[0]["logo"] else None)

        top_c, bot_c = (16, 18, 28), (5, 6, 9)
        grad = Image.linear_gradient("L").resize((W, H))
        canvas = Image.merge("RGB", [grad.point(lambda p, a=top_c[i], b=bot_c[i]: int(a + (b - a) * p / 255)) for i in range(3)]).convert("RGBA")
        canvas = Image.alpha_composite(canvas, _glow(accent, W // 2, 90, 330, 120, (W, H)))
        edges = Image.radial_gradient("L").resize((W, H)).point(lambda p: int((p / 255) ** 2 * 150))
        canvas = Image.composite(Image.new("RGBA", (W, H), (0, 0, 0, 255)), canvas, edges)

        # translucent parts: zebra rows + zone bars
        over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(over)
        ys = y0 + 44
        for i, r in enumerate(rows):
            y = ys + i * rh
            if i % 2 == 0:
                od.rounded_rectangle((30, y + 2, W - 30, y + rh - 2), 12, fill=(255, 255, 255, 14))
            if r["zone"]:
                od.rounded_rectangle((36, y + 8, 42, y + rh - 8), 3, fill=ZONE_COLORS[r["zone"]] + (255,))
        canvas = Image.alpha_composite(canvas, over)

        d = ImageDraw.Draw(canvas)
        white, grey = (255, 255, 255), (150, 153, 166)
        fl, txt = _fit(d, " ".join(_plain(name).upper()), "med", 34, 900, 20)
        d.text((W // 2, 74), txt, font=fl, fill=(215, 217, 226), anchor="mm")
        d.text((W // 2, 122), f"{season}/{str(season + 1)[2:]}  ·  updated {_clock(updated)}", font=_font("reg", 24), fill=grey, anchor="mm")

        cols = {"p": 655, "w": 725, "d": 795, "l": 865, "gd": 945, "pts": 1020}
        d.text((78, y0 + 16), "#", font=_font("med", 22), fill=grey, anchor="mm")
        d.text((170, y0 + 16), "TEAM", font=_font("med", 22), fill=grey, anchor="lm")
        for key, label in (("p", "P"), ("w", "W"), ("d", "D"), ("l", "L"), ("gd", "GD"), ("pts", "PTS")):
            d.text((cols[key], y0 + 16), label, font=_font("med", 22), fill=grey, anchor="mm")

        f_rank, f_num, f_pts = _font("bold", 26), _font("reg", 26), _font("bold", 28)
        for i, r in enumerate(rows):
            cy = ys + i * rh + rh // 2
            d.text((78, cy), str(r["rank"]), font=f_rank, fill=white, anchor="mm")
            logo = _logo(r["logo"]) if r["logo"] else None
            if logo is not None:
                lg = logo.copy()
                lg.thumbnail((34, 34), Image.LANCZOS)
                canvas.alpha_composite(lg, (112 + (34 - lg.width) // 2, cy - lg.height // 2))
            fn, txt = _fit(d, _plain(r["team"]), "med", 28, 455, 18)
            d.text((170, cy), txt, font=fn, fill=white, anchor="lm")
            for key in ("p", "w", "d", "l"):
                d.text((cols[key], cy), str(r[key]), font=f_num, fill=(205, 207, 216), anchor="mm")
            gd = r["gd"]
            d.text((cols["gd"], cy), f"{gd:+d}" if gd else "0", font=f_num, fill=(205, 207, 216), anchor="mm")
            d.text((cols["pts"], cy), str(r["pts"]), font=f_pts, fill=white, anchor="mm")

        # legend (only the zones that exist) + footer
        labels = UCL_LABELS if league_id == 2 else ZONE_LABELS
        present = []
        for r in rows:
            if r["zone"] and r["zone"] not in present:
                present.append(r["zone"])
        ly = ys + n * rh + 34
        x = 50
        fg = _font("reg", 22)
        for z in present:
            d.ellipse((x, ly - 8, x + 16, ly + 8), fill=ZONE_COLORS[z])
            d.text((x + 26, ly), labels.get(z, z), font=fg, fill=(190, 192, 202), anchor="lm")
            x += 26 + d.textlength(labels.get(z, z), font=fg) + 34
        if total > n:
            d.text((W - 50, ly), f"top {n} of {total}", font=fg, fill=grey, anchor="rm")
        d.text((W // 2, H - 44), "G O D D E S S   F O O T B A L L", font=_font("med", 20), fill=(110, 113, 126), anchor="mm")

        img = canvas.convert("RGB")
        grain = Image.effect_noise((W, H), 24).convert("RGB")
        img = Image.blend(img, ImageChops.overlay(img, grain), 0.05)
        out = BytesIO()
        img.save(out, "JPEG", quality=90, optimize=True)
        return out.getvalue()
    except Exception as e:
        logger.warning(f"[Football] could not render the table: {e}")
        return None


def _table_text(name: str, rows: list[dict]) -> str:
    """Plain monospace fallback if the card can't be drawn."""
    lines = [f"{'#':>2} {'Team':<16}{'P':>3}{'GD':>4}{'Pts':>4}"]
    for r in rows:
        lines.append(f"{r['rank']:>2} {_plain(r['team'])[:15]:<16}{r['p']:>3}{r['gd']:>+4d}{r['pts']:>4}")
    return f"<b>{html.escape(name)}</b>\n<pre>{html.escape(chr(10).join(lines))}</pre>"


async def _send_table(message, league_id: int) -> None:
    name = TABLE_LEAGUES[league_id]
    rows, season, fetched, note = await _get_table(league_id)
    if rows is None:
        await message.reply_text(f"Couldn't get the {name} table: {note}")
        return
    total = len(rows)
    rows = rows[: UCL_MAX_ROWS if league_id == 2 else 24]
    caption = f"🏆 <b>{html.escape(name)}</b> · {season}/{str(season + 1)[2:]}"
    if note == "stale":
        caption += "\n<i>couldn't refresh just now - showing the last table I have</i>"
    card = await asyncio.to_thread(_render_table, name, season, rows, fetched, league_id, total)
    try:
        if card:
            buf = BytesIO(card)
            buf.name = "table.jpg"
            await message.reply_photo(buf, caption=caption, parse_mode=ParseMode.HTML)
            return
    except TelegramError as e:
        logger.warning(f"[Football] could not send the table card: {e}")
    await message.reply_text(_table_text(name, rows), parse_mode=ParseMode.HTML)


def _table_picker() -> InlineKeyboardMarkup:
    items = list(TABLE_LEAGUES.items())
    rows = [
        [InlineKeyboardButton(n, callback_data=f"fbt:{i}") for i, n in items[k:k + 2]]
        for k in range(0, len(items), 2)
    ]
    return InlineKeyboardMarkup(rows)


async def table_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    try:
        league_id = int((q.data or "").split(":")[1])
    except (IndexError, ValueError):
        await q.answer()
        return
    if league_id not in TABLE_LEAGUES:
        await q.answer()
        return
    await q.answer("Getting the table... 🏆")
    await _send_table(q.message, league_id)


# ---------------------------------------------------------------- /football
async def _probe() -> str:
    """What does ESPN actually return? One line per competition (admins only)."""
    lines = ["ESPN check (unofficial source)", ""]
    day = _utc_date(_now()).replace("-", "")
    for lid in _cover_ids():
        slug = ESPN_SLUGS.get(lid)
        label = COVER.get(lid, str(lid))
        if not slug:
            lines.append(f"{label}: no ESPN slug set")
            continue
        data, err = await _call(ESPN_SCOREBOARD.format(slug=slug), {"dates": day})
        if err or not isinstance(data, dict):
            lines.append(f"{label} ({slug}): FAILED - {(err or 'no data')[:70]}")
        else:
            events = data.get("events") or []
            readable = sum(1 for e in events if _from_espn(e, lid, label))
            details = sum(len(((e.get("competitions") or [{}])[0]).get("details") or []) for e in events)
            lines.append(f"{label} ({slug}): {len(events)} matches today, {readable} readable, {details} goal/card rows")
        await asyncio.sleep(0.3)
    for lid in (39, 2):
        data, err = await _call(ESPN_STANDINGS.format(slug=ESPN_SLUGS[lid]), {})
        try:
            n = len(_parse_table(data, lid)) if isinstance(data, dict) else 0
            lines.append(f"table {COVER[lid]}: {n} rows" if not err else f"table {COVER[lid]}: FAILED - {err[:60]}")
        except Exception as e:
            lines.append(f"table {COVER[lid]}: could not read ({type(e).__name__})")
    return "\n".join(lines)

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

    if sub == "table":
        league_id = _league_from_text(" ".join(context.args[1:]))
        if league_id is None:
            await m.reply_text("Which table? Pick one:", reply_markup=_table_picker())
        else:
            await _send_table(m, league_id)
        return

    if sub in ("on", "off", "status", "probe"):
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
        elif sub == "probe":
            await m.reply_text("Checking ESPN for every competition, one moment...")
            await m.reply_text(await _probe())
        else:
            u = db["usage"]
            used = u["calls"] if u["date"] == _utc_date(_now()) else 0
            names = ", ".join(COVER.get(i, str(i)) for i in _cover_ids())
            await m.reply_text(
                f"Football updates: {'ON' if conf['enabled'] else 'OFF'}\n"
                f"Source: ESPN public data (unofficial)\n"
                f"Competitions: {names}\n"
                f"Requests made today: {used}\n"
                f"Last problem: {db['last_error'] or 'none'}"
            )
        return

    # /football  - today's matches (from the cache, no request unless it's stale)
    await _refresh_schedule()
    await m.reply_text(_today_text(), parse_mode=ParseMode.HTML)


def register(app) -> None:
    app.add_handler(CommandHandler("football", football_cmd, filters=filters.ChatType.GROUPS))
    app.add_handler(CallbackQueryHandler(table_callback, pattern="^fbt:"), group=11)
