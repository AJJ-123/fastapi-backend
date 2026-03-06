print("⚡ ACCAGENIUS ULTIMATE - AI BETTING INTELLIGENCE PLATFORM ⚡")

import os
import requests
import random
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
    _LONDON = ZoneInfo("Europe/London")
except ImportError:
    _LONDON = None
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logger = logging.getLogger("accagenius")

# =========================
# CONFIG
# =========================
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "0192e664450828fc0345770b74b75e9f")
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {
    "x-rapidapi-key": API_FOOTBALL_KEY,
    "x-rapidapi-host": "v3.football.api-sports.io"
}

# Telegram config — reads YOUR Railway variable names
TELEGRAM_BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "8459345549:AAEwCGBLjwijbVliHY1Qaw9KB-fybfu0Pa8")
# Read your exact variable names first, then fallbacks
TELEGRAM_CHANNEL_FREE = (
    os.getenv("FREE_CHANNEL_ID", "") or
    os.getenv("TELEGRAM_CHANNEL_FREE", "") or
    os.getenv("TELEGRAM_CHANNEL_ID", "")
)
TELEGRAM_CHANNEL_PRO  = (
    os.getenv("PRO_CHANNEL_ID", "") or
    os.getenv("TELEGRAM_CHANNEL_PRO", "") or
    os.getenv("TELEGRAM_CHANNEL_ID", "")
)
TELEGRAM_CHANNEL_ID   = TELEGRAM_CHANNEL_PRO or TELEGRAM_CHANNEL_FREE

# ── EMAIL (Resend) ───────────────────────────────────────────────
RESEND_API_KEY           = os.getenv("RESEND_API_KEY", "re_LuMWocBr_AwT1A7BeJ4dF1Wzrsc7Q84Ae")
EMAIL_FROM               = os.getenv("EMAIL_FROM", "AccaGenius <noreply@accagenius.com>")
SITE_URL                 = os.getenv("SITE_URL", "https://accagenius.com")
_reset_tokens: dict = {}  # token -> {email, name, expires, used}

# ── ACCESS CODES ─────────────────────────────────────────────────
PRO_ACCESS_CODES_RAW     = os.getenv("PRO_ACCESS_CODES", "ACCA-X7K2,ACCA-M9P4")
_all_codes: set          = set(c.strip().upper() for c in PRO_ACCESS_CODES_RAW.split(",") if c.strip())
_used_codes: dict        = {}  # code -> {email, name, used_at}
TELEGRAM_PRO_INVITE_LINK = os.getenv("TELEGRAM_PRO_INVITE_LINK", "https://t.me/+6hHYweKkeEs3Mjk0")

# ── ADMIN PASSWORD ───────────────────────────────────────────────
# Support both variable names - AJ_ADMIN_2026 (current) and ADMIN_PASSWORD (standard)
ADMIN_PASSWORD_ENV = os.getenv("ADMIN_PASSWORD", "") or os.getenv("AJ_ADMIN_2026", "")

# Daily limits for free channel
free_alerts_sent_today  = 0   # max 1 per day on free
free_alert_reset_date   = ""

# Alert thresholds
ALERT_WIN_PCT    = 65    # Win % must be >= this
ALERT_XG_GAP    = 0.4   # xG gap between teams must be >= this
ALERT_MINUTE_MIN = 20   # Don't alert before 20th minute
ALERT_MINUTE_MAX = 75   # Don't alert after 75th minute

app = FastAPI(title="AccaGenius Ultimate API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi.responses import JSONResponse
from fastapi.requests import Request

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all — return clean JSON instead of crashing and flooding logs"""
    logger.error(f"Unhandled error on {request.url.path}: {type(exc).__name__}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": str(exc), "path": str(request.url.path)}
    )

# In-memory store
saved_accas_store: List[dict] = []

# =========================
# SMART API CACHE
# Saves your 100 calls/day free quota by serving cached data
# TTLs chosen to balance freshness vs API spend:
#   today fixtures  — 20 min  (live scores handled by /live endpoint separately)
#   next-round      — 60 min  (fixtures don't change often)
#   standings       — 120 min (table changes only after matches)
#   form/h2h        — 180 min (historical — barely changes)
#   odds            — 15 min  (odds shift but slowly)
#   live scores     — 60 sec  (must be fresh — but only called when XG tab open)
# =========================
_cache: Dict[str, Any] = {}

# ────────────────────────────────────────────────────────────────
# EMAIL  (Resend API — resend.com)
# ────────────────────────────────────────────────────────────────

def send_email(to: str, subject: str, html_body: str) -> bool:
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — email skipped")
        return False
    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": EMAIL_FROM, "to": [to], "subject": subject, "html": html_body},
            timeout=10
        )
        ok = r.status_code in (200, 201)
        if ok:
            logger.info(f"✉️  Email sent: {subject} → {to}")
        else:
            logger.error(f"Email failed {r.status_code}: {r.text[:200]}")
        return ok
    except Exception as e:
        logger.error(f"Email error: {e}")
        return False


def _email_base(body_html: str) -> str:
    """Wrap content in branded AccaGenius email shell."""
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{margin:0;padding:0;background:#0a0c12;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}}</style>
</head><body>
<div style="max-width:560px;margin:0 auto;padding:32px 16px">
  <div style="text-align:center;margin-bottom:28px">
    <div style="display:inline-block;background:linear-gradient(135deg,#f0b429,#f6ad55);border-radius:14px;padding:10px 20px">
      <span style="font-size:20px;font-weight:900;color:#000;letter-spacing:1px">⚡ ACCAGENIUS</span>
    </div>
  </div>
  {body_html}
  <div style="text-align:center;color:#4b5563;font-size:12px;margin-top:28px;line-height:1.8">
    AccaGenius · <a href="{SITE_URL}" style="color:#4cc9f0;text-decoration:none">accagenius.com</a><br>
    Please gamble responsibly · 18+ only ·
    <a href="https://www.begambleaware.org" style="color:#4cc9f0;text-decoration:none">BeGambleAware.org</a>
  </div>
</div>
</body></html>"""


def email_welcome_pro(to: str, name: str, code: str, telegram_invite: str = "") -> bool:
    tg = f"""<div style="background:#0d1b2a;border:1px solid #4cc9f0;border-radius:10px;padding:16px;margin:16px 0;text-align:center">
      <div style="font-size:22px;margin-bottom:6px">📲</div>
      <div style="font-weight:700;color:#4cc9f0;margin-bottom:6px">Join Your Pro Telegram Channel</div>
      <div style="color:#8b9cbf;font-size:13px;margin-bottom:12px">Daily accas, live in-play alerts &amp; xG analysis straight to your phone</div>
      <a href="{telegram_invite}" style="display:inline-block;background:linear-gradient(90deg,#4cc9f0,#7c3aed);color:#fff;font-weight:700;padding:10px 24px;border-radius:8px;text-decoration:none;font-size:14px">Join AccaGenius Pro →</a>
    </div>""" if telegram_invite else ""

    features = [
        ("AI Acca Generator", "Balanced, safe &amp; risky picks across 19 leagues"),
        ("Live xG Tracker", "In-play expected goals with Goal Due alerts"),
        ("Today's Acca of the Day", "3 themed accas every morning — Form, Rank &amp; Value"),
        ("HT Value Finder", "First-half patterns and halftime picks"),
        ("Full Match Analysis", "H2H, form, lineups, predictions &amp; best odds"),
        ("Saved Accas &amp; P&amp;L", "Track every bet and monitor your profit"),
    ]
    feat_html = "".join(
        f'<div style="display:flex;gap:10px;margin-bottom:10px"><span style="color:#f0b429">⚡</span>'
        f'<div><div style="color:#fff;font-size:14px;font-weight:600">{f[0]}</div>'
        f'<div style="color:#8b9cbf;font-size:12px">{f[1]}</div></div></div>'
        for f in features
    )
    body = f"""
    <div style="background:#111827;border-radius:16px;padding:28px;margin-bottom:16px;border:1px solid #1f2937">
      <h1 style="color:#f0b429;font-size:22px;margin:0 0 10px;font-weight:900">Welcome to the Pro, {name}! 🎉</h1>
      <p style="color:#8b9cbf;font-size:14px;margin:0 0 20px;line-height:1.6">
        Your Pro access is now active. Full AI picks, live stats, xG analysis — everything is unlocked.
      </p>
      <div style="background:#0d1b2a;border:1px solid #1f2937;border-radius:10px;padding:14px;margin-bottom:16px">
        <div style="color:#8b9cbf;font-size:11px;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">Your Access Code</div>
        <div style="font-family:monospace;font-size:22px;font-weight:900;color:#f0b429;letter-spacing:4px">{code}</div>
        <div style="color:#8b9cbf;font-size:11px;margin-top:4px">Keep this safe — you'll need it if you sign in on a new device</div>
      </div>
      {tg}
      <div style="text-align:center;margin-top:20px">
        <a href="{SITE_URL}" style="display:inline-block;background:linear-gradient(135deg,#f0b429,#f6ad55);color:#000;font-weight:800;padding:13px 32px;border-radius:10px;text-decoration:none;font-size:15px">🚀 Go to AccaGenius</a>
      </div>
    </div>
    <div style="background:#111827;border-radius:16px;padding:22px;border:1px solid #1f2937">
      <h2 style="color:#fff;font-size:15px;margin:0 0 14px;font-weight:700">✅ What's included in your Pro plan</h2>
      {feat_html}
    </div>"""
    return send_email(to, "⚡ Welcome to AccaGenius Pro — you're in!", _email_base(body))


def email_password_reset(to: str, name: str, token: str) -> bool:
    reset_url = f"{SITE_URL}?reset={token}"
    body = f"""
    <div style="background:#111827;border-radius:16px;padding:28px;border:1px solid #1f2937">
      <div style="font-size:36px;text-align:center;margin-bottom:12px">🔑</div>
      <h1 style="color:#fff;font-size:20px;margin:0 0 10px;text-align:center;font-weight:800">Reset Your Password</h1>
      <p style="color:#8b9cbf;font-size:14px;margin:0 0 24px;text-align:center;line-height:1.6">
        Hi {name}, tap below to reset your AccaGenius password.<br>
        <strong style="color:#f0b429">This link expires in 1 hour.</strong>
      </p>
      <div style="text-align:center;margin-bottom:22px">
        <a href="{reset_url}" style="display:inline-block;background:linear-gradient(135deg,#f0b429,#f6ad55);color:#000;font-weight:800;padding:13px 32px;border-radius:10px;text-decoration:none;font-size:15px">Reset My Password</a>
      </div>
      <div style="background:#0d1b2a;border-radius:8px;padding:12px;font-size:12px;color:#8b9cbf;text-align:center">
        If you didn't request this, ignore this email. Your password won't change.
      </div>
    </div>"""
    return send_email(to, "🔑 Reset your AccaGenius password", _email_base(body))


def cache_get(key: str) -> Any:
    """Return cached value if still fresh, else None."""
    entry = _cache.get(key)
    if entry and datetime.now() < entry["expires"]:
        return entry["data"]
    return None

def cache_set(key: str, data: Any, ttl_seconds: int):
    """Store data in cache with expiry."""
    _cache[key] = {"data": data, "expires": datetime.now() + timedelta(seconds=ttl_seconds)}

def cache_stats() -> dict:
    """Return cache hit counts and remaining quota estimate."""
    now = datetime.now()
    live_keys = [k for k, v in _cache.items() if now < v["expires"]]
    return {"cached_keys": len(live_keys), "total_keys": len(_cache)}

# Track API calls to warn when approaching daily limit
_api_call_count = 0
_api_call_reset = datetime.now().date()

def api_get(endpoint: str, params: dict, timeout: int = 10) -> dict:
    """Safe API call wrapper with quota tracking."""
    global _api_call_count, _api_call_reset
    # Reset counter each day
    today = datetime.now().date()
    if today != _api_call_reset:
        _api_call_count = 0
        _api_call_reset = today
    _api_call_count += 1
    if _api_call_count % 500 == 0 or _api_call_count > 6000:
        pct = round(_api_call_count / 7500 * 100, 1)
        logger.warning(f"📊 API quota: {_api_call_count}/7500 ({pct}%) calls today")
    if _api_call_count > 7400:
        logger.error(f"🛑 API call blocked — at {_api_call_count}/7500 daily limit")
        return {}
    try:
        r = requests.get(f"{BASE_URL}/{endpoint}", headers=HEADERS, params=params, timeout=timeout)
        if r.status_code == 429:
            logger.error(f"🚫 API rate limit hit (429) on /{endpoint} — {_api_call_count} calls today")
            return {}
        if r.status_code == 200:
            return r.json()
        return {}
    except Exception as e:
        logger.error(f"api_get error on /{endpoint}: {e}")
        return {}

# Track which fixtures we've already alerted — resets each day
alerted_fixtures: set = set()
alerted_details: dict = {}  # fixture_id -> {home, away, bet, odds, channel} for result posting
last_alert_reset: str = ""

# =========================
# LEAGUES (17)
# =========================
LEAGUES = [
    {"code": "PL",   "name": "Premier League",         "country": "England",     "id": 39,  "flag": "ENG"},
    {"code": "ELC",  "name": "Championship",            "country": "England",     "id": 40,  "flag": "ENG"},
    {"code": "EL1",  "name": "League One",              "country": "England",     "id": 41,  "flag": "ENG"},
    {"code": "EL2",  "name": "League Two",              "country": "England",     "id": 42,  "flag": "ENG"},
    {"code": "CL",   "name": "Champions League",        "country": "Europe",      "id": 2,   "flag": "EUR"},
    {"code": "EL",   "name": "Europa League",           "country": "Europe",      "id": 3,   "flag": "EUR"},
    {"code": "ECL",  "name": "Conference League",       "country": "Europe",      "id": 848, "flag": "EUR"},
    {"code": "FL1",  "name": "Ligue 1",                 "country": "France",      "id": 61,  "flag": "FRA"},
    {"code": "FL2",  "name": "Ligue 2",                 "country": "France",      "id": 62,  "flag": "FRA"},
    {"code": "BL1",  "name": "Bundesliga",              "country": "Germany",     "id": 78,  "flag": "GER"},
    {"code": "BL2",  "name": "2. Bundesliga",           "country": "Germany",     "id": 79,  "flag": "GER"},
    {"code": "SA",   "name": "Serie A",                 "country": "Italy",       "id": 135, "flag": "ITA"},
    {"code": "NED",  "name": "Eredivisie",              "country": "Netherlands", "id": 88,  "flag": "NED"},
    {"code": "PPL",  "name": "Primeira Liga",           "country": "Portugal",    "id": 94,  "flag": "POR"},
    {"code": "PD",   "name": "La Liga",                 "country": "Spain",       "id": 140, "flag": "ESP"},
    {"code": "TUR",  "name": "Super Lig",               "country": "Turkey",      "id": 203, "flag": "TUR"},
    {"code": "BEL",  "name": "Belgium Pro League",      "country": "Belgium",     "id": 144, "flag": "BEL"},
    {"code": "POL",  "name": "Ekstraklasa",             "country": "Poland",      "id": 106, "flag": "POL"},
    {"code": "SPFL", "name": "Scottish Premiership",    "country": "Scotland",    "id": 179, "flag": "SCO"},
]
LEAGUE_IDS = {l["code"]: l["id"] for l in LEAGUES}
LEAGUE_INFO = {l["code"]: l for l in LEAGUES}

def get_season() -> int:
    now = datetime.now()
    return now.year if now.month >= 8 else now.year - 1

# =========================
# TELEGRAM ALERTS
# =========================

def send_telegram(message: str) -> bool:
    """Send message to Telegram channel. Returns True on success."""
    channel = TELEGRAM_CHANNEL_PRO or TELEGRAM_CHANNEL_ID or TELEGRAM_CHANNEL_FREE
    if not TELEGRAM_BOT_TOKEN or not channel:
        logger.warning("Telegram not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_PRO in Railway Variables")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": normalise_channel(channel),
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            logger.info(f"Telegram alert sent: {message[:60]}")
            return True
        else:
            logger.error(f"Telegram error {r.status_code}: {r.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"Telegram exception: {e}")
        return False


def score_live_match(fixture_id: int, home: str, away: str,
                     home_score: int, away_score: int,
                     minute: int, league: str) -> Optional[dict]:
    """
    Score a live match against all three alert conditions.
    Returns alert dict if all conditions met, else None.
    """
    # Condition 1: Minute range — not too early or too late
    if not (ALERT_MINUTE_MIN <= minute <= ALERT_MINUTE_MAX):
        return None

    # Fetch live stats
    stats_data = api_get("fixtures/statistics", {"fixture": fixture_id})
    stats_raw = stats_data.get("response", [])
    if len(stats_raw) < 2:
        return None

    FIELD_MAP = {
        "Shots on Goal": "shots_on_goal", "Total Shots": "total_shots",
        "Shots insidebox": "shots_inside_box", "Goalkeeper Saves": "goalkeeper_saves",
        "Ball Possession": "ball_possession", "Corner Kicks": "corner_kicks",
        "Expected Goals": "expected_goals", "expected_goals": "expected_goals",
    }

    team_stats = {}
    for td in stats_raw:
        tname = td["team"]["name"]
        ts = {}
        for s in td.get("statistics", []):
            key = FIELD_MAP.get(s["type"]) or s["type"].lower().replace(" ", "_")
            val = s.get("value")
            if isinstance(val, str) and val.endswith("%"):
                try: val = int(val.replace("%", ""))
                except: pass
            ts[key] = val if val is not None else 0
        team_stats[tname] = ts

    hs = team_stats.get(home, {})
    as_ = team_stats.get(away, {})

    # ── xG: use real if available, else estimate ──
    h_xg = float(hs.get("expected_goals") or 0)
    a_xg = float(as_.get("expected_goals") or 0)
    if h_xg == 0 and a_xg == 0:
        # Estimate from shots
        h_shots_on = float(hs.get("shots_on_goal") or 0)
        a_shots_on = float(as_.get("shots_on_goal") or 0)
        h_inside   = float(hs.get("shots_inside_box") or 0)
        a_inside   = float(as_.get("shots_inside_box") or 0)
        h_saves    = float(as_.get("goalkeeper_saves") or 0)  # opp keeper saves = h attacks
        a_saves    = float(hs.get("goalkeeper_saves") or 0)
        h_xg = round(h_shots_on * 0.33 + h_inside * 0.08 + h_saves * 0.15, 2)
        a_xg = round(a_shots_on * 0.33 + a_inside * 0.08 + a_saves * 0.15, 2)
        xg_real = False
    else:
        xg_real = True

    xg_gap = round(h_xg - a_xg, 2)

    # ── Win % from score + xG + possession ──
    h_poss = float(hs.get("ball_possession") or 50)
    a_poss = float(as_.get("ball_possession") or 50)
    score_diff = home_score - away_score

    # Base win % from current score using Poisson-style logic
    # Weight: score (50%) + xG advantage (30%) + possession (20%)
    score_weight = max(0, min(40, score_diff * 18))  # each goal = ~18%
    xg_weight    = max(-20, min(20, xg_gap * 15))
    poss_weight  = round((h_poss - 50) * 0.15, 1)
    base_home    = 50 + score_weight + xg_weight + poss_weight
    h_win_pct    = round(max(5, min(95, base_home)))
    a_win_pct    = round(max(5, min(95, 100 - h_win_pct - 10)))
    draw_pct     = max(0, 100 - h_win_pct - a_win_pct)

    # ── Condition 2: Win % >= threshold ──
    if h_win_pct < ALERT_WIN_PCT and a_win_pct < ALERT_WIN_PCT:
        return None

    # ── Condition 3: xG gap meaningful ──
    if abs(xg_gap) < ALERT_XG_GAP and h_xg + a_xg > 0.1:
        return None

    # ── Determine favoured team ──
    if h_win_pct >= ALERT_WIN_PCT:
        fav_team   = home
        fav_win_pct = h_win_pct
        fav_xg     = h_xg
        opp_xg     = a_xg
        bet        = f"{home} Win"
    else:
        fav_team   = away
        fav_win_pct = a_win_pct
        fav_xg     = a_xg
        opp_xg     = h_xg
        xg_gap     = abs(xg_gap)
        bet        = f"{away} Win"

    # ── Condition: Scored first (momentum check) ──
    # If favoured team is winning on score, that's the first goal signal
    scored_first = (h_win_pct >= ALERT_WIN_PCT and home_score > away_score) or \
                   (a_win_pct >= ALERT_WIN_PCT and away_score > home_score)
    # Allow if xG dominance is extreme even without a goal yet
    if not scored_first and xg_gap < 0.8:
        return None

    # ── Fetch live odds ──
    odds_data = get_real_odds(fixture_id)
    h_odds = odds_data.get("home", 0)
    a_odds = odds_data.get("away", 0)
    d_odds = odds_data.get("draw", 0)
    fav_odds = h_odds if fav_team == home else a_odds
    if not fav_odds or fav_odds <= 1.01:
        fav_odds_str = "N/A"
    else:
        fav_odds_str = f"{fav_odds:.2f}"

    xg_label = "Real xG" if xg_real else "Est. xG"

    return {
        "fixture_id": fixture_id,
        "home": home, "away": away,
        "score": f"{home_score}-{away_score}",
        "minute": minute,
        "league": league,
        "h_win_pct": h_win_pct,
        "a_win_pct": a_win_pct,
        "draw_pct": draw_pct,
        "h_xg": h_xg, "a_xg": a_xg,
        "xg_gap": round(xg_gap, 2),
        "xg_real": xg_real,
        "xg_label": xg_label,
        "fav_team": fav_team,
        "fav_win_pct": fav_win_pct,
        "bet": bet,
        "fav_odds": fav_odds_str,
        "h_odds": h_odds, "d_odds": d_odds, "a_odds": a_odds,
        "scored_first": scored_first,
    }


def build_telegram_message(alert: dict) -> str:
    """Full PRO message — all stats, xG, win %, odds."""
    minute = alert['minute']
    score  = alert['score']
    league = alert['league']
    home   = alert['home']
    away   = alert['away']
    bet    = alert['bet']
    odds   = alert['fav_odds']
    h_wp   = alert['h_win_pct']
    a_wp   = alert['a_win_pct']
    h_xg   = alert['h_xg']
    a_xg   = alert['a_xg']
    xg_lbl = alert['xg_label']
    first  = "✅ Scored first" if alert['scored_first'] else "📊 xG dominance"

    return (
        f"🟢 <b>ACCAGENIUS PRO ALERT</b> 🟢\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚽ <b>{home} vs {away}</b>\n"
        f"🏆 {league}\n"
        f"🕐 {minute}' | Score: <b>{score}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Win Probability</b>\n"
        f"  {home}: <b>{h_wp}%</b> 🟢\n"
        f"  Draw: {alert['draw_pct']}%\n"
        f"  {away}: {a_wp}%\n"
        f"\n"
        f"⚡ <b>{xg_lbl}</b>\n"
        f"  {home}: {h_xg}  |  {away}: {a_xg}\n"
        f"  Gap: {alert['xg_gap']:+.2f}\n"
        f"\n"
        f"🔥 <b>Trigger: {first}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Suggested Bet: {bet}</b>\n"
        f"📈 Current Odds: <b>{odds}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <i>Bet responsibly. 18+ only.</i>\n"
        f"🤖 AccaGenius Pro | Live Intelligence"
    )


def build_free_message(alert: dict) -> str:
    """Teaser FREE message — just the pick, score, minute. No xG or win %."""
    return (
        f"🟡 <b>ACCAGENIUS FREE TIP</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚽ <b>{alert['home']} vs {alert['away']}</b>\n"
        f"🕐 {alert['minute']}' | Score: <b>{alert['score']}</b>\n"
        f"\n"
        f"💰 <b>In-Play Tip: {alert['bet']}</b>\n"
        f"\n"
        f"🔒 <i>Win probability, xG stats & full analysis</i>\n"
        f"🔒 <i>available on AccaGenius Pro</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👉 <b>Upgrade: accagenius.com</b>\n"
        f"⚠️ <i>Bet responsibly. 18+ only.</i>"
    )


async def live_alert_scanner():
    """Background task — scans live matches every 5 minutes.
    Also sends Today's Acca at 9am and live alerts throughout the day."""
    global alerted_fixtures, last_alert_reset
    global free_alerts_sent_today, free_alert_reset_date
    logger.info("🤖 Telegram dual-channel scanner started")

    acca_sent_date = ""  # tracks which date we've already sent the morning acca

    while True:
        try:
            now   = datetime.now()
            today = now.strftime("%Y-%m-%d")
            hour  = now.hour
            minute = now.minute

            # ── Reset daily trackers at midnight ──
            if today != last_alert_reset:
                alerted_fixtures = set()
                alerted_details  = {}
                last_alert_reset = today
                logger.info("Alert tracker reset for new day")

            if today != free_alert_reset_date:
                free_alerts_sent_today = 0
                free_alert_reset_date  = today
                logger.info("Free alert counter reset for new day")

            # Need at least bot token to do anything
            if not TELEGRAM_BOT_TOKEN:
                await asyncio.sleep(300)
                continue

            has_pro  = bool(TELEGRAM_CHANNEL_PRO or TELEGRAM_CHANNEL_ID)
            has_free = bool(TELEGRAM_CHANNEL_FREE)

            if not has_pro and not has_free:
                await asyncio.sleep(300)
                continue

            # ── 9AM: Send Today's Acca to both channels ──
            if hour == 9 and acca_sent_date != today:
                acca_sent_date = today  # mark immediately so we don't double-send
                pro_channel = TELEGRAM_CHANNEL_PRO or TELEGRAM_CHANNEL_ID
                try:
                    # Generate picks fresh for today
                    acca_data = cache_get(f"today_acca_{today}")
                    if not acca_data:
                        # Trigger generation — reuse the endpoint logic inline
                        logger.info("9am: generating today's acca for Telegram...")
                        # Re-fetch after potential generation
                    acca_data = cache_get(f"today_acca_{today}")
                    sep = "━" * 20

                    if acca_data and any(a.get("picks") for a in acca_data.get("accas", [])):
                        for acca in acca_data.get("accas", []):
                            picks = acca.get("picks", [])
                            if not picks:
                                continue
                            label = acca.get("label", "Acca")
                            emoji = acca.get("emoji", "⚽")
                            total_odds = round(
                                __import__("functools").reduce(
                                    lambda a, p: a * float(p.get("odds", 1.75)), picks, 1.0
                                ), 2
                            )
                            legs = ""
                            for p in picks:
                                legs += "  ⚽ " + p.get("home","") + " vs " + p.get("away","") + "\n"
                                legs += "     ✅ " + str(p.get("bet","")) + " @ " + str(p.get("odds","")) + "\n"

                            if has_pro:
                                pro_msg = (
                                    emoji + " <b>TODAY'S ACCA — " + label.upper() + "</b>\n"
                                    + sep + "\n" + legs + sep + "\n"
                                    + "💰 Combined: <b>" + str(total_odds) + "x</b>\n"
                                    + "📅 " + today + " · Match Winner\n"
                                    + "🤖 AccaGenius · accagenius.com"
                                )
                                send_telegram_to(pro_msg, pro_channel)

                            if has_free:
                                free_msg = (
                                    emoji + " <b>TODAY'S ACCA — " + label.upper() + "</b>\n"
                                    + sep + "\n"
                                    + "💰 Combined odds: <b>" + str(total_odds) + "x</b> · " + str(len(picks)) + " picks\n"
                                    + sep + "\n"
                                    + "🔒 Full picks → accagenius.com\n"
                                    + "👉 Upgrade to Pro for full analysis"
                                )
                                send_telegram_to(free_msg, TELEGRAM_CHANNEL_FREE)

                        logger.info(f"9am acca sent: {len(acca_data.get('accas', []))} accas")

                    else:
                        # No picks available — send "no acca today" message
                        no_acca_msg = (
                            "📅 <b>ACCAGENIUS — " + today + "</b>\n"
                            + sep + "\n"
                            + "⚠️ <b>No Acca Today</b>\n"
                            + "Not enough qualifying fixtures today.\n"
                            + "Check back for in-play alerts during live matches!\n"
                            + "🤖 AccaGenius · accagenius.com"
                        )
                        if has_pro:
                            send_telegram_to(no_acca_msg, pro_channel)
                        if has_free:
                            send_telegram_to(no_acca_msg, TELEGRAM_CHANNEL_FREE)
                        logger.info("9am: no acca available — sent no-acca message")

                except Exception as e:
                    logger.error(f"9am acca send error: {e}")

            # Fetch live matches — use cache to avoid burning API quota
            # Cache is 90s so still near-real-time
            cached_live = cache_get("live_matches")
            if cached_live:
                live_matches = cached_live.get("matches", [])
                logger.info(f"Scanner: using cached live data ({len(live_matches)} matches)")
            else:
                live_data = api_get("fixtures", {"live": "all", "timezone": "Europe/London"})
                our_league_ids = {l["id"] for l in LEAGUES}
                live_matches = []
                for f in live_data.get("response", []):
                    try:
                        if f["league"]["id"] not in our_league_ids:
                            continue
                        live_matches.append({
                            "id":         f["fixture"]["id"],
                            "home":       f["teams"]["home"]["name"],
                            "away":       f["teams"]["away"]["name"],
                            "home_score": f["goals"]["home"] or 0,
                            "away_score": f["goals"]["away"] or 0,
                            "minute":     f["fixture"]["status"].get("elapsed") or 0,
                            "league":     f["league"]["name"],
                        })
                    except Exception:
                        pass
                # Store in cache for 90s so next scan can reuse
                cache_set("live_matches", {"matches": live_matches}, 90)
                logger.info(f"Scanner: fresh live fetch — {len(live_matches)} matches")

            pro_channel = TELEGRAM_CHANNEL_PRO or TELEGRAM_CHANNEL_ID

            # ── CHECK RESULTS of previously alerted matches ──
            live_ids = {m["id"] for m in live_matches}
            for fid, details in list(alerted_details.items()):
                if details.get("result_posted"): continue
                if fid in live_ids: continue  # Still playing
                # Match finished — fetch result (cache 30 mins so we don't re-fetch each scan)
                try:
                    result_cache_key = f"result_{fid}"
                    fr_data = cache_get(result_cache_key)
                    if not fr_data:
                        fd = api_get("fixtures", {"id": fid, "timezone": "Europe/London"})
                        fr_data = fd.get("response", [{}])[0] if fd.get("response") else {}
                        if fr_data:
                            cache_set(result_cache_key, fr_data, 1800)  # 30 min cache
                    fr = fr_data
                    status = fr.get("fixture", {}).get("status", {}).get("short", "")
                    if status in ["FT", "AET", "PEN"]:
                        hs = fr.get("goals", {}).get("home", 0) or 0
                        as_ = fr.get("goals", {}).get("away", 0) or 0
                        bet = details.get("bet", "")
                        home = details.get("home", "")
                        away = details.get("away", "")
                        odds = details.get("odds", 2.0)
                        # Evaluate result
                        won = False
                        if home + " Win" in bet: won = hs > as_
                        elif away + " Win" in bet: won = as_ > hs
                        elif "Draw" in bet: won = hs == as_
                        elif "Both Teams" in bet or "BTTS" in bet: won = hs > 0 and as_ > 0
                        elif "Over 2.5" in bet: won = (hs + as_) > 2.5
                        elif "Over 1.5" in bet: won = (hs + as_) > 1.5

                        result = "won" if won else "lost"
                        stake = telegram_pl["stake"]
                        telegram_pl["tips"] += 1
                        if won:
                            profit = round(stake * odds - stake, 2)
                            telegram_pl["won"] += 1
                            telegram_pl["profit"] = round(telegram_pl["profit"] + profit, 2)
                            pl_line = f"Won:{telegram_pl['won']} Lost:{telegram_pl['lost']} · P&L: {'+'if telegram_pl['profit']>=0 else ''}£{telegram_pl['profit']:.2f}"

                            # Edited FREE message — original tip + WIN result appended
                            free_result = (
                                details.get("original_msg", "").rstrip() + "\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"💥 <b>RESULT: WINNER!</b> {hs}-{as_} FT\n"
                                f"🏆 +£{profit:.2f} on £{stake:.0f} · {pl_line}\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"🔒 Full xG analysis → accagenius.com"
                            )
                            # Edited PRO message — original tip + WIN result
                            pro_result = (
                                details.get("pro_msg", "").rstrip() + "\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"💥 <b>RESULT: WINNER! FT {hs}-{as_}</b>\n"
                                f"🏆 +£{profit:.2f} profit · {pl_line}"
                            )
                        else:
                            telegram_pl["lost"] += 1
                            telegram_pl["profit"] = round(telegram_pl["profit"] - stake, 2)
                            pl_line = f"Won:{telegram_pl['won']} Lost:{telegram_pl['lost']} · P&L: {'+'if telegram_pl['profit']>=0 else ''}£{telegram_pl['profit']:.2f}"

                            free_result = (
                                details.get("original_msg", "").rstrip() + "\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"❌ <b>RESULT: No luck FT {hs}-{as_}</b>\n"
                                f"📊 {pl_line}\n"
                                f"More tips coming soon 👉 accagenius.com"
                            )
                            pro_result = (
                                details.get("pro_msg", "").rstrip() + "\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"❌ <b>RESULT: FT {hs}-{as_} — No luck</b>\n"
                                f"📊 {pl_line}"
                            )

                        # Edit the original messages in-place
                        free_mid = details.get("free_mid", 0)
                        pro_mid  = details.get("pro_mid", 0)
                        if has_free and free_mid:
                            edit_telegram_message(TELEGRAM_CHANNEL_FREE, free_mid, free_result)
                        elif has_free:
                            # Message expired or wasn't stored — send new one
                            send_telegram_to(free_result, TELEGRAM_CHANNEL_FREE)
                        if has_pro and pro_mid:
                            edit_telegram_message(pro_channel, pro_mid, pro_result)
                        elif has_pro:
                            send_telegram_to(pro_result, pro_channel)

                        alerted_details[fid]["result_posted"] = True
                        logger.info(f"Result edited: {home} vs {away} FT {hs}-{as_} — {'WON' if won else 'LOST'}")
                except Exception as e:
                    logger.error(f"Result check error {fid}: {e}")

            # Sort by strongest xG potential — best match gets free alert
            alerts_found = []
            for m in live_matches:
                if m["id"] in alerted_fixtures:
                    continue
                # Pre-filter by minute BEFORE fetching stats — saves API calls
                minute = m.get("minute") or 0
                if not (ALERT_MINUTE_MIN <= minute <= ALERT_MINUTE_MAX):
                    continue
                try:
                    alert = score_live_match(
                        m["id"], m["home"], m["away"],
                        m["home_score"], m["away_score"],
                        m["minute"], m["league"]
                    )
                    if alert:
                        alerts_found.append(alert)
                except Exception as e:
                    logger.error(f"Scoring error {m['id']}: {e}")

            # Sort by win % descending — strongest alert first
            alerts_found.sort(key=lambda a: a['fav_win_pct'], reverse=True)

            sent_count = 0

            for i, alert in enumerate(alerts_found):
                fid = alert['fixture_id']

                # ── PRO channel gets every alert ──
                if has_pro:
                    pro_msg = build_telegram_message(alert)
                    pro_mid = send_telegram_to(pro_msg, pro_channel)
                    if pro_mid:
                        sent_count += 1
                        logger.info(f"PRO alert: {alert['home']} vs {alert['away']} {alert['minute']}'")

                # ── FREE channel gets top 3 alerts per day ──
                free_mid = 0
                if has_free and free_alerts_sent_today < 3:
                    free_msg = build_free_message(alert)
                    free_mid = send_telegram_to(free_msg, TELEGRAM_CHANNEL_FREE)
                    if free_mid:
                        free_alerts_sent_today += 1
                        logger.info(f"FREE alert sent ({free_alerts_sent_today}/3): {alert['home']} vs {alert['away']}")

                # Store details + message IDs for result editing later
                alerted_fixtures.add(fid)
                alerted_details[fid] = {
                    "home": alert["home"], "away": alert["away"],
                    "bet": alert["bet"], "odds": alert.get("fav_odds", 2.0),
                    "original_msg": build_free_message(alert),   # base text to edit
                    "pro_msg": build_telegram_message(alert),
                    "free_mid": free_mid,    # message_id in free channel
                    "pro_mid": pro_mid if has_pro else 0,
                    "result_posted": False
                }
                await asyncio.sleep(2)

            logger.info(f"Scanner done — {sent_count} pro alerts, {free_alerts_sent_today} free today")

        except Exception as e:
            logger.error(f"Scanner error: {e}")

        # Active-hours gating: UK football runs 11am-11pm. Sleep longer overnight to save API quota.
        hour = datetime.now().hour
        if 11 <= hour <= 23:
            await asyncio.sleep(300)   # 5 min during active hours
        else:
            await asyncio.sleep(900)   # 15 min overnight (midnight-11am)


def normalise_channel(channel: str) -> str:
    """Normalise Telegram channel ID — adds -100 prefix if missing from numeric IDs."""
    c = str(channel).strip()
    if c.startswith("@"):
        return c  # username format e.g. @AccaGeniusPro
    # Numeric ID — must be negative for supergroups/channels
    digits = c.lstrip("-")
    if digits.isdigit():
        num = int(digits)
        # Channel IDs are always >1000000000 and must be negative
        if num > 0:
            return f"-{num}"  # e.g. 1003799180225 → -1003799180225
        return c  # already negative
    return c


def send_telegram_to(message: str, channel: str) -> int:
    """Send to a specific channel. Returns message_id on success, 0 on failure."""
    if not TELEGRAM_BOT_TOKEN or not channel:
        return 0
    channel = normalise_channel(channel)
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={
            "chat_id": channel,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }, timeout=10)
        if r.status_code == 200:
            return r.json().get("result", {}).get("message_id", 0)
        logger.error(f"Telegram send failed {r.status_code}: {r.text[:200]}")
        return 0
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
        return 0


def edit_telegram_message(channel: str, message_id: int, new_text: str) -> bool:
    """Edit an existing message in-place — used for result updates on tip messages."""
    if not TELEGRAM_BOT_TOKEN or not channel or not message_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
        r = requests.post(url, json={
            "chat_id": channel,
            "message_id": message_id,
            "text": new_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }, timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Telegram edit error: {e}")
        return False


def send_telegram(message: str) -> int:
    """Legacy — sends to pro channel or fallback. Returns message_id."""
    channel = TELEGRAM_CHANNEL_PRO or TELEGRAM_CHANNEL_ID
    return send_telegram_to(message, channel)




@app.on_event("startup")
async def startup_event():
    """Start background scanner on server startup."""
    asyncio.create_task(live_alert_scanner())
    logger.info("AccaGenius API started — Telegram scanner running")



# =========================
# MODELS
# =========================
class AccaRequest(BaseModel):
    selections: int = 5
    market: str = "winner"
    risk: str = "balanced"
    leagues: List[str] = []
    today_only: bool = False

class HTAccaRequest(BaseModel):
    selections: int = 5
    leagues: List[str] = []

class SaveAccaRequest(BaseModel):
    name: str
    selections: List[dict]
    total_odds: float
    stake: float = 10.0

# =========================
# CORE HELPERS
# =========================

def get_team_form(team_id: int, league_id: int, last: int = 10) -> dict:
    """Get detailed team form with extended stats — cached 6 hours"""
    cache_key = f"form_{team_id}_{league_id}_{last}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    # Hard quota guard — skip form fetch if running critically high
    if _api_call_count > 6500:
        logger.warning(f"⚠️ Skipping form fetch (quota guard) — {_api_call_count}/7500 today")
        return {"games": 0, "wins": 0, "draws": 0, "losses": 0, "form": ""}

    data = api_get("fixtures", {
        "team": team_id, "league": league_id,
        "season": get_season(), "last": last, "timezone": "Europe/London"
    })
    fixtures = data.get("response", [])
    wins = draws = losses = gf = ga = ht_leads = early_goals = scored_first = 0
    recent = []

    for f in fixtures:
        if f["fixture"]["status"]["short"] != "FT":
            continue
        is_home = f["teams"]["home"]["id"] == team_id
        hg = f["goals"]["home"] or 0
        ag = f["goals"]["away"] or 0
        tf = hg if is_home else ag
        ta = ag if is_home else hg
        gf += tf; ga += ta
        if tf > ta: wins += 1
        elif tf == ta: draws += 1
        else: losses += 1

        # HT stats
        ht = f.get("score", {}).get("halftime", {})
        ht_h = ht.get("home") or 0
        ht_a = ht.get("away") or 0
        if is_home and ht_h > ht_a: ht_leads += 1
        elif not is_home and ht_a > ht_h: ht_leads += 1

        opp = f["teams"]["away"]["name"] if is_home else f["teams"]["home"]["name"]
        opp_logo = f["teams"]["away"]["logo"] if is_home else f["teams"]["home"]["logo"]
        result = "W" if tf > ta else ("D" if tf == ta else "L")
        recent.append({
            "date": f["fixture"]["date"].split("T")[0],
            "opponent": opp, "opponent_logo": opp_logo,
            "venue": "H" if is_home else "A",
            "goals_for": tf, "goals_against": ta, "result": result
        })

    games = wins + draws + losses
    form_rating = (wins * 3 + draws) / max(games, 1) if games else 1.5
    gf_avg = round(gf / max(games, 1), 2)
    ga_avg = round(ga / max(games, 1), 2)
    btts_pct = round(sum(1 for r in recent if r["goals_for"] > 0 and r["goals_against"] > 0) / max(len(recent), 1) * 100)
    over25_pct = round(sum(1 for r in recent if r["goals_for"] + r["goals_against"] > 2) / max(len(recent), 1) * 100)
    cs_pct = round(sum(1 for r in recent if r["goals_against"] == 0) / max(len(recent), 1) * 100)
    ht_lead_pct = round(ht_leads / max(games, 1) * 100)

    result = {
        "games": games, "wins": wins, "draws": draws, "losses": losses,
        "gf": gf, "ga": ga, "gf_avg": gf_avg, "ga_avg": ga_avg,
        "form_rating": round(form_rating, 2),
        "btts_pct": btts_pct, "over25_pct": over25_pct,
        "cs_pct": cs_pct, "ht_lead_pct": ht_lead_pct,
        "recent_fixtures": recent
    }
    cache_set(cache_key, result, 21600)  # 6 hours — form doesn't change during a day
    return result


def get_real_odds(fixture_id: int) -> dict:
    """Fetch best odds from API across all available bookmakers — cached 15 mins"""
    cache_key = f"odds_{fixture_id}"
    cached = cache_get(cache_key)
    if cached:
        return cached
    data = api_get("odds", {"fixture": fixture_id})
    response_data = data.get("response", [])
    fallback = {"home": 0, "draw": 0, "away": 0, "home_bk": "", "draw_bk": "", "away_bk": "", "available": False}
    if not response_data:
        return fallback
    best = {"home": 0.0, "draw": 0.0, "away": 0.0, "home_bk": "", "draw_bk": "", "away_bk": ""}
    all_bookmakers = {}
    for bm in response_data[0].get("bookmakers", [])[:12]:
        bm_name = bm["name"]
        for bet in bm.get("bets", []):
            if bet["name"] == "Match Winner":
                vals = {v["value"]: v["odd"] for v in bet.get("values", [])}
                try:
                    h = float(vals.get("Home", 0))
                    d = float(vals.get("Draw", 0))
                    a = float(vals.get("Away", 0))
                    all_bookmakers[bm_name] = {"home": h, "draw": d, "away": a}
                    if h > best["home"]: best["home"] = h; best["home_bk"] = bm_name
                    if d > best["draw"]: best["draw"] = d; best["draw_bk"] = bm_name
                    if a > best["away"]: best["away"] = a; best["away_bk"] = bm_name
                except Exception:
                    pass
    if best["home"] == 0:
        return fallback
    result = {**best, "available": True, "all_bookmakers": all_bookmakers}
    cache_set(cache_key, result, 900)  # 15 minutes — odds shift slowly
    return result


def get_quick_odds(fixture_id: int) -> dict:
    """Fast odds fetch — returns best home/draw/away with bookmaker name"""
    return get_real_odds(fixture_id)


def analyze_and_pick(fixture: dict, home_form: dict, away_form: dict, risk: str, market: str = "winner") -> Optional[dict]:
    """Pick engine — Match Winner market with proper risk-based selection.
    Works with OR without real odds. Avoids draws unless they're very clear value."""
    try:
        home = fixture["teams"]["home"]["name"]
        away = fixture["teams"]["away"]["name"]
        home_logo = fixture["teams"]["home"]["logo"]
        away_logo = fixture["teams"]["away"]["logo"]
        fid = fixture["fixture"]["id"]
        home_id = fixture["teams"]["home"]["id"]
        away_id = fixture["teams"]["away"]["id"]
        league_id = fixture["league"]["id"]
        dt = datetime.fromisoformat(fixture["fixture"]["date"].replace("Z", "+00:00"))
        date_str = dt.strftime("%d/%m %H:%M")

        odds = get_real_odds(fid)
        ho = odds.get("home", 0)
        do = odds.get("draw", 0)
        ao = odds.get("away", 0)
        has_odds = odds.get("available", False)

        hr = home_form.get("form_rating", 1.5) + 0.4  # home advantage boost
        ar = away_form.get("form_rating", 1.5)
        hw = home_form.get("wins", 0)
        aw_wins = away_form.get("wins", 0)
        hg = home_form.get("gf_avg", 1.2)
        ag = away_form.get("gf_avg", 1.0)
        hga = home_form.get("ga_avg", 1.2)
        aga = away_form.get("ga_avg", 1.0)
        h_games = max(home_form.get("games", 1), 1)
        a_games = max(away_form.get("games", 1), 1)
        h_wr = hw / h_games
        a_wr = aw_wins / a_games

        base = {
            "id": fid, "home": home, "away": away,
            "home_logo": home_logo, "away_logo": away_logo,
            "date": date_str, "home_id": home_id, "away_id": away_id,
            "league_id": league_id, "market_type": "1X2",
            "league": fixture["league"]["name"],
            "odds_home": ho, "odds_draw": do, "odds_away": ao,
            "odds_available": has_odds
        }

        gap = hr - ar  # positive = home stronger

        # ─── SAFE risk ───
        if risk == "safe":
            if gap > 0.5:
                pick_odds = ho if has_odds and ho > 1.10 else round(1.45 + max(0, 1.8 - gap) * 0.2, 2)
                conf = min(88, 62 + int(gap * 10) + int(h_wr * 10))
                return {**base, "bet": f"{home} Win", "odds": round(pick_odds, 2), "confidence": conf,
                        "reasoning": f"{home} {hw}W-{home_form.get('draws',0)}D-{home_form.get('losses',0)}L · avg {hg:.1f} goals/game"}
            if gap < -0.5:
                pick_odds = ao if has_odds and ao > 1.10 else round(1.60 + max(0, 1.8 - abs(gap)) * 0.2, 2)
                conf = min(85, 60 + int(abs(gap) * 10) + int(a_wr * 10))
                return {**base, "bet": f"{away} Win", "odds": round(pick_odds, 2), "confidence": conf,
                        "reasoning": f"{away} dominant · {aw_wins}W in last {a_games} · avg {ag:.1f} goals/game"}
            return None

        # ─── RISKY risk ───
        elif risk == "risky":
            if gap < 0.3 and ar >= 1.4 and (not has_odds or ao >= 2.2):
                pick_odds = ao if has_odds and ao > 1.5 else round(2.4 + max(0, ar - 1.4) * 0.5, 2)
                conf = min(72, 50 + int(ar * 10))
                return {**base, "bet": f"{away} Win", "odds": round(pick_odds, 2), "confidence": conf,
                        "reasoning": f"Value upset — {away} {aw_wins}W in last {a_games}, form tighter than odds suggest"}
            if gap > 0.2 and (not has_odds or ho >= 2.5) and hr > ar:
                pick_odds = ho if has_odds and ho > 2.0 else round(2.8 + max(0, 1.0 - gap) * 0.4, 2)
                conf = min(70, 50 + int(gap * 8))
                return {**base, "bet": f"{home} Win", "odds": round(pick_odds, 2), "confidence": conf,
                        "reasoning": f"{home} home form underpriced · {hw}W last {h_games}"}
            # Draw only in risky mode and only when genuinely close
            if abs(gap) < 0.3 and has_odds and do >= 3.2 and a_wr >= 0.25 and h_wr >= 0.25:
                conf = min(65, 48 + int((3.5 - abs(gap)) * 5))
                return {**base, "bet": "Draw", "odds": round(do, 2), "confidence": conf,
                        "reasoning": f"Closely matched — form gap {abs(gap):.1f}, draw @ {do:.2f} value"}
            if ao >= 3.0 and aw_wins >= 3:
                pick_odds = ao if has_odds else 3.5
                conf = min(65, 44 + aw_wins * 4)
                return {**base, "bet": f"{away} Win", "odds": round(pick_odds, 2), "confidence": conf,
                        "reasoning": f"{away} {aw_wins}W in last {a_games} — priced generously"}
            return None

        # ─── BALANCED (default) ───
        else:
            # Home win — form edge
            if gap > 0.3:
                pick_odds = ho if has_odds and ho > 1.20 else round(1.65 + max(0, 1.5 - gap) * 0.3, 2)
                conf = min(84, 55 + int(gap * 14) + int(h_wr * 8))
                return {**base, "bet": f"{home} Win", "odds": round(pick_odds, 2), "confidence": conf,
                        "reasoning": f"{home} {hw}W-{home_form.get('draws',0)}D-{home_form.get('losses',0)}L last {h_games} · {hg:.1f} scored avg"}
            # Away win — clear away edge
            if gap < -0.5:
                pick_odds = ao if has_odds and ao > 1.20 else round(1.80 + max(0, 1.5 - abs(gap)) * 0.3, 2)
                conf = min(82, 58 + int(abs(gap) * 12) + int(a_wr * 8))
                return {**base, "bet": f"{away} Win", "odds": round(pick_odds, 2), "confidence": conf,
                        "reasoning": f"{away} stronger · {aw_wins}W-{away_form.get('draws',0)}D-{away_form.get('losses',0)}L · {ag:.1f} goals/game"}
            # Slight home edge — still pick home
            if gap > 0.2 and h_wr >= 0.40:
                pick_odds = ho if has_odds and ho > 1.30 else round(1.85 + max(0, 1.0 - gap) * 0.3, 2)
                conf = min(72, 55 + int(gap * 10) + int(h_wr * 8))
                return {**base, "bet": f"{home} Win", "odds": round(pick_odds, 2), "confidence": conf,
                        "reasoning": f"{home} home edge · {hw}W last {h_games} · {hg:.1f} goals/game"}
            # Slight away edge
            if gap < -0.2 and a_wr >= 0.40:
                pick_odds = ao if has_odds and ao > 1.30 else round(2.00 + max(0, 1.0 - abs(gap)) * 0.3, 2)
                conf = min(70, 53 + int(abs(gap) * 10) + int(a_wr * 8))
                return {**base, "bet": f"{away} Win", "odds": round(pick_odds, 2), "confidence": conf,
                        "reasoning": f"{away} away edge · {aw_wins}W last {a_games} · {ag:.1f} goals avg"}
            # Only pick draw if genuinely very evenly matched AND odds available AND above 3.0
            if abs(gap) < 0.15 and has_odds and do >= 3.0 and h_wr >= 0.3 and a_wr >= 0.3:
                conf = min(68, 52 + int((3.2 - abs(gap)) * 6))
                return {**base, "bet": "Draw", "odds": round(do, 2), "confidence": conf,
                        "reasoning": f"Very evenly matched · form gap {abs(gap):.2f} · draw @ {do:.2f}"}
            return None

    except Exception as e:
        logger.error(f"Pick error: {e}")
        return None


def ht_pick(fixture: dict, home_form: dict, away_form: dict) -> Optional[dict]:
    """HT-specific pick using first-half patterns"""
    try:
        home = fixture["teams"]["home"]["name"]
        away = fixture["teams"]["away"]["name"]
        fid = fixture["fixture"]["id"]
        home_id = fixture["teams"]["home"]["id"]
        away_id = fixture["teams"]["away"]["id"]
        dt = datetime.fromisoformat(fixture["fixture"]["date"].replace("Z", "+00:00"))

        h_ht = home_form.get("ht_lead_pct", 40)
        a_ht = away_form.get("ht_lead_pct", 35)
        hg = home_form.get("gf_avg", 1.2)
        ag = away_form.get("gf_avg", 1.0)
        total = hg + ag

        base = {
            "id": fid, "home": home, "away": away,
            "home_logo": fixture["teams"]["home"]["logo"],
            "away_logo": fixture["teams"]["away"]["logo"],
            "date": dt.strftime("%d/%m %H:%M"),
            "home_id": home_id, "away_id": away_id
        }

        # HT Result
        if h_ht > 55 and h_ht > a_ht + 15:
            return {**base, "bet": f"{home} HT Lead", "odds": round(1.85 + (h_ht - 55) * 0.02, 2),
                    "confidence": min(78, h_ht), "market_type": "HT Result",
                    "reasoning": f"{home} lead at HT in {h_ht}% of games — strong opening pattern"}
        elif a_ht > 55:
            return {**base, "bet": f"{away} HT Lead", "odds": round(2.20 + (a_ht - 55) * 0.02, 2),
                    "confidence": min(72, a_ht), "market_type": "HT Result",
                    "reasoning": f"{away} lead at HT in {a_ht}% of games"}

        # HT Over 0.5
        if total > 2.0:
            conf = min(78, 55 + int(total * 8))
            return {**base, "bet": "HT Over 0.5 Goals", "odds": round(1.40 + total * 0.04, 2),
                    "confidence": conf, "market_type": "HT O/U",
                    "reasoning": f"Combined {round(total,1)} g/game — goal before HT very likely"}
        return None
    except Exception:
        return None


# =========================
# ROUTES
# =========================

# ────────────────────────────────────────────────────────────────
# ACCESS CODE ENDPOINTS
# ────────────────────────────────────────────────────────────────

@app.post("/redeem-code")
async def redeem_code(request: dict):
    """Validate a Pro access code. Single-use. Sends welcome email + returns Telegram invite."""
    code  = (request.get("code") or "").strip().upper()
    email = (request.get("email") or "").strip().lower()
    name  = (request.get("name") or "there").strip()

    if not code:
        return {"ok": False, "error": "Please enter your access code"}
    if code not in _all_codes:
        return {"ok": False, "error": "Invalid access code — check for typos or contact support"}
    if code in _used_codes:
        return {"ok": False, "error": "This code has already been used. Contact support if this is a mistake"}

    _used_codes[code] = {"email": email, "name": name, "used_at": datetime.now().isoformat()}
    logger.info(f"✅ Code redeemed: {code} by {email}")

    email_sent = email_welcome_pro(email, name, code, TELEGRAM_PRO_INVITE_LINK) if email else False

    return {
        "ok": True,
        "plan": "pro",
        "telegram_invite": TELEGRAM_PRO_INVITE_LINK or None,
        "email_sent": email_sent,
        "message": f"Welcome to AccaGenius Pro, {name}! 🎉"
    }


@app.get("/admin/codes")
async def admin_codes(token: str = "", pwd: str = ""):
    # Accept either the hashed token OR the raw password directly
    import hashlib
    if not ADMIN_PASSWORD_ENV:
        return {"error": "ADMIN_PASSWORD not set in Railway"}
    check = token or pwd
    if not check:
        return {"error": "Unauthorised"}
    expected_token = hashlib.sha256((ADMIN_PASSWORD_ENV + datetime.now().strftime("%Y-%m-%d")).encode()).hexdigest()[:32]
    if check != expected_token and check != ADMIN_PASSWORD_ENV:
        return {"error": "Unauthorised"}
    available = sorted([c for c in _all_codes if c not in _used_codes])
    return {
        "total": len(_all_codes), "used": len(_used_codes), "available": len(available),
        "available_codes": available, "used_details": dict(_used_codes)
    }


@app.post("/admin/generate-codes")
async def admin_generate_codes(request: dict):
    import hashlib, secrets as _sec
    token = request.get("token", "") or request.get("pwd", "")
    count = min(int(request.get("count", 10)), 50)
    if not ADMIN_PASSWORD_ENV or not token:
        return {"error": "Unauthorised"}
    expected = hashlib.sha256((ADMIN_PASSWORD_ENV + datetime.now().strftime("%Y-%m-%d")).encode()).hexdigest()[:32]
    if token != expected:
        return {"error": "Unauthorised"}
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    new_codes = [f"ACCA-{''.join(_sec.choice(chars) for _ in range(4))}" for _ in range(count)]
    combined  = sorted(set(list(_all_codes) + new_codes))
    return {"new_codes": new_codes, "railway_value": ",".join(combined),
            "instructions": "Paste railway_value into PRO_ACCESS_CODES Railway Variable then redeploy"}


@app.post("/admin/send-code-email")
async def admin_send_code_email(request: dict):
    """Manually email a Pro code to a customer (use after manual PayPal sale)."""
    import hashlib
    token = request.get("token", "")
    if not ADMIN_PASSWORD_ENV or not token:
        return {"error": "Unauthorised"}
    expected = hashlib.sha256((ADMIN_PASSWORD_ENV + datetime.now().strftime("%Y-%m-%d")).encode()).hexdigest()[:32]
    if token != expected:
        return {"error": "Unauthorised"}
    to    = (request.get("email") or "").strip()
    name  = (request.get("name") or "there").strip()
    code  = (request.get("code") or "").strip().upper()
    if not to or not code:
        return {"error": "email and code are required"}
    sent = email_welcome_pro(to, name, code, TELEGRAM_PRO_INVITE_LINK)
    return {"ok": sent, "to": to, "code": code}


# ────────────────────────────────────────────────────────────────
# PASSWORD RESET ENDPOINTS
# ────────────────────────────────────────────────────────────────

@app.post("/forgot-password")
async def forgot_password(request: dict):
    import secrets as _sec
    email = (request.get("email") or "").strip().lower()
    name  = (request.get("name") or "there").strip()
    if not email:
        return {"ok": False, "error": "Please enter your email address"}
    token   = _sec.token_urlsafe(32)
    expires = datetime.now() + timedelta(hours=1)
    _reset_tokens[token] = {"email": email, "name": name, "expires": expires, "used": False}
    email_password_reset(email, name, token)
    # Always return ok — never reveal if email exists (security)
    return {"ok": True, "message": "If an account exists for that email, a reset link is on its way"}


@app.get("/reset-password")
async def check_reset_token(token: str = ""):
    if not token or token not in _reset_tokens:
        return {"valid": False, "error": "Invalid or expired reset link"}
    entry = _reset_tokens[token]
    if entry["used"]:
        return {"valid": False, "error": "This link has already been used"}
    if datetime.now() > entry["expires"]:
        del _reset_tokens[token]
        return {"valid": False, "error": "Link expired — please request a new one"}
    return {"valid": True, "email": entry["email"]}


@app.post("/reset-password")
async def do_reset_password(request: dict):
    token    = (request.get("token") or "").strip()
    new_pass = (request.get("new_password") or "").strip()
    if not token or token not in _reset_tokens:
        return {"ok": False, "error": "Invalid or expired link"}
    entry = _reset_tokens[token]
    if entry["used"]:
        return {"ok": False, "error": "Already used — request a new reset link"}
    if datetime.now() > entry["expires"]:
        return {"ok": False, "error": "Link expired — please request a new one"}
    if len(new_pass) < 8:
        return {"ok": False, "error": "Password must be at least 8 characters"}
    _reset_tokens[token]["used"] = True
    logger.info(f"Password reset completed for {entry['email']}")
    return {"ok": True, "email": entry["email"]}


# ────────────────────────────────────────────────────────────────
# ADMIN ENDPOINTS
# ────────────────────────────────────────────────────────────────

@app.get("/admin/verify-get")
async def admin_verify_get(password: str = ""):
    import hashlib
    if not ADMIN_PASSWORD_ENV:
        return {"ok": False, "error": "ADMIN_PASSWORD not set in Railway Variables"}
    if password == ADMIN_PASSWORD_ENV:
        token = hashlib.sha256((ADMIN_PASSWORD_ENV + datetime.now().strftime("%Y-%m-%d")).encode()).hexdigest()[:32]
        return {"ok": True, "token": token}
    return {"ok": False, "error": "Invalid password"}


@app.post("/admin/verify")
async def admin_verify(request: dict):
    import hashlib
    pwd = request.get("password", "")
    if not ADMIN_PASSWORD_ENV:
        return {"ok": False, "error": "ADMIN_PASSWORD not set in Railway Variables"}
    if pwd == ADMIN_PASSWORD_ENV:
        token = hashlib.sha256((ADMIN_PASSWORD_ENV + datetime.now().strftime("%Y-%m-%d")).encode()).hexdigest()[:32]
        return {"ok": True, "token": token, "role": "admin"}
    return {"ok": False, "error": "Incorrect password"}


@app.get("/admin/check-config")
async def check_config():
    """Quick check that Railway env vars are set correctly (passwords not exposed)."""
    return {
        "ADMIN_PASSWORD_set":       bool(ADMIN_PASSWORD_ENV),
        "PRO_ACCESS_CODES_set":     bool(PRO_ACCESS_CODES_RAW),
        "codes_loaded":             len(_all_codes),
        "codes_used":               len(_used_codes),
        "TELEGRAM_BOT_set":         bool(TELEGRAM_BOT_TOKEN),
        "TELEGRAM_PRO_set":         bool(TELEGRAM_PRO_INVITE_LINK),
        "RESEND_API_KEY_set":       bool(RESEND_API_KEY),
        "EMAIL_FROM":               EMAIL_FROM,
        "SITE_URL":                 SITE_URL,
    }


@app.get("/")
@app.head("/")
async def root():
    return {"status": "AccaGenius Ultimate API — Live", "version": "5.0"}

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

@app.get("/telegram/status")
async def telegram_status():
    pro_raw  = TELEGRAM_CHANNEL_PRO or TELEGRAM_CHANNEL_ID or ""
    free_raw = TELEGRAM_CHANNEL_FREE or ""
    return {
        "bot_configured": bool(TELEGRAM_BOT_TOKEN),
        "pro_channel":  normalise_channel(pro_raw)  if pro_raw  else "not set",
        "free_channel": normalise_channel(free_raw) if free_raw else "not set",
        "pro_channel_raw":  pro_raw  or "not set",
        "free_channel_raw": free_raw or "not set",
        "free_alerts_today": free_alerts_sent_today,
        "alerted_fixtures_today": len(alerted_fixtures),
        "thresholds": {
            "win_pct": ALERT_WIN_PCT,
            "xg_gap": ALERT_XG_GAP,
            "minute_min": ALERT_MINUTE_MIN,
            "minute_max": ALERT_MINUTE_MAX,
        }
    }


@app.get("/telegram/find-channels")
async def find_channels():
    """
    Automatically finds channel IDs for any channel your bot is an admin of.
    HOW TO USE:
      1. Make sure your bot is added as admin to your channels
      2. Send ANY message to each channel (e.g. type 'test')
      3. Visit this URL in your browser
      4. Copy the IDs shown and paste into Railway Variables
    """
    if not TELEGRAM_BOT_TOKEN:
        return {"error": "TELEGRAM_BOT_TOKEN not set in Railway Variables"}

    try:
        # getUpdates fetches recent messages/events the bot has seen
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        r = requests.get(url, timeout=10)
        data = r.json()

        if not data.get("ok"):
            return {
                "error": "Bot token invalid or bot has no recent activity",
                "detail": data.get("description", ""),
                "fix": "Make sure you added the bot as admin to your channels and sent a message in each channel"
            }

        # Extract all unique chats the bot has seen
        chats = {}
        for update in data.get("result", []):
            # Check all possible message locations
            for key in ["message", "channel_post", "edited_channel_post", "my_chat_member"]:
                item = update.get(key, {})
                if not item:
                    continue
                chat = item.get("chat", {})
                if not chat:
                    continue
                chat_id = chat.get("id")
                chat_title = chat.get("title") or chat.get("username") or chat.get("first_name") or "Unknown"
                chat_type = chat.get("type", "unknown")
                if chat_id and chat_type in ("channel", "supergroup", "group"):
                    chats[chat_id] = {
                        "id": chat_id,
                        "title": chat_title,
                        "type": chat_type
                    }

        if not chats:
            return {
                "found": 0,
                "channels": [],
                "instructions": [
                    "No channels found yet. Do these steps then visit this URL again:",
                    "1. Go to your Free channel in Telegram",
                    "2. Type and send any message (e.g. 'test')",
                    "3. Go to your Pro channel",
                    "4. Type and send any message",
                    "5. Refresh this page"
                ]
            }

        channel_list = list(chats.values())

        return {
            "found": len(channel_list),
            "channels": channel_list,
            "next_steps": [
                "Copy the 'id' value for each channel",
                "Go to Railway → your project → Variables",
                "Add: FREE_CHANNEL_ID = (id of your free channel)",
                "Add: PRO_CHANNEL_ID = (id of your pro channel)",
                "Then click Deploy or Redeploy"
            ]
        }

    except Exception as e:
        return {"error": str(e)}

@app.post("/telegram/test")
async def telegram_test():
    results = {}
    pro_channel = TELEGRAM_CHANNEL_PRO or TELEGRAM_CHANNEL_ID
    test_pro = (
        "🟢 <b>ACCAGENIUS PRO — Test Alert</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "✅ Pro channel working!\n"
        "Full xG stats, win %, odds — all live here.\n"
        "🤖 AccaGenius Pro"
    )
    test_free = (
        "🟡 <b>ACCAGENIUS FREE — Test Alert</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "✅ Free channel working!\n"
        "💰 <b>In-Play Tip: Example Win</b>\n"
        "🔒 Full analysis on AccaGenius Pro\n"
        "👉 <b>Upgrade: accagenius.com</b>"
    )
    if pro_channel:
        results["pro"] = send_telegram_to(test_pro, pro_channel)
    if TELEGRAM_CHANNEL_FREE:
        results["free"] = send_telegram_to(test_free, TELEGRAM_CHANNEL_FREE)
    if not results:
        return {"error": "No channels configured. Set TELEGRAM_CHANNEL_PRO and TELEGRAM_CHANNEL_FREE in Railway Variables."}
    return {"results": results}
@app.post("/telegram/send-todays-acca")
async def send_todays_acca_telegram():
    """Send today's acca picks to both Telegram channels."""
    pro_channel = TELEGRAM_CHANNEL_PRO or TELEGRAM_CHANNEL_ID
    if not TELEGRAM_BOT_TOKEN:
        return {"error": "TELEGRAM_BOT_TOKEN not set"}
    if not pro_channel and not TELEGRAM_CHANNEL_FREE:
        return {"error": "No channels configured"}

    today_str = datetime.now(_LONDON).strftime("%Y-%m-%d") if _LONDON else datetime.utcnow().strftime("%Y-%m-%d")
    cached = cache_get(f"today_acca_{today_str}")
    if not cached:
        return {"error": "No acca generated yet — hit /today-acca first"}

    results = {}
    for acca in cached.get("accas", []):
        picks = acca.get("picks", [])
        if not picks:
            continue
        label = acca.get("label", "Acca")
        emoji = acca.get("emoji", "⚽")
        total_odds = round(
            __import__("functools").reduce(lambda a, p: a * p.get("odds", 1.75), picks, 1.0), 2
        )
        sep = "━" * 20

        def fmt_form(p, team):
            """Format form string e.g. WWDLW for a team from pick data."""
            key = "home_form" if team == "home" else "away_form"
            form = p.get(key, "")
            if not form:
                return ""
            coloured = ""
            for ch in str(form)[-5:]:
                if ch == "W": coloured += "🟢"
                elif ch == "L": coloured += "🔴"
                elif ch == "D": coloured += "🟡"
                else: coloured += "⚪"
            return coloured

        # Pro legs — full detail with form
        legs_pro = ""
        for p in picks:
            hform = fmt_form(p, "home")
            aform = fmt_form(p, "away")
            kick  = p.get("time", "")
            legs_pro += "⚽ <b>" + p.get("home","") + "</b> vs <b>" + p.get("away","") + "</b>"
            if kick: legs_pro += "  🕐 " + kick
            legs_pro += "\n"
            if hform: legs_pro += "   " + p.get("home","").split()[0] + " form: " + hform + "\n"
            if aform: legs_pro += "   " + p.get("away","").split()[0] + " form: " + aform + "\n"
            legs_pro += "   ✅ <b>" + str(p.get("bet","")) + "</b> @ <b>" + str(p.get("odds","")) + "</b>\n\n"

        # Free legs — teams, bet type and form shown; odds locked
        legs_free = ""
        for p in picks:
            hform = fmt_form(p, "home")
            aform = fmt_form(p, "away")
            kick  = p.get("time", "")
            legs_free += "⚽ <b>" + p.get("home","") + "</b> vs <b>" + p.get("away","") + "</b>"
            if kick: legs_free += "  🕐 " + kick
            legs_free += "\n"
            if hform: legs_free += "   " + p.get("home","").split()[0] + " form: " + hform + "\n"
            if aform: legs_free += "   " + p.get("away","").split()[0] + " form: " + aform + "\n"
            legs_free += "   ✅ <b>" + str(p.get("bet","")) + "</b> · 🔒 Odds on Pro\n\n"

        pro_msg = (
            emoji + " <b>ACCA OF THE DAY — " + label.upper() + "</b>\n"
            + sep + "\n"
            + legs_pro
            + sep + "\n"
            + "💰 Combined odds: <b>" + str(total_odds) + "x</b>\n"
            + "📅 " + today_str + "\n"
            + "🤖 AccaGenius Pro · accagenius.com"
        )

        free_msg = (
            emoji + " <b>ACCA OF THE DAY — " + label.upper() + "</b>\n"
            + sep + "\n"
            + legs_free
            + sep + "\n"
            + "📅 " + today_str + "  ·  " + str(len(picks)) + " picks\n"
            + "🔒 Full odds &amp; xG analysis on Pro\n"
            + "👉 <b>accagenius.com</b> · 7-day free trial"
        )

        if pro_channel:
            mid = send_telegram_to(pro_msg, pro_channel)
            results["pro_" + acca["theme"]] = mid
        if TELEGRAM_CHANNEL_FREE:
            mid = send_telegram_to(free_msg, TELEGRAM_CHANNEL_FREE)
            results["free_" + acca["theme"]] = mid

    return {"sent": results, "accas": len(cached.get("accas", []))}



@app.post("/telegram/scan-now")
async def telegram_scan_now():
    """Manually trigger one scan cycle immediately."""
    any_channel = TELEGRAM_CHANNEL_PRO or TELEGRAM_CHANNEL_FREE or TELEGRAM_CHANNEL_ID
    if not TELEGRAM_BOT_TOKEN or not any_channel:
        return {"error": "Telegram not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_PRO in Railway Variables."}

    live_data = api_get("fixtures", {"live": "all", "timezone": "Europe/London"})
    our_league_ids = {l["id"] for l in LEAGUES}
    live_matches = []
    for f in live_data.get("response", []):
        try:
            if f["league"]["id"] not in our_league_ids:
                continue
            live_matches.append({
                "id": f["fixture"]["id"],
                "home": f["teams"]["home"]["name"],
                "away": f["teams"]["away"]["name"],
                "home_score": f["goals"]["home"] or 0,
                "away_score": f["goals"]["away"] or 0,
                "minute": f["fixture"]["status"].get("elapsed") or 0,
                "league": f["league"]["name"],
            })
        except Exception:
            pass

    results = []
    for m in live_matches:
        try:
            alert = score_live_match(m["id"], m["home"], m["away"],
                                     m["home_score"], m["away_score"],
                                     m["minute"], m["league"])
            if alert:
                msg = build_telegram_message(alert)
                sent = send_telegram(msg)
                alerted_fixtures.add(m["id"])
                results.append({"match": f"{m['home']} vs {m['away']}", "sent": sent})
            else:
                results.append({"match": f"{m['home']} vs {m['away']}", "sent": False, "reason": "conditions not met"})
        except Exception as e:
            results.append({"match": f"{m['home']} vs {m['away']}", "error": str(e)})

    return {"live_matches": len(live_matches), "results": results}


@app.get("/results/today")
async def get_results_today():
    """Return today's finished AND in-progress fixtures for leg result checking.
    Cached 2 minutes — fresh enough for result updates without burning quota."""
    today = datetime.now().strftime("%Y-%m-%d")
    cache_key = f"results_today_{today}_{datetime.now().strftime('%H%M')[:-1]}"  # 10-min buckets
    cached = cache_get(cache_key)
    if cached:
        return cached

    # Fetch finished + live — lets frontend update legs mid-match too
    fixtures = []
    for status_filter in ["FT-AET-PEN", "1H-HT-2H-ET-P"]:
        data = api_get("fixtures", {
            "date": today,
            "timezone": "Europe/London",
            "status": status_filter
        })
        for f in data.get("response", []):
            try:
                fixtures.append({
                    "id": f["fixture"]["id"],
                    "home": f["teams"]["home"]["name"],
                    "away": f["teams"]["away"]["name"],
                    "home_score": f["goals"]["home"] or 0,
                    "away_score": f["goals"]["away"] or 0,
                    "status": f["fixture"]["status"]["short"],
                    "league": f["league"]["name"],
                    "minute": f["fixture"]["status"].get("elapsed"),
                })
            except Exception:
                pass

    result = {"fixtures": fixtures, "date": today, "count": len(fixtures)}
    cache_set(cache_key, result, 120)  # 2 min cache
    return result


# Running Telegram P&L tracker (in-memory, resets on deploy)
telegram_pl = {"tips": 0, "won": 0, "lost": 0, "profit": 0.0, "stake": 10.0}

@app.post("/telegram/result")
async def post_telegram_result(payload: dict):
    """Called when a tip result is known — posts result to free channel and updates P&L."""
    result  = payload.get("result", "")   # "won" or "lost"
    match   = payload.get("match", "")
    bet     = payload.get("bet", "")
    odds    = float(payload.get("odds", 2.0))
    channel = payload.get("channel", "free")  # "free" or "pro"

    stake = telegram_pl["stake"]
    telegram_pl["tips"] += 1

    if result == "won":
        profit = round(stake * odds - stake, 2)
        telegram_pl["won"] += 1
        telegram_pl["profit"] = round(telegram_pl["profit"] + profit, 2)
        msg = (
            f"💥 <b>BOOM! WINNER!</b> 💥\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ <b>{match}</b>\n"
            f"💰 {bet} @ {odds}\n"
            f"🏆 <b>+£{profit:.2f} profit on £{stake:.0f} stake!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Running P&L (£{stake:.0f} stakes)</b>\n"
            f"  Tips: {telegram_pl['tips']} · Won: {telegram_pl['won']} · Lost: {telegram_pl['lost']}\n"
            f"  Profit: <b>{'+'if telegram_pl['profit']>=0 else ''}£{telegram_pl['profit']:.2f}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔒 Full analysis on AccaGenius Pro\n"
            f"👉 accagenius.com"
        )
    else:
        telegram_pl["lost"] += 1
        telegram_pl["profit"] = round(telegram_pl["profit"] - stake, 2)
        msg = (
            f"❌ <b>No Luck This Time</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<s>{match} — {bet}</s>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Running P&L (£{stake:.0f} stakes)</b>\n"
            f"  Tips: {telegram_pl['tips']} · Won: {telegram_pl['won']} · Lost: {telegram_pl['lost']}\n"
            f"  Profit: <b>{'+'if telegram_pl['profit']>=0 else ''}£{telegram_pl['profit']:.2f}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"More tips coming — stay tuned!\n"
            f"🔒 Pro alerts → accagenius.com"
        )

    target = TELEGRAM_CHANNEL_FREE if channel == "free" else (TELEGRAM_CHANNEL_PRO or TELEGRAM_CHANNEL_ID)
    sent = send_telegram_to(msg, target)
    return {"sent": sent, "pl": telegram_pl}



@app.get("/fixtures/{league_code}")
async def get_fixtures(league_code: str):
    league_id = LEAGUE_IDS.get(league_code.upper())
    if not league_id:
        raise HTTPException(404, "League not found")
    cache_key = f"fixtures_{league_code.upper()}"
    cached = cache_get(cache_key)
    if cached:
        return cached
    data = api_get("fixtures", {
        "league": league_id, "season": get_season(),
        "next": 10, "timezone": "Europe/London"  # reduced from 20
    })
    by_date = {}
    for f in data.get("response", []):
        date = f["fixture"]["date"].split("T")[0]
        by_date.setdefault(date, []).append({
            "id": f["fixture"]["id"],
            "date": f["fixture"]["date"],
            "time": datetime.fromisoformat(f["fixture"]["date"].replace("Z", "+00:00")).strftime("%H:%M"),
            "home": f["teams"]["home"]["name"],
            "away": f["teams"]["away"]["name"],
            "home_id": f["teams"]["home"]["id"],
            "away_id": f["teams"]["away"]["id"],
            "home_logo": f["teams"]["home"]["logo"],
            "away_logo": f["teams"]["away"]["logo"],
            "venue": f["fixture"]["venue"]["name"],
            "referee": f["fixture"]["referee"],
            "league_id": league_id
        })
    result = {"league": league_code, "fixtures": by_date}
    if by_date:
        cache_set(cache_key, result, 3600)  # 1 hour
    return result


@app.get("/today")
async def get_today_fixtures():
    """All fixtures for today — cached 10 minutes to save API quota."""
    today_str = datetime.now(_LONDON).strftime("%Y-%m-%d") if _LONDON else datetime.utcnow().strftime("%Y-%m-%d")
    cache_key = f"today_{today_str}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    def fetch_league_today(league: dict, timeout: int = 12) -> list:
        try:
            data = api_get("fixtures", {
                "league": league["id"], "date": today_str, "timezone": "Europe/London"
            }, timeout=timeout)
            results = []
            for f in data.get("response", []):
                try:
                    status = f["fixture"]["status"]["short"]
                    kick_time = datetime.fromisoformat(
                        f["fixture"]["date"].replace("Z", "+00:00")
                    ).strftime("%H:%M")
                    results.append({
                        "id": f["fixture"]["id"],
                        "time": kick_time,
                        "home": f["teams"]["home"]["name"],
                        "away": f["teams"]["away"]["name"],
                        "home_id": f["teams"]["home"]["id"],
                        "away_id": f["teams"]["away"]["id"],
                        "home_logo": f["teams"]["home"]["logo"],
                        "away_logo": f["teams"]["away"]["logo"],
                        "league": league["name"],
                        "league_code": league["code"],
                        "league_flag": league["flag"],
                        "league_id": league["id"],
                        "status": status,
                        "home_score": f["goals"]["home"],
                        "away_score": f["goals"]["away"],
                        "minute": f["fixture"]["status"]["elapsed"],
                        "_raw": f,  # keep raw for acca generator to reuse (saves API calls)
                        "venue": (f["fixture"]["venue"].get("name") or ""),
                        "odds_available": False,
                    })
                except Exception:
                    pass
            return results
        except Exception:
            return None  # None = failed (vs [] = genuinely no games)

    loop = asyncio.get_event_loop()

    # Pass 1: all leagues with 12s timeout
    with ThreadPoolExecutor(max_workers=19) as executor:
        futures = [loop.run_in_executor(executor, fetch_league_today, lg, 12) for lg in LEAGUES]
        results = await asyncio.gather(*futures, return_exceptions=True)

    # Pass 2: retry failed leagues with 20s timeout
    failed_leagues = [LEAGUES[i] for i, r in enumerate(results) if r is None or isinstance(r, Exception)]
    if failed_leagues:
        logger.warning(f"⚠️  /today retry for: {[l['code'] for l in failed_leagues]}")
        with ThreadPoolExecutor(max_workers=len(failed_leagues)) as executor:
            retry_futures = [loop.run_in_executor(executor, fetch_league_today, lg, 20) for lg in failed_leagues]
            retry_results = await asyncio.gather(*retry_futures, return_exceptions=True)
        fi = 0
        for i, r in enumerate(results):
            if r is None or isinstance(r, Exception):
                results[i] = retry_results[fi] if not isinstance(retry_results[fi], Exception) else []
                fi += 1

    all_matches = sorted(
        [m for r in results if isinstance(r, list) for m in r],
        key=lambda x: x["time"]
    )

    # Fetch odds for scheduled matches only (skip live/finished)
    def fetch_odds_today(m: dict) -> dict:
        if m.get("status") not in ["NS", "TBD"]:
            return m
        odds = get_real_odds(m["id"])
        m["odds_home"]      = odds.get("home", 0)
        m["odds_draw"]      = odds.get("draw", 0)
        m["odds_away"]      = odds.get("away", 0)
        m["odds_home_bk"]   = odds.get("home_bk", "")
        m["odds_draw_bk"]   = odds.get("draw_bk", "")
        m["odds_away_bk"]   = odds.get("away_bk", "")
        m["odds_available"] = odds.get("available", False)
        return m

    if all_matches:
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures_odds = [loop.run_in_executor(executor, fetch_odds_today, m) for m in all_matches]
            all_matches = list(await asyncio.gather(*futures_odds))

    still_failed = [LEAGUES[i] for i, r in enumerate(results) if r is None or isinstance(r, Exception)]
    result = {"matches": all_matches, "count": len(all_matches), "date": today_str,
              "leagues_failed": [l["code"] for l in still_failed]}
    if all_matches and len(still_failed) <= 1:
        cache_set(cache_key, result, 1200)
    elif all_matches:
        cache_set(cache_key, result, 120)  # partial — retry soon
    logger.info(f"/today: {len(all_matches)} matches, {len(still_failed)} leagues failed")
    return result


@app.post("/today/refresh")
async def refresh_today():
    today_str = datetime.now(_LONDON).strftime("%Y-%m-%d") if _LONDON else datetime.utcnow().strftime("%Y-%m-%d")
    cache_key = f"today_{today_str}"
    throttle_key = f"refresh_throttle_{today_str}"
    # Rate-limit manual refreshes to once every 5 minutes
    if cache_get(throttle_key):
        return {"ok": False, "message": "Refreshed too recently — try again in 5 minutes"}
    if cache_key in _cache:
        del _cache[cache_key]
    cache_set(throttle_key, True, 300)  # 5 minute cooldown
    return {"ok": True, "message": "Cache cleared — refreshing now"}


@app.get("/next-round")
async def get_next_round_fixtures():
    """Next round of fixtures across all leagues — cached 60 minutes"""
    cache_key = f"next_round_{datetime.now().strftime('%Y-%m-%d_%H')}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    today = datetime.now().date()
    cutoff = today + timedelta(days=3)

    def fetch_league_upcoming(league: dict) -> list:
        data = api_get("fixtures", {
            "league": league["id"], "season": get_season(),
            "next": 10, "timezone": "Europe/London"
        }, timeout=8)
        results = []
        for f in data.get("response", []):
            try:
                fd = datetime.fromisoformat(f["fixture"]["date"].replace("Z", "+00:00")).date()
                if fd > cutoff:
                    continue
                results.append({
                    "id": f["fixture"]["id"],
                    "date": fd.isoformat(),
                    "time": datetime.fromisoformat(f["fixture"]["date"].replace("Z", "+00:00")).strftime("%H:%M"),
                    "home": f["teams"]["home"]["name"],
                    "away": f["teams"]["away"]["name"],
                    "home_id": f["teams"]["home"]["id"],
                    "away_id": f["teams"]["away"]["id"],
                    "home_logo": f["teams"]["home"]["logo"],
                    "away_logo": f["teams"]["away"]["logo"],
                    "league": league["name"],
                    "league_code": league["code"],
                    "league_flag": league["flag"],
                    "league_id": league["id"],
                    "venue": f["fixture"]["venue"]["name"] or "",
                })
            except Exception:
                pass
        return results

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=17) as executor:
        futures = [loop.run_in_executor(executor, fetch_league_upcoming, league) for league in LEAGUES]
        results = await asyncio.gather(*futures)

    all_matches = [m for sublist in results for m in sublist]

    # Fetch odds in parallel
    def fetch_odds_next(m: dict) -> dict:
        odds = get_real_odds(m["id"])
        m["odds_home"] = odds.get("home", 0)
        m["odds_draw"] = odds.get("draw", 0)
        m["odds_away"] = odds.get("away", 0)
        m["odds_home_bk"] = odds.get("home_bk", "")
        m["odds_draw_bk"] = odds.get("draw_bk", "")
        m["odds_away_bk"] = odds.get("away_bk", "")
        m["odds_available"] = odds.get("available", False)
        return m

    if all_matches:
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures2 = [loop.run_in_executor(executor, fetch_odds_next, m) for m in all_matches]
            all_matches = list(await asyncio.gather(*futures2))

    all_matches.sort(key=lambda x: (x["date"], x["time"]))
    result = {"matches": all_matches, "count": len(all_matches)}
    if all_matches:
        cache_set(cache_key, result, 3600)  # 60 minutes
    return result


@app.get("/live")
async def get_live():
    """Live matches — cached 90 seconds (frequent enough for score updates)"""
    cache_key = "live_matches"
    cached = cache_get(cache_key)
    if cached:
        return cached

    data = api_get("fixtures", {"live": "all", "timezone": "Europe/London"})
    # Build set of our league IDs for fast lookup
    our_league_ids = {l["id"] for l in LEAGUES}
    matches = []
    for f in data.get("response", []):
        try:
            league_id = f["league"]["id"]
            if league_id not in our_league_ids:
                continue  # Skip — not one of our leagues
            matches.append({
                "id": f["fixture"]["id"],
                "home": f["teams"]["home"]["name"],
                "away": f["teams"]["away"]["name"],
                "home_logo": f["teams"]["home"]["logo"],
                "away_logo": f["teams"]["away"]["logo"],
                "home_id": f["teams"]["home"]["id"],
                "away_id": f["teams"]["away"]["id"],
                "home_score": f["goals"]["home"] or 0,
                "away_score": f["goals"]["away"] or 0,
                "minute": f["fixture"]["status"]["elapsed"],
                "status": f["fixture"]["status"]["short"],
                "status_long": f["fixture"]["status"]["long"],
                "league": f["league"]["name"],
                "league_id": league_id,
                "league_logo": f["league"]["logo"],
                "venue": f["fixture"]["venue"]["name"],
                "ht_home": (f.get("score", {}).get("halftime", {}) or {}).get("home"),
                "ht_away": (f.get("score", {}).get("halftime", {}) or {}).get("away"),
            })
        except Exception:
            pass
    result = {"matches": matches, "count": len(matches)}
    cache_set(cache_key, result, 90)  # 90 seconds
    return result

@app.get("/live/{fixture_id}/events")
async def get_live_events(fixture_id: int):
    """Minute-by-minute events for a live match"""
    data = api_get("fixtures/events", {"fixture": fixture_id})
    events = []
    for e in data.get("response", []):
        try:
            events.append({
                "minute": e["time"]["elapsed"],
                "extra": e["time"].get("extra"),
                "team": e["team"]["name"],
                "team_id": e["team"]["id"],
                "player": (e.get("player") or {}).get("name"),
                "assist": (e.get("assist") or {}).get("name"),
                "type": e["type"],
                "detail": e["detail"],
                "comments": e.get("comments")
            })
        except Exception:
            pass
    events.sort(key=lambda x: x["minute"] or 0)
    return {"events": events}


@app.get("/live/{fixture_id}/stats")
async def get_live_stats(fixture_id: int):
    """Live match statistics — real API-Football field names mapped to clean keys"""
    data = api_get("fixtures/statistics", {"fixture": fixture_id})
    stats = {}

    # Exact API-Football stat type names → clean key mapping
    FIELD_MAP = {
        "Shots on Goal":         "shots_on_goal",
        "Shots off Goal":        "shots_off_goal",
        "Total Shots":           "total_shots",
        "Blocked Shots":         "blocked_shots",
        "Shots insidebox":       "shots_inside_box",
        "Shots outsidebox":      "shots_outside_box",
        "Fouls":                 "fouls",
        "Corner Kicks":          "corner_kicks",
        "Offsides":              "offsides",
        "Ball Possession":       "ball_possession",
        "Yellow Cards":          "yellow_cards",
        "Red Cards":             "red_cards",
        "Goalkeeper Saves":      "goalkeeper_saves",
        "Total passes":          "total_passes",
        "Passes accurate":       "passes_accurate",
        "Passes %":              "passes_pct",
        "expected_goals":        "expected_goals",   # real xG from API
        "Expected Goals":        "expected_goals",
    }

    for team_data in data.get("response", []):
        team_name = team_data["team"]["name"]
        team_stats = {}
        for s in team_data.get("statistics", []):
            raw_type = s.get("type", "")
            raw_val = s.get("value")
            # Use exact map first, then normalise as fallback
            clean_key = FIELD_MAP.get(raw_type) or raw_type.lower().replace(" ", "_").replace("%", "pct").replace(".", "")
            # Strip % from possession values
            if isinstance(raw_val, str) and raw_val.endswith("%"):
                try:
                    raw_val = int(raw_val.replace("%", ""))
                except ValueError:
                    pass
            team_stats[clean_key] = raw_val if raw_val is not None else 0
        stats[team_name] = team_stats

    return {"stats": stats, "fixture_id": fixture_id}


@app.get("/fixture/{fixture_id}/lineups")
async def get_lineups(fixture_id: int):
    """Starting lineups"""
    data = api_get("fixtures/lineups", {"fixture": fixture_id})
    lineups = []
    for team in data.get("response", []):
        try:
            lineups.append({
                "team": team["team"]["name"],
                "team_logo": team["team"]["logo"],
                "formation": team.get("formation"),
                "startXI": [{"name": p["player"]["name"], "number": p["player"]["number"], "pos": p["player"]["pos"]} for p in team.get("startXI", [])],
                "substitutes": [{"name": p["player"]["name"], "number": p["player"]["number"], "pos": p["player"]["pos"]} for p in team.get("substitutes", [])]
            })
        except Exception:
            pass
    return {"lineups": lineups}


@app.get("/standings/{league_code}")
async def get_standings(league_code: str):
    league_id = LEAGUE_IDS.get(league_code.upper())
    if not league_id:
        raise HTTPException(404, "League not found")
    cache_key = f"standings_{league_code.upper()}"
    cached = cache_get(cache_key)
    if cached:
        return cached
    data = api_get("standings", {"league": league_id, "season": get_season()})
    try:
        response = data.get("response", [])
        if not response:
            return {"league": league_code, "standings": [], "message": "No standings data available yet for this season"}
        # Some competitions (CL, EL, ECL, EL1, EL2) return multiple groups — flatten all
        all_groups = response[0]["league"].get("standings", [])
        if not all_groups:
            return {"league": league_code, "standings": [], "message": "No standings available"}
        # Use first group (main table) — works for all single-table leagues
        raw = all_groups[0]
        standings = []
        for t in raw:
            try:
                standings.append({
                    "position": t["rank"],
                    "team": t["team"]["name"],
                    "logo": t["team"]["logo"],
                    "played": t["all"]["played"],
                    "won": t["all"]["win"],
                    "drawn": t["all"]["draw"],
                    "lost": t["all"]["lose"],
                    "gf": t["all"]["goals"]["for"],
                    "ga": t["all"]["goals"]["against"],
                    "gd": t.get("goalsDiff", 0),
                    "goalDifference": t.get("goalsDiff", 0),
                    "points": t["points"],
                    "form": t.get("form", ""),
                    "description": (t.get("description") or "")
                })
            except Exception:
                pass
        result = {"league": league_code, "standings": standings}
        if standings:
            cache_set(cache_key, result, 7200)  # 2 hours
        return result
    except Exception as e:
        return {"league": league_code, "standings": [], "message": str(e)}


@app.get("/h2h/{team1_id}/{team2_id}")
async def get_h2h(team1_id: int, team2_id: int):
    cache_key = f"h2h_{min(team1_id,team2_id)}_{max(team1_id,team2_id)}"
    cached = cache_get(cache_key)
    if cached:
        return cached
    data = api_get("fixtures/headtohead", {"h2h": f"{team1_id}-{team2_id}", "last": 10})
    matches = []
    for f in data.get("response", []):
        try:
            matches.append({
                "date": f["fixture"]["date"].split("T")[0],
                "home": f["teams"]["home"]["name"],
                "away": f["teams"]["away"]["name"],
                "home_logo": f["teams"]["home"]["logo"],
                "away_logo": f["teams"]["away"]["logo"],
                "home_score": f["goals"]["home"],
                "away_score": f["goals"]["away"],
                "league": f["league"]["name"]
            })
        except Exception:
            pass
    result = {"matches": matches}
    if matches:
        cache_set(cache_key, result, 21600)  # 6 hours — h2h is historical
    return result


@app.get("/quota")
async def quota_status():
    """Check API Football quota usage."""
    today = datetime.now().date()
    if today != _api_call_reset:
        return {"calls_today": 0, "plan_limit": 7500, "remaining": 7500, "status": "fresh"}
    remaining = max(0, 7500 - _api_call_count)
    pct = round(_api_call_count / 7500 * 100, 1)
    status = "critical" if _api_call_count > 6000 else "warning" if _api_call_count > 3000 else "ok"
    return {
        "calls_today": _api_call_count,
        "plan_limit": 7500,
        "remaining": remaining,
        "used_pct": pct,
        "status": status,
        "reset": str(_api_call_reset),
    }


@app.get("/cache-status")
async def get_cache_status():
    """Monitor API quota usage — visit this URL to check how many calls used today"""
    stats = cache_stats()
    return {
        "api_calls_today": _api_call_count,
        "daily_limit": 7500,
        "calls_remaining": max(0, 7500 - _api_call_count),
        "reset_date": str(_api_call_reset),
        "cache": stats,
        "warning": "CRITICAL" if _api_call_count > 7000 else ("HIGH" if _api_call_count > 5000 else ("MODERATE" if _api_call_count > 3000 else "OK"))
    }


@app.get("/form/{team_id}/{league_id}")
async def get_form(team_id: int, league_id: int):
    return get_team_form(team_id, league_id, last=10)


@app.get("/predictions/{fixture_id}")
async def get_predictions(fixture_id: int):
    data = api_get("predictions", {"fixture": fixture_id})
    preds = data.get("response", [])
    if not preds:
        return {"prediction": None, "predictions": {}}
    pred = preds[0].get("predictions", {})
    teams = preds[0].get("teams", {})
    comp = preds[0].get("comparison", {})
    winner = pred.get("winner", {}) or {}
    percent = pred.get("percent", {}) or {}
    goals = pred.get("goals", {}) or {}
    return {
        "prediction": {
            "winner": winner.get("name"),
            "winner_comment": winner.get("comment"),
            "percent_home": percent.get("home", "33%"),
            "percent_draw": percent.get("draw", "33%"),
            "percent_away": percent.get("away", "33%"),
            "goals_home": goals.get("home"),
            "goals_away": goals.get("away"),
            "advice": pred.get("advice", ""),
            "under_over": pred.get("goals", {}).get("total")
        },
        "predictions": pred,
        "comparison": comp,
        "teams": {
            "home": teams.get("home", {}).get("name"),
            "away": teams.get("away", {}).get("name")
        }
    }


@app.get("/odds/{fixture_id}")
async def get_odds(fixture_id: int):
    """Full odds across multiple bookmakers and markets — cached 15 mins"""
    cache_key = f"full_odds_{fixture_id}"
    cached = cache_get(cache_key)
    if cached:
        return cached
    data = api_get("odds", {"fixture": fixture_id}, timeout=5)
    bookmakers_data = {}
    markets = {}

    response_list = data.get("response", [])
    if not response_list:
        return {"bookmakers": {}, "markets": {}, "best_odds": {
            "home": {"bookmaker": "", "odds": 0},
            "draw": {"bookmaker": "", "odds": 0},
            "away": {"bookmaker": "", "odds": 0}
        }}
    response_data = response_list[0]
    for bm in response_data.get("bookmakers", [])[:8]:
        bm_name = bm["name"]
        bm_odds = {}
        for bet in bm.get("bets", []):
            bname = bet["name"]
            bm_odds[bname] = {v["value"]: v["odd"] for v in bet.get("values", [])}
            if bname not in markets:
                markets[bname] = {}
            markets[bname][bm_name] = {v["value"]: v["odd"] for v in bet.get("values", [])}
        bookmakers_data[bm_name] = bm_odds

    # Best odds per outcome for Match Winner
    best = {"home": ("", 0), "draw": ("", 0), "away": ("", 0)}
    mw = markets.get("Match Winner", {})
    for bm, vals in mw.items():
        try:
            if float(vals.get("Home", 0)) > best["home"][1]:
                best["home"] = (bm, float(vals["Home"]))
            if float(vals.get("Draw", 0)) > best["draw"][1]:
                best["draw"] = (bm, float(vals["Draw"]))
            if float(vals.get("Away", 0)) > best["away"][1]:
                best["away"] = (bm, float(vals["Away"]))
        except Exception:
            pass

    result = {
        "bookmakers": bookmakers_data,
        "markets": markets,
        "best_odds": {
            "home": {"bookmaker": best["home"][0], "odds": best["home"][1]},
            "draw": {"bookmaker": best["draw"][0], "odds": best["draw"][1]},
            "away": {"bookmaker": best["away"][0], "odds": best["away"][1]}
        }
    }
    cache_set(cache_key, result, 900)  # 15 minutes
    return result


@app.get("/player-stats/{team_id}")
async def get_player_stats(team_id: int):
    """Top scorers/assists for a team"""
    data = api_get("players/topscorers", {"league": 39, "season": get_season()})
    players = []
    for p in data.get("response", [])[:20]:
        if p.get("statistics", [{}])[0].get("team", {}).get("id") == team_id:
            s = p["statistics"][0]
            players.append({
                "name": p["player"]["name"],
                "photo": p["player"]["photo"],
                "goals": s["goals"]["total"] or 0,
                "assists": s["goals"]["assists"] or 0,
                "matches": s["games"]["appearences"] or 0,
                "rating": s["games"].get("rating")
            })
    return {"players": players}


@app.get("/today-acca")
async def get_today_acca():
    """Generate today's 3 themed accas ONCE and cache all day — same picks for every user."""
    today_str = datetime.now(_LONDON).strftime("%Y-%m-%d") if _LONDON else datetime.utcnow().strftime("%Y-%m-%d")
    cache_key = f"today_acca_{today_str}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    leagues = ["PL","ELC","BL1","SA","FL1","PD","CL","EL","NED","PPL","TUR","BEL","SPFL","EL1","EL2"]

    async def fetch_picks(risk: str, endpoint: str = "form") -> list:
        try:
            if endpoint == "rank":
                req = AccaRequest(selections=8, risk=risk, leagues=leagues, market="winner", today_only=True)
                result = await generate_table_rank_acca(req)
            else:
                req = AccaRequest(selections=8, risk=risk, leagues=leagues, market="winner", today_only=True)
                result = await generate_acca(req)
            return result.get("selections", []) if isinstance(result, dict) else []
        except Exception as e:
            logger.error(f"today-acca fetch error ({risk}/{endpoint}): {e}")
            return []

    # Run sequentially so form cache from pass 1 is reused in passes 2+3
    # Parallel = 3× the form API calls on cold cache
    form_picks  = await fetch_picks("balanced", "form")
    rank_picks  = await fetch_picks("balanced", "rank")
    value_picks = await fetch_picks("risky",    "form")

    # Global dedup — no match appears in more than one acca
    used_ids = set()
    used_teams = set()

    def dedup(picks, n):
        out = []
        for p in picks:
            fid = p.get("id")
            team_key = "|".join(sorted([p.get("home",""), p.get("away","")]))
            if fid in used_ids or team_key in used_teams:
                continue
            out.append(p)
            used_ids.add(fid)
            used_teams.add(team_key)
            if len(out) >= n:
                break
        return out

    t1 = dedup(form_picks,  3)
    t2 = dedup(rank_picks,  3)
    t3 = dedup(value_picks, 4)

    result = {
        "date": today_str,
        "accas": [
            {"theme": "form",  "label": "Form Treble",   "emoji": "📈", "picks": t1},
            {"theme": "rank",  "label": "Rank Treble",   "emoji": "🏆", "picks": t2},
            {"theme": "value", "label": "Value 4-Fold",  "emoji": "💎", "picks": t3},
        ]
    }

    # Cache all day — resets at midnight when date changes
    if any(a["picks"] for a in result["accas"]):
        cache_set(cache_key, result, 86400)  # 24h but key includes date so auto-resets

    return result


@app.post("/generate-acca")
async def generate_acca(request: AccaRequest):
    try:
        leagues = request.leagues if request.leagues else ["PL", "ELC", "PD", "BL1", "SA", "FL1", "CL", "EL", "ECL", "TUR", "NED", "PPL", "BEL", "SPFL"]
        today = datetime.now().date()
        cutoff = today if request.today_only else today + timedelta(days=3)
        all_picks = []
        seen = set()
        random.shuffle(leagues)

        # ── Always work from today's fixture list (date-accurate, cached) ──
        today_str_acca = datetime.now().strftime("%Y-%m-%d")
        _today_cache_key = f"today_{today_str_acca}"
        _today_cached = cache_get(_today_cache_key)

        # Build league → raw fixtures map from the /today cache
        _today_raw_by_league: dict = {}
        if _today_cached:
            for m in _today_cached.get("matches", []):
                raw = m.get("_raw")
                if raw:
                    lc_key = m.get("league_code", "")
                    _today_raw_by_league.setdefault(lc_key, []).append(raw)

        def fetch_league_picks(lc: str) -> list:
            lid = LEAGUE_IDS.get(lc)
            if not lid: return []
            picks = []
            try:
                # Use cached today fixtures if available — avoids extra API calls
                # and guarantees we only use today's games
                if lc in _today_raw_by_league:
                    raw_fixtures = _today_raw_by_league[lc]
                else:
                    # Cache miss — fetch by DATE (not next:N) so we still get today only
                    data = api_get("fixtures", {
                        "league": lid,
                        "date": today_str_acca,
                        "timezone": "Europe/London",
                        "season": get_season()
                    })
                    raw_fixtures = data.get("response", [])

                fixtures = sorted(
                    raw_fixtures,
                    key=lambda f: f["fixture"]["date"] if isinstance(f, dict) and "fixture" in f else ""
                )
                picks_this_league = 0
                for f in fixtures:
                    if picks_this_league >= 3: break  # max 3 picks per league
                    fid = f["fixture"]["id"]
                    if fid in seen: continue
                    # Date guard — only today
                    fd = datetime.fromisoformat(f["fixture"]["date"].replace("Z", "+00:00")).date()
                    if fd != today: continue
                    status = f["fixture"]["status"]["short"]
                    if status in ("1H","HT","2H","ET","P","FT","AET","PEN"): continue
                    hf = get_team_form(f["teams"]["home"]["id"], lid)
                    af = get_team_form(f["teams"]["away"]["id"], lid)
                    if hf.get("games", 0) == 0 and af.get("games", 0) == 0:
                        continue
                    pick = analyze_and_pick(f, hf, af, request.risk, request.market)
                    if pick and pick["confidence"] >= 45:
                        picks.append(pick)
                        picks_this_league += 1
            except Exception as e:
                logger.error(f"League pick error {lc}: {e}")
            return picks

        with ThreadPoolExecutor(max_workers=7) as ex:  # reduced to limit API calls
            import asyncio
            loop = asyncio.get_event_loop()
            futures = [loop.run_in_executor(ex, fetch_league_picks, lc) for lc in leagues]
            results = await asyncio.gather(*futures)

        for league_picks in results:
            for pick in league_picks:
                if pick["id"] not in seen:
                    seen.add(pick["id"])
                    all_picks.append(pick)

        all_picks.sort(key=lambda x: x["confidence"], reverse=True)

        # Limit draws — max 1 draw pick per 5 selections to avoid draw-heavy accas
        final = []
        draw_count = 0
        for p in all_picks:
            if p.get("bet") == "Draw":
                if draw_count >= 1: continue  # skip extra draws
                draw_count += 1
            final.append(p)
            if len(final) >= request.selections: break

        picks = final

        if not picks:
            return {"message": "No picks found — try selecting more leagues or switching to Balanced risk.", 
                    "total_selections": 0, "total_odds": 0, "confidence": 0, "selections": []}

        total_odds = 1.0
        for p in picks: total_odds *= p.get("odds", 1.0)
        avg_conf = sum(p["confidence"] for p in picks) / len(picks)

        return {
            "message": f"AI Acca — {request.risk.capitalize()} risk — Next 3 Days",
            "total_selections": len(picks),
            "total_odds": round(total_odds, 2),
            "confidence": round(avg_conf),
            "risk_level": request.risk,
            "market": request.market,
            "selections": picks
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/generate-ht-acca")
async def generate_ht_acca(request: HTAccaRequest):
    try:
        leagues = request.leagues if request.leagues else ["PL", "ELC", "PD", "BL1", "SA", "FL1"]
        today = datetime.now().date()
        cutoff = today + timedelta(days=3)
        all_picks = []
        seen = set()
        random.shuffle(leagues)
        for lc in leagues:
            lid = LEAGUE_IDS.get(lc)
            if not lid: continue
            data = api_get("fixtures", {"league": lid, "season": get_season(), "next": 15, "timezone": "Europe/London"})
            fixtures = sorted(data.get("response", []), key=lambda f: f["fixture"]["date"])
            for f in fixtures:
                fid = f["fixture"]["id"]
                if fid in seen: continue
                fd = datetime.fromisoformat(f["fixture"]["date"].replace("Z", "+00:00")).date()
                if fd < today or fd > cutoff: continue
                hf = get_team_form(f["teams"]["home"]["id"], lid)
                af = get_team_form(f["teams"]["away"]["id"], lid)
                pick = ht_pick(f, hf, af)
                if pick and pick["confidence"] >= 60:
                    seen.add(fid)
                    odds = get_real_odds(fid)
                    pick["odds_home"] = odds.get("home", 0)
                    pick["odds_draw"] = odds.get("draw", 0)
                    pick["odds_away"] = odds.get("away", 0)
                    pick["odds_home_bk"] = odds.get("home_bk", "")
                    pick["odds_available"] = odds.get("available", False)
                    all_picks.append(pick)
            if len(all_picks) >= request.selections * 3: break

        all_picks.sort(key=lambda x: x["confidence"], reverse=True)
        picks = all_picks[:request.selections]
        if not picks:
            return {"message": "No HT picks in next 3 days", "total_selections": 0, "total_odds": 0, "confidence": 0, "selections": []}
        total_odds = 1.0
        for p in picks: total_odds *= p["odds"]
        avg_conf = sum(p["confidence"] for p in picks) / len(picks)
        return {
            "message": "HT Acca — Next 3 days",
            "total_selections": len(picks),
            "total_odds": round(total_odds, 2),
            "confidence": round(avg_conf),
            "selections": picks
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/generate-btts-acca")
async def generate_btts_acca(request: AccaRequest):
    """BTTS acca — finds fixtures where both teams have high BTTS% historically, unique games only."""
    try:
        leagues = request.leagues if request.leagues else ["PL","ELC","BL1","SA","FL1","PD"]
        today = datetime.now().date()
        cutoff = today + timedelta(days=3)
        all_picks = []
        seen = set()
        random.shuffle(leagues)

        def fetch_btts(lc: str) -> list:
            lid = LEAGUE_IDS.get(lc)
            if not lid: return []
            picks = []
            try:
                data = api_get("fixtures", {"league": lid, "season": get_season(), "next": 10, "timezone": "Europe/London"})
                for f in sorted(data.get("response", []), key=lambda x: x["fixture"]["date"]):
                    fid = f["fixture"]["id"]
                    if fid in seen: continue
                    fd = datetime.fromisoformat(f["fixture"]["date"].replace("Z","+00:00")).date()
                    if fd < today or fd > cutoff: continue
                    status = f["fixture"]["status"]["short"]
                    if status in ("1H","HT","2H","ET","P","FT","AET","PEN"): continue
                    hf = get_team_form(f["teams"]["home"]["id"], lid)
                    af = get_team_form(f["teams"]["away"]["id"], lid)
                    h_btts = hf.get("btts_pct", 0)
                    a_btts = af.get("btts_pct", 0)
                    combined_btts = round((h_btts + a_btts) / 2)
                    if combined_btts >= 55:
                        dt = datetime.fromisoformat(f["fixture"]["date"].replace("Z","+00:00"))
                        odds = get_real_odds(fid)
                        conf = min(88, 55 + int(combined_btts * 0.4))
                        picks.append({
                            "id": fid,
                            "home": f["teams"]["home"]["name"],
                            "away": f["teams"]["away"]["name"],
                            "home_logo": f["teams"]["home"]["logo"],
                            "away_logo": f["teams"]["away"]["logo"],
                            "date": dt.strftime("%d/%m %H:%M"),
                            "home_id": f["teams"]["home"]["id"],
                            "away_id": f["teams"]["away"]["id"],
                            "league_id": lid,
                            "bet": "Both Teams to Score",
                            "market_type": "BTTS",
                            "odds": odds.get("btts", 1.72),
                            "odds_home": odds.get("home", 0),
                            "odds_draw": odds.get("draw", 0),
                            "odds_away": odds.get("away", 0),
                            "odds_available": odds.get("available", False),
                            "confidence": conf,
                            "reasoning": f"Combined BTTS% {combined_btts}% — {f['teams']['home']['name']} {h_btts}% | {f['teams']['away']['name']} {a_btts}%",
                        })
            except Exception:
                pass
            return picks

        with ThreadPoolExecutor(max_workers=10) as ex:
            loop = asyncio.get_event_loop()
            results = await asyncio.gather(*[loop.run_in_executor(ex, fetch_btts, lc) for lc in leagues])

        for league_picks in results:
            for pick in league_picks:
                if pick["id"] not in seen:
                    seen.add(pick["id"])
                    all_picks.append(pick)

        all_picks.sort(key=lambda x: x["confidence"], reverse=True)
        picks = all_picks[:request.selections]
        if not picks:
            return {"message": "No BTTS picks found.", "total_selections": 0, "total_odds": 0, "confidence": 0, "selections": []}

        total_odds = 1.0
        for p in picks: total_odds *= p.get("odds", 1.72)
        avg_conf = sum(p["confidence"] for p in picks) / len(picks)
        return {"message": "BTTS Acca", "total_selections": len(picks), "total_odds": round(total_odds, 2),
                "confidence": round(avg_conf), "market": "btts", "selections": picks}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/generate-form-crash-acca")
async def generate_form_crash_acca(request: AccaRequest):
    """Out-of-form / form-crash acca — teams on bad run being strong favourites = value against them."""
    try:
        leagues = request.leagues if request.leagues else ["PL","ELC","BL1","SA","FL1","PD","CL"]
        today = datetime.now().date()
        cutoff = today + timedelta(days=3)
        all_picks = []
        seen = set()
        random.shuffle(leagues)

        def fetch_form_crash(lc: str) -> list:
            lid = LEAGUE_IDS.get(lc)
            if not lid: return []
            picks = []
            try:
                data = api_get("fixtures", {"league": lid, "season": get_season(), "next": 10, "timezone": "Europe/London"})
                for f in sorted(data.get("response", []), key=lambda x: x["fixture"]["date"]):
                    fid = f["fixture"]["id"]
                    if fid in seen: continue
                    fd = datetime.fromisoformat(f["fixture"]["date"].replace("Z","+00:00")).date()
                    if fd < today or fd > cutoff: continue
                    status = f["fixture"]["status"]["short"]
                    if status in ("1H","HT","2H","ET","P","FT","AET","PEN"): continue
                    hf = get_team_form(f["teams"]["home"]["id"], lid)
                    af = get_team_form(f["teams"]["away"]["id"], lid)
                    h_rating = hf.get("form_rating", 1.5)
                    a_rating = af.get("form_rating", 1.5)
                    h_losses = hf.get("losses", 0)
                    a_losses = af.get("losses", 0)
                    dt = datetime.fromisoformat(f["fixture"]["date"].replace("Z","+00:00"))
                    odds = get_real_odds(fid)
                    ho = odds.get("home", 0)
                    ao = odds.get("away", 0)

                    # Team on bad run (3+ losses) but still short-odds favourite — fade them
                    if h_losses >= 3 and ao >= 2.5 and a_rating >= 1.3:
                        conf = min(75, 50 + h_losses * 5 + int(a_rating * 5))
                        picks.append({
                            "id": fid, "home": f["teams"]["home"]["name"], "away": f["teams"]["away"]["name"],
                            "home_logo": f["teams"]["home"]["logo"], "away_logo": f["teams"]["away"]["logo"],
                            "date": dt.strftime("%d/%m %H:%M"), "home_id": f["teams"]["home"]["id"],
                            "away_id": f["teams"]["away"]["id"], "league_id": lid,
                            "bet": f"{f['teams']['away']['name']} Win",
                            "market_type": "1X2", "odds": round(ao, 2),
                            "odds_home": ho, "odds_draw": odds.get("draw",0), "odds_away": ao,
                            "odds_available": odds.get("available", False),
                            "confidence": conf,
                            "reasoning": f"⚠️ Form crash pick — {f['teams']['home']['name']} on {h_losses}-loss run. {f['teams']['away']['name']} priced at {ao:.2f} — value against struggling side"
                        })
                    elif a_losses >= 3 and ho >= 2.5 and h_rating >= 1.3:
                        conf = min(75, 50 + a_losses * 5 + int(h_rating * 5))
                        picks.append({
                            "id": fid, "home": f["teams"]["home"]["name"], "away": f["teams"]["away"]["name"],
                            "home_logo": f["teams"]["home"]["logo"], "away_logo": f["teams"]["away"]["logo"],
                            "date": dt.strftime("%d/%m %H:%M"), "home_id": f["teams"]["home"]["id"],
                            "away_id": f["teams"]["away"]["id"], "league_id": lid,
                            "bet": f"{f['teams']['home']['name']} Win",
                            "market_type": "1X2", "odds": round(ho, 2),
                            "odds_home": ho, "odds_draw": odds.get("draw",0), "odds_away": ao,
                            "odds_available": odds.get("available", False),
                            "confidence": conf,
                            "reasoning": f"⚠️ Form crash pick — {f['teams']['away']['name']} on {a_losses}-loss run. {f['teams']['home']['name']} at {ho:.2f} is value"
                        })
            except Exception:
                pass
            return picks

        with ThreadPoolExecutor(max_workers=10) as ex:
            loop = asyncio.get_event_loop()
            results = await asyncio.gather(*[loop.run_in_executor(ex, fetch_form_crash, lc) for lc in leagues])

        for league_picks in results:
            for pick in league_picks:
                if pick["id"] not in seen:
                    seen.add(pick["id"])
                    all_picks.append(pick)

        all_picks.sort(key=lambda x: x["confidence"], reverse=True)
        picks = all_picks[:request.selections]
        if not picks:
            return {"message": "No form crash picks found.", "total_selections": 0, "total_odds": 0, "confidence": 0, "selections": []}

        total_odds = 1.0
        for p in picks: total_odds *= p.get("odds", 2.0)
        avg_conf = sum(p["confidence"] for p in picks) / len(picks)
        return {"message": "Form Crash Acca — Fading Out-of-Form Favourites", "total_selections": len(picks),
                "total_odds": round(total_odds, 2), "confidence": round(avg_conf), "market": "form_crash", "selections": picks}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/generate-table-rank-acca")
async def generate_table_rank_acca(request: AccaRequest):
    """Table rank acca — top-half vs bottom-half clashes, back the table strength."""
    try:
        leagues = request.leagues if request.leagues else ["PL","ELC","BL1","SA","FL1","PD"]
        today = datetime.now().date()
        cutoff = today if request.today_only else today + timedelta(days=3)
        all_picks = []
        seen = set()

        def fetch_standings_and_fixtures(lc: str) -> list:
            lid = LEAGUE_IDS.get(lc)
            if not lid: return []
            picks = []
            try:
                # Get standings to know positions
                st_data = api_get("standings", {"league": lid, "season": get_season()})
                standings = {}
                try:
                    resp = st_data.get("response", [])
                    if not resp: return []
                    raw_table = resp[0].get("league", {}).get("standings", [])
                    table = raw_table[0] if raw_table else []
                    if not table: return []
                    total_teams = len(table)
                    for entry in table:
                        standings[entry["team"]["id"]] = {
                            "rank": entry["rank"],
                            "points": entry["points"],
                            "total": total_teams
                        }
                except (IndexError, KeyError):
                    return []

                data = api_get("fixtures", {"league": lid, "season": get_season(), "next": 10, "timezone": "Europe/London"})
                for f in sorted(data.get("response", []), key=lambda x: x["fixture"]["date"]):
                    fid = f["fixture"]["id"]
                    if fid in seen: continue
                    fd = datetime.fromisoformat(f["fixture"]["date"].replace("Z","+00:00")).date()
                    if fd < today or fd > cutoff: continue
                    status = f["fixture"]["status"]["short"]
                    if status in ("1H","HT","2H","ET","P","FT","AET","PEN"): continue

                    hid = f["teams"]["home"]["id"]
                    aid = f["teams"]["away"]["id"]
                    h_st = standings.get(hid, {})
                    a_st = standings.get(aid, {})
                    if not h_st or not a_st: continue

                    h_rank = h_st["rank"]
                    a_rank = a_st["rank"]
                    total  = h_st.get("total", 20)
                    midpoint = total // 2

                    # Clear top vs bottom clash — min 6 position gap
                    rank_gap = abs(h_rank - a_rank)
                    if rank_gap < 6: continue

                    dt = datetime.fromisoformat(f["fixture"]["date"].replace("Z","+00:00"))
                    odds = get_real_odds(fid)
                    ho = odds.get("home", 0)
                    ao = odds.get("away", 0)

                    if h_rank < a_rank and h_rank <= midpoint:
                        conf = min(84, 58 + rank_gap * 2)
                        picks.append({
                            "id": fid, "home": f["teams"]["home"]["name"], "away": f["teams"]["away"]["name"],
                            "home_logo": f["teams"]["home"]["logo"], "away_logo": f["teams"]["away"]["logo"],
                            "date": dt.strftime("%d/%m %H:%M"), "home_id": hid, "away_id": aid, "league_id": lid,
                            "bet": f"{f['teams']['home']['name']} Win",
                            "market_type": "1X2", "odds": round(ho, 2) if ho else 1.8,
                            "odds_home": ho, "odds_draw": odds.get("draw",0), "odds_away": ao,
                            "odds_available": odds.get("available", False),
                            "confidence": conf,
                            "reasoning": f"📊 Table strength — Rank {h_rank} vs Rank {a_rank} ({rank_gap} places apart). Home side significantly higher in table."
                        })
                    elif a_rank < h_rank and a_rank <= midpoint:
                        conf = min(82, 56 + rank_gap * 2)
                        picks.append({
                            "id": fid, "home": f["teams"]["home"]["name"], "away": f["teams"]["away"]["name"],
                            "home_logo": f["teams"]["home"]["logo"], "away_logo": f["teams"]["away"]["logo"],
                            "date": dt.strftime("%d/%m %H:%M"), "home_id": hid, "away_id": aid, "league_id": lid,
                            "bet": f"{f['teams']['away']['name']} Win",
                            "market_type": "1X2", "odds": round(ao, 2) if ao else 2.1,
                            "odds_home": ho, "odds_draw": odds.get("draw",0), "odds_away": ao,
                            "odds_available": odds.get("available", False),
                            "confidence": conf,
                            "reasoning": f"📊 Table strength — Away side Rank {a_rank} vs home Rank {h_rank} ({rank_gap} places ahead). Away quality showing."
                        })
            except Exception as e:
                logger.error(f"Table rank error {lc}: {e}")
            return picks

        with ThreadPoolExecutor(max_workers=8) as ex:
            loop = asyncio.get_event_loop()
            results = await asyncio.gather(*[loop.run_in_executor(ex, fetch_standings_and_fixtures, lc) for lc in leagues])

        for league_picks in results:
            for pick in league_picks:
                if pick["id"] not in seen:
                    seen.add(pick["id"])
                    all_picks.append(pick)

        all_picks.sort(key=lambda x: x["confidence"], reverse=True)
        picks = all_picks[:request.selections]
        if not picks:
            return {"message": "No table rank picks found.", "total_selections": 0, "total_odds": 0, "confidence": 0, "selections": []}

        total_odds = 1.0
        for p in picks: total_odds *= p.get("odds", 1.9)
        avg_conf = sum(p["confidence"] for p in picks) / len(picks)
        return {"message": "Table Rank Acca — Backing League Position Quality", "total_selections": len(picks),
                "total_odds": round(total_odds, 2), "confidence": round(avg_conf), "market": "table_rank", "selections": picks}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/generate-fhg-acca")
async def generate_fhg_acca(request: AccaRequest):
    """First Half Goals acca — teams with high first-half scoring rates."""
    try:
        leagues = request.leagues if request.leagues else ["BL1","ELC","PL","SA","FL1"]
        today = datetime.now().date()
        cutoff = today + timedelta(days=3)
        all_picks = []
        seen = set()
        random.shuffle(leagues)

        def fetch_fhg(lc: str) -> list:
            lid = LEAGUE_IDS.get(lc)
            if not lid: return []
            picks = []
            try:
                data = api_get("fixtures", {"league": lid, "season": get_season(), "next": 10, "timezone": "Europe/London"})
                for f in sorted(data.get("response", []), key=lambda x: x["fixture"]["date"]):
                    fid = f["fixture"]["id"]
                    if fid in seen: continue
                    fd = datetime.fromisoformat(f["fixture"]["date"].replace("Z","+00:00")).date()
                    if fd < today or fd > cutoff: continue
                    status = f["fixture"]["status"]["short"]
                    if status in ("1H","HT","2H","ET","P","FT","AET","PEN"): continue
                    hf = get_team_form(f["teams"]["home"]["id"], lid)
                    af = get_team_form(f["teams"]["away"]["id"], lid)
                    # Use over25% as proxy for high-scoring / early goal tendency
                    h_o25 = hf.get("over25_pct", 0)
                    a_o25 = af.get("over25_pct", 0)
                    h_gf  = hf.get("gf_avg", 0)
                    a_gf  = af.get("gf_avg", 0)
                    combined_goals = round(h_gf + a_gf, 2)
                    avg_o25 = round((h_o25 + a_o25) / 2)
                    if avg_o25 < 55 or combined_goals < 2.3: continue
                    dt = datetime.fromisoformat(f["fixture"]["date"].replace("Z","+00:00"))
                    odds = get_real_odds(fid)
                    conf = min(85, 52 + int(avg_o25 * 0.4) + int(combined_goals * 5))
                    picks.append({
                        "id": fid, "home": f["teams"]["home"]["name"], "away": f["teams"]["away"]["name"],
                        "home_logo": f["teams"]["home"]["logo"], "away_logo": f["teams"]["away"]["logo"],
                        "date": dt.strftime("%d/%m %H:%M"), "home_id": f["teams"]["home"]["id"],
                        "away_id": f["teams"]["away"]["id"], "league_id": lid,
                        "bet": "Over 2.5 Goals (FHG)",
                        "market_type": "FHG",
                        "odds": odds.get("over25", 1.85),
                        "odds_home": odds.get("home", 0), "odds_draw": odds.get("draw",0), "odds_away": odds.get("away",0),
                        "odds_available": odds.get("available", False),
                        "confidence": conf,
                        "reasoning": f"⚡ First-half goals pick — combined avg {combined_goals:.1f} goals/game. {f['teams']['home']['name']} O2.5 {h_o25}% | {f['teams']['away']['name']} O2.5 {a_o25}%"
                    })
            except Exception:
                pass
            return picks

        with ThreadPoolExecutor(max_workers=10) as ex:
            loop = asyncio.get_event_loop()
            results = await asyncio.gather(*[loop.run_in_executor(ex, fetch_fhg, lc) for lc in leagues])

        for league_picks in results:
            for pick in league_picks:
                if pick["id"] not in seen:
                    seen.add(pick["id"])
                    all_picks.append(pick)

        all_picks.sort(key=lambda x: x["confidence"], reverse=True)
        picks = all_picks[:request.selections]
        if not picks:
            return {"message": "No FHG picks found.", "total_selections": 0, "total_odds": 0, "confidence": 0, "selections": []}

        total_odds = 1.0
        for p in picks: total_odds *= p.get("odds", 1.85)
        avg_conf = sum(p["confidence"] for p in picks) / len(picks)
        return {"message": "First Half Goals Acca", "total_selections": len(picks),
                "total_odds": round(total_odds, 2), "confidence": round(avg_conf), "market": "fhg", "selections": picks}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/generate-value-acca")
async def generate_value_acca(request: AccaRequest):
    """Value acca — out-of-form teams, higher odds picks where market may be overpricing.
    Looks for teams on a bad run vs strong opposition where the longer odds offer value."""
    try:
        leagues = request.leagues if request.leagues else ["PL","ELC","BL1","SA","FL1","PD","CL"]
        today = datetime.now().date()
        cutoff = today + timedelta(days=3)
        all_picks = []
        seen = set()
        random.shuffle(leagues)

        def fetch_value(lc: str) -> list:
            lid = LEAGUE_IDS.get(lc)
            if not lid: return []
            picks = []
            try:
                data = api_get("fixtures", {"league": lid, "season": get_season(), "next": 10, "timezone": "Europe/London"})
                for f in sorted(data.get("response", []), key=lambda x: x["fixture"]["date"]):
                    fid = f["fixture"]["id"]
                    if fid in seen: continue
                    fd = datetime.fromisoformat(f["fixture"]["date"].replace("Z","+00:00")).date()
                    if fd < today or fd > cutoff: continue
                    status = f["fixture"]["status"]["short"]
                    if status in ("1H","HT","2H","ET","P","FT","AET","PEN"): continue

                    hf = get_team_form(f["teams"]["home"]["id"], lid)
                    af = get_team_form(f["teams"]["away"]["id"], lid)
                    odds = get_real_odds(fid)
                    ho = odds.get("home", 0)
                    aw = odds.get("away", 0)
                    if not odds.get("available") or ho < 1.05 or aw < 1.05: continue

                    h_wr = hf.get("wins", 0) / max(hf.get("games", 1), 1)
                    a_wr = af.get("wins", 0) / max(af.get("games", 1), 1)
                    h_form = hf.get("form_rating", 1.5)
                    a_form = af.get("form_rating", 1.5)

                    # VALUE pick logic:
                    # Strong away team (good form) at big away odds = value
                    # Out-of-form home team priced as favourite = fade them
                    bet = None; pick_odds = 0; reasoning = ""; conf = 60

                    # Case 1: Away team in better form but underdog price
                    if a_form > h_form and aw >= 2.20 and a_wr >= 0.40:
                        form_gap = round(a_form - h_form, 2)
                        bet = f"{f['teams']['away']['name']} Win"
                        pick_odds = aw
                        conf = min(76, 52 + int(form_gap * 12) + int(a_wr * 15))
                        reasoning = f"💎 Value: {f['teams']['away']['name']} in better form ({a_wr*100:.0f}% win rate) @ {aw} — market undervalues away strength"

                    # Case 2: Home team on bad run but priced as big favourite — fade
                    elif h_form < 1.2 and h_wr < 0.30 and ho <= 1.55 and a_form >= 1.6:
                        bet = f"{f['teams']['away']['name']} Win or Draw"
                        pick_odds = round(aw * 0.65 + odds.get("draw", 3.0) * 0.35, 2)
                        pick_odds = max(1.50, min(pick_odds, aw))
                        conf = min(72, 50 + int((1 - h_wr) * 20))
                        reasoning = f"💎 Value fade: {f['teams']['home']['name']} on bad run ({h_wr*100:.0f}% wins) but priced @ {ho}"

                    # Case 3: Both teams in moderate form — BTTS value at decent odds
                    elif (hf.get("btts_pct",0) >= 60 and af.get("btts_pct",0) >= 60 and
                          odds.get("btts", 0) >= 1.75):
                        bet = "Both Teams to Score"
                        pick_odds = odds.get("btts", 1.80)
                        conf = min(74, 50 + int((hf.get("btts_pct",0) + af.get("btts_pct",0)) / 5))
                        reasoning = f"💎 BTTS value @ {pick_odds} — {f['teams']['home']['name']} {hf.get('btts_pct',0)}% · {f['teams']['away']['name']} {af.get('btts_pct',0)}%"

                    if not bet or pick_odds < 1.50: continue

                    dt = datetime.fromisoformat(f["fixture"]["date"].replace("Z","+00:00"))
                    picks.append({
                        "id": fid, "home": f["teams"]["home"]["name"], "away": f["teams"]["away"]["name"],
                        "home_logo": f["teams"]["home"]["logo"], "away_logo": f["teams"]["away"]["logo"],
                        "date": dt.strftime("%d/%m %H:%M"), "home_id": f["teams"]["home"]["id"],
                        "away_id": f["teams"]["away"]["id"], "league_id": lid,
                        "bet": bet, "market_type": "VALUE",
                        "odds": round(pick_odds, 2),
                        "odds_home": ho, "odds_draw": odds.get("draw",0), "odds_away": aw,
                        "odds_available": True,
                        "confidence": conf,
                        "reasoning": reasoning
                    })
            except Exception:
                pass
            return picks

        with ThreadPoolExecutor(max_workers=10) as ex:
            loop = asyncio.get_event_loop()
            results = await asyncio.gather(*[loop.run_in_executor(ex, fetch_value, lc) for lc in leagues])

        for league_picks in results:
            for pick in league_picks:
                if pick["id"] not in seen:
                    seen.add(pick["id"])
                    all_picks.append(pick)

        all_picks.sort(key=lambda x: x["confidence"], reverse=True)
        picks = all_picks[:request.selections]
        if not picks:
            return {"message": "No value picks found today.", "total_selections": 0, "total_odds": 0, "confidence": 0, "selections": []}

        total_odds = 1.0
        for p in picks: total_odds *= p.get("odds", 2.0)
        avg_conf = sum(p["confidence"] for p in picks) / len(picks)
        return {"message": "Value Acca — Out of Form / Market Edge Picks", "total_selections": len(picks),
                "total_odds": round(total_odds, 2), "confidence": round(avg_conf), "market": "value", "selections": picks}
    except Exception as e:
        raise HTTPException(500, str(e))


async def chat(request: dict):
    msg = request.get("message", "").lower()
    if not msg:
        return {"response": "Ask me anything about football or betting! ⚽"}

    # Comprehensive football AI responses
    responses = {
        ("xg", "expected goals", "expected"): "📊 **Expected Goals (xG)** measures the quality of scoring chances, not just quantity. An xG of 1.5 means based on shot quality, a team *should* score 1.5 goals. Combined xG > 2.5 = Over 2.5 Goals bet. Both teams xG > 1.0 = BTTS. The xG Acca tab uses this data to find value bets professionals use daily.",
        ("btts", "both teams", "both score"): "🎯 **Both Teams to Score (BTTS)** — best when both sides have weak defences AND strong attacks. Look for teams with GA > 1.0 per game AND GF > 1.0. Bundesliga and Championship are goldmines for BTTS. The AI tracks historical BTTS% per team to find the best spots.",
        ("acca", "accumulator", "parlay", "treble"): "🎰 **Accumulators** multiply odds — a 5-fold at 2.0 per leg = 32.0 total! The AI picks based on form, xG, and confidence scores. Use Balanced risk (60%+ confidence per pick) for best long-term ROI. A 5-fold winning once in 5 attempts is profit at value odds.",
        ("over", "goals", "2.5", "3.5", "under"): "⚽ **Goals markets** — Over 2.5 is the most popular. Look for combined xG > 2.8, both teams averaging 1.5+ goals, and poor defensive records. Bundesliga averages 3.1 goals/game — one of the best leagues for overs. Under 2.5 works in Serie A, La Liga cup games and derbies.",
        ("form", "recent", "performance", "last 5"): "📈 **Form analysis** is key! The AI weighs last 10 games — wins (×3), draws (×1). Form rating 3.0 = perfect, 0 = no wins. Home advantage adds 0.4 to the rating. Click Analyse Match on any fixture for full form breakdown including goals scored, conceded, and recent results.",
        ("live", "inplay", "in-play", "minute"): "🔴 **Live betting** — the Live tab shows minute-by-minute scores, match events (goals, cards, subs), and live stats. Click any live match for the detailed timeline. Stats like possession and shots can signal momentum shifts — a team with 12 shots but 0 goals may be due!",
        ("value", "value bet", "kelly"): "💰 **Value betting** is when true probability > implied probability from odds. If you think a team has 50% chance to win but odds imply 40%, that's value. Kelly Criterion = (bp - q) / b where b=decimal odds-1, p=your probability, q=1-p. Never bet more than 5% of bankroll on a single bet.",
        ("odds", "bookmaker", "best odds"): "📊 **Odds comparison** — the Best Odds tab in Analyse Match compares across multiple bookmakers. Even 0.1 extra odds on a 5-fold can mean 5-10% more return. Bet365, William Hill, Betfair, Paddy Power all covered. Always take the best odds available!",
        ("strategy", "system", "bankroll", "staking"): "🧠 **Betting strategy**: 1) Flat stake 1-2% of bankroll per bet 2) Only bet where AI confidence ≥65% 3) Track everything in Saved Accas 4) Review monthly P&L 5) Never chase losses. Value + patience + discipline beats any system long-term.",
        ("tiki taka", "pressing", "formation", "tactic", "gegenpress"): "⚽ **Football tactics**: Tiki-taka (Barcelona) = short passes, possession retention. Gegenpressing (Klopp/Liverpool) = immediate press after losing ball. 4-3-3 = width and pressing. 3-5-2 = midfield dominance with wing-backs. Tactical setups affect match stats — defensively solid teams show lower xG allowed.",
        ("var", "offside", "handball", "rules"): "📏 **VAR** checks goals, penalties, red cards, and mistaken identity. Offside uses the furthest point of attacker vs last defender. Handball = arm/hand above shoulder height is usually penalised. Understanding rules helps — VAR reversals mid-game affect live betting significantly.",
        ("messi", "ronaldo", "haaland", "bellingham", "salah"): "⭐ **Modern greats**: Messi (Inter Miami) — highest xG efficiency ever. Ronaldo (Al-Nassr) — movement and finishing. Haaland (Man City) — 36 PL goals in one season, insane xG conversion. Bellingham (Real Madrid) — pressing + goal contributions. Salah — consistent 20+ goal seasons. Check player stats in Analyse Match!",
        ("champions league", "ucl", "europa"): "🏆 **Champions League** — covered fully! UCL games can be tactical (1st leg away caution), affecting goals markets. Home teams average higher xG in UCL group stages. The AI adjusts for European competition patterns. Check the group stage vs knockout round form separately.",
        ("premier league", "pl", "epl"): "ENG **Premier League** — most competitive globally. Man City, Arsenal, Liverpool, Chelsea dominate xG stats. Watch for home advantage — PL home win rate ~45%. Midweek fixtures after European games hit fatigue. AI tracks all 20 PL teams across all markets.",
        ("bundesliga", "germany"): "🇩🇪 **Bundesliga** — highest goals/game average in top 5 leagues (3.1). Perfect for Over 2.5 and BTTS accas. Dortmund, Leverkusen, Bayern all attack-heavy. Especially good for HT goals given aggressive openers.",
        ("tip", "tips", "advice", "help me"): "💡 **Top 5 AI tips**: 1) Generate AI Acca (Balanced risk) for data-backed picks. 2) Cross-reference with xG Acca for confirmation. 3) Use Analyse Match → Best Odds to maximise value. 4) Save all bets and track P&L. 5) Filter by league you know well. Confidence score ≥70% = higher quality picks.",
        ("ht", "half time", "halftime", "first half"): "⏱️ **HT Acca** uses first-half patterns — teams that consistently lead at HT, score in first 20 mins, and have high FH goals averages. Bundesliga and Championship are strong HT markets. The AI analyses 10 games of HT history per team.",
        ("save", "saved", "track", "history"): "💾 **Saved Accas** — save any bet slip, name it, set stake. Mark Won/Lost after results. The dashboard shows win rate, P&L, ROI. Review monthly to identify which markets/leagues work best for you. All data stored locally + synced.",
        ("hello", "hi", "hey", "start", "what can"): "👋 **Welcome to AccaGenius!** I'm your AI betting co-pilot. I can help with:\n• AI Acca generation (form + xG based)\n• Live scores & minute-by-minute events\n• Market explanations & strategies\n• Team analysis & predictions\n• xG deep dives\n\nWhat would you like to explore? ⚽",
        ("thank", "thanks", "cheers", "brilliant", "great"): "🙏 You're welcome! Good luck with your accas — may the odds be in your favour! 🍀⚽",
    }

    for keywords, response in responses.items():
        if any(kw in msg for kw in keywords):
            return {"response": response}

    return {"response": f"Great question! The AI Acca Generator analyses real form + xG data across 17 leagues. Try 'Analyse Match' on any fixture for H2H, form stats, predictions and best odds. What specific match or market are you interested in? ⚽"}


# =========================
# SAVED ACCAS
# =========================

@app.get("/saved-accas")
async def get_saved_accas():
    return {"accas": saved_accas_store}

@app.post("/saved-accas")
@app.post("/save-acca")
async def save_acca(request: dict):
    acca = {
        "id": len(saved_accas_store) + int(datetime.now().timestamp()),
        "name": request.get("name", "My Acca"),
        "selections": request.get("selections", []),
        "total_odds": request.get("total_odds", 0),
        "stake": request.get("stake", 10),
        "created_at": datetime.now().isoformat(),
        "result": "pending"
    }
    saved_accas_store.append(acca)
    return {"message": "Saved!", "id": acca["id"], "acca": acca}

@app.delete("/saved-accas/{acca_id}")
async def delete_acca(acca_id: int):
    global saved_accas_store
    saved_accas_store = [a for a in saved_accas_store if a.get("id") != acca_id]
    return {"message": "Deleted"}

@app.patch("/saved-accas/{acca_id}/result")
async def update_result(acca_id: int, request: dict):
    for a in saved_accas_store:
        if a.get("id") == acca_id:
            a["result"] = request.get("result", "pending")
            return {"message": "Updated", "acca": a}
    raise HTTPException(404, "Not found")
