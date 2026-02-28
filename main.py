print("⚡ ACCAGENIUS ULTIMATE - AI BETTING INTELLIGENCE PLATFORM ⚡")

import os
import requests
import random
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
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

# Telegram config — set these in Railway Variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "")  # e.g. @AccaGeniusFree or -100xxxxxxxxxx

# Alert thresholds
ALERT_WIN_PCT   = 65    # Win % must be >= this
ALERT_XG_GAP   = 0.4   # xG gap between teams must be >= this
ALERT_MINUTE_MIN = 20  # Don't alert before 20th minute (too early)
ALERT_MINUTE_MAX = 75  # Don't alert after 75th minute (too late for value)

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

# Track which fixtures we've already alerted — resets each day
alerted_fixtures: set = set()
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
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logger.warning("Telegram not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHANNEL_ID,
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
    """Build the Telegram alert message with all info."""
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

    # Green circle = in the green = all conditions met
    msg = (
        f"🟢 <b>ACCAGENIUS IN-PLAY ALERT</b> 🟢\n"
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
        f"🤖 AccaGenius AI | Live Intelligence"
    )
    return msg


async def live_alert_scanner():
    """Background task — scans live matches every 5 minutes and fires Telegram alerts."""
    global alerted_fixtures, last_alert_reset
    logger.info("🤖 Telegram alert scanner started")

    while True:
        try:
            # Reset alerted fixtures each new day
            today = datetime.now().strftime("%Y-%m-%d")
            if today != last_alert_reset:
                alerted_fixtures = set()
                last_alert_reset = today
                logger.info("Alert tracker reset for new day")

            if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
                await asyncio.sleep(300)
                continue

            # Fetch all live matches
            live_data = api_get("fixtures", {"live": "all", "timezone": "Europe/London"})
            our_league_ids = {l["id"] for l in LEAGUES}
            live_matches = []
            for f in live_data.get("response", []):
                try:
                    if f["league"]["id"] not in our_league_ids:
                        continue
                    minute = f["fixture"]["status"].get("elapsed") or 0
                    live_matches.append({
                        "id":          f["fixture"]["id"],
                        "home":        f["teams"]["home"]["name"],
                        "away":        f["teams"]["away"]["name"],
                        "home_score":  f["goals"]["home"] or 0,
                        "away_score":  f["goals"]["away"] or 0,
                        "minute":      minute,
                        "league":      f["league"]["name"],
                    })
                except Exception:
                    pass

            logger.info(f"Scanner: {len(live_matches)} live matches found")

            alerts_sent = 0
            for m in live_matches:
                fid = m["id"]
                # Skip if already alerted this match today
                if fid in alerted_fixtures:
                    continue

                try:
                    alert = score_live_match(
                        fid, m["home"], m["away"],
                        m["home_score"], m["away_score"],
                        m["minute"], m["league"]
                    )
                    if alert:
                        msg = build_telegram_message(alert)
                        if send_telegram(msg):
                            alerted_fixtures.add(fid)
                            alerts_sent += 1
                            logger.info(f"Alert sent: {m['home']} vs {m['away']} min {m['minute']}")
                        # Small delay between alerts to avoid Telegram rate limits
                        await asyncio.sleep(2)
                except Exception as e:
                    logger.error(f"Error scoring {fid}: {e}")

            logger.info(f"Scanner cycle done — {alerts_sent} alerts sent")

        except Exception as e:
            logger.error(f"Scanner error: {e}")

        # Wait 5 minutes before next scan
        await asyncio.sleep(300)


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

def api_get(endpoint: str, params: dict, timeout: int = 10) -> dict:
    """Safe API call wrapper"""
    try:
        r = requests.get(f"{BASE_URL}/{endpoint}", headers=HEADERS, params=params, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        return {}
    except Exception:
        return {}


def get_team_form(team_id: int, league_id: int, last: int = 10) -> dict:
    """Get detailed team form with extended stats"""
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

    return {
        "games": games, "wins": wins, "draws": draws, "losses": losses,
        "gf": gf, "ga": ga, "gf_avg": gf_avg, "ga_avg": ga_avg,
        "form_rating": round(form_rating, 2),
        "btts_pct": btts_pct, "over25_pct": over25_pct,
        "cs_pct": cs_pct, "ht_lead_pct": ht_lead_pct,
        "recent_fixtures": recent
    }


def get_real_odds(fixture_id: int) -> dict:
    """Fetch best odds from API across all available bookmakers"""
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
    return {**best, "available": True, "all_bookmakers": all_bookmakers}


def get_quick_odds(fixture_id: int) -> dict:
    """Fast odds fetch — returns best home/draw/away with bookmaker name"""
    return get_real_odds(fixture_id)


def analyze_and_pick(fixture: dict, home_form: dict, away_form: dict, risk: str, market: str = "winner") -> Optional[dict]:
    """Pick engine — Match Winner market with proper risk-based selection"""
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
        ho = odds.get("home", 0)   # real home odds
        do = odds.get("draw", 0)   # real draw odds
        ao = odds.get("away", 0)   # real away odds

        hr = home_form.get("form_rating", 1.5) + 0.4  # home advantage
        ar = away_form.get("form_rating", 1.5)
        hg = home_form.get("gf_avg", 1.2)
        ag = away_form.get("gf_avg", 1.0)

        base = {
            "id": fid, "home": home, "away": away,
            "home_logo": home_logo, "away_logo": away_logo,
            "date": date_str, "home_id": home_id, "away_id": away_id,
            "league_id": league_id, "market_type": "1X2"
        }

        gap = hr - ar  # positive = home stronger, negative = away stronger

        if risk == "safe":
            # Strong clear favourites only — odds between 1.20 and 1.80, dominant form
            if gap > 1.2 and ho > 1.15 and ho < 1.85:
                conf = min(88, 72 + int(gap * 8))
                return {**base, "bet": f"{home} Win", "odds": round(ho, 2),
                        "confidence": conf,
                        "reasoning": f"{home} strong home form {home_form['wins']}W-{home_form['draws']}D-{home_form['losses']}L, {hg:.1f} goals/game avg"}
            if gap < -1.2 and ao > 1.15 and ao < 1.85:
                conf = min(85, 70 + int(abs(gap) * 8))
                return {**base, "bet": f"{away} Win", "odds": round(ao, 2),
                        "confidence": conf,
                        "reasoning": f"{away} dominant away form {away_form['wins']}W-{away_form['draws']}D-{away_form['losses']}L"}
            return None

        elif risk == "risky":
            # Target upsets and value — look for underdogs and draws with longer odds
            # Upset pick: away team has decent form but is priced as underdog
            if gap < 0.3 and ao >= 2.5 and ar >= 1.4:
                conf = min(72, 52 + int(ar * 10))
                return {**base, "bet": f"{away} Win", "odds": round(ao, 2),
                        "confidence": conf,
                        "reasoning": f"Value upset — {away} {away_form['wins']}W in last 10, priced at {ao:.2f}. Form tighter than odds suggest"}
            # Draw value: evenly matched teams, draw priced 3.0+
            if abs(gap) < 0.5 and do >= 3.0:
                conf = min(68, 48 + int((5.0 - abs(gap)) * 5))
                return {**base, "bet": "Draw", "odds": round(do, 2),
                        "confidence": conf,
                        "reasoning": f"Closely matched — {home} vs {away} form gap only {abs(gap):.1f}. Draw at {do:.2f} is value"}
            # Home underdog — home team priced surprisingly high
            if gap > 0.2 and ho >= 2.8 and hr > ar:
                conf = min(70, 50 + int(gap * 8))
                return {**base, "bet": f"{home} Win", "odds": round(ho, 2),
                        "confidence": conf,
                        "reasoning": f"{home} home form ({home_form['wins']}W) underpriced at {ho:.2f} — potential big-odds upset"}
            # Away upset: away priced 3.5+ but form suggests competitive
            if ao >= 3.5 and away_form['wins'] >= 3:
                conf = min(65, 45 + away_form['wins'] * 4)
                return {**base, "bet": f"{away} Win", "odds": round(ao, 2),
                        "confidence": conf,
                        "reasoning": f"{away} {away_form['wins']} wins in last 10 — priced generously at {ao:.2f}"}
            return None

        else:  # balanced
            # Balanced: clear form edge, reasonable odds (1.5–2.8)
            if gap > 0.8 and ho >= 1.40:
                conf = min(84, 65 + int(gap * 10))
                return {**base, "bet": f"{home} Win", "odds": round(ho, 2),
                        "confidence": conf,
                        "reasoning": f"{home} {home_form['wins']}W-{home_form['draws']}D-{home_form['losses']}L last 10 | avg {hg:.1f} goals/game"}
            if gap < -0.8 and ao >= 1.40:
                conf = min(82, 63 + int(abs(gap) * 10))
                return {**base, "bet": f"{away} Win", "odds": round(ao, 2),
                        "confidence": conf,
                        "reasoning": f"{away} stronger — {away_form['wins']}W-{away_form['draws']}D-{away_form['losses']}L, avg {ag:.1f} goals/game"}
            # Draw value in close match
            if abs(gap) < 0.4 and do >= 2.8:
                conf = min(72, 55 + int((3.0 - abs(gap)) * 8))
                return {**base, "bet": "Draw", "odds": round(do, 2),
                        "confidence": conf,
                        "reasoning": f"Evenly matched — form gap just {abs(gap):.1f}. Draw at {do:.2f} offers value"}
            return None

    except Exception as e:
        print(f"Pick error: {e}")
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

@app.get("/")
@app.head("/")
async def root():
    return {"status": "AccaGenius Ultimate API — Live", "version": "5.0"}

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

@app.get("/telegram/status")
async def telegram_status():
    """Check Telegram config status."""
    return {
        "configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID),
        "bot_token_set": bool(TELEGRAM_BOT_TOKEN),
        "channel_set": bool(TELEGRAM_CHANNEL_ID),
        "channel": TELEGRAM_CHANNEL_ID if TELEGRAM_CHANNEL_ID else "not set",
        "alerted_today": len(alerted_fixtures),
        "thresholds": {
            "win_pct": ALERT_WIN_PCT,
            "xg_gap": ALERT_XG_GAP,
            "minute_min": ALERT_MINUTE_MIN,
            "minute_max": ALERT_MINUTE_MAX,
        }
    }

@app.post("/telegram/test")
async def telegram_test():
    """Send a test message to verify Telegram is working."""
    msg = (
        "🟢 <b>ACCAGENIUS TEST ALERT</b> 🟢\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "✅ Your Telegram alerts are working!\n"
        "🤖 AccaGenius AI is now monitoring live matches.\n"
        "You'll receive in-play tips when all 3 conditions are met:\n"
        "  • Win % ≥ 65%\n"
        "  • xG gap ≥ 0.4\n"
        "  • Scoring momentum confirmed\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <i>Bet responsibly. 18+ only.</i>"
    )
    success = send_telegram(msg)
    return {"success": success, "message": "Test sent" if success else "Failed — check TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID in Railway Variables"}

@app.post("/telegram/scan-now")
async def telegram_scan_now():
    """Manually trigger one scan cycle immediately."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        return {"error": "Telegram not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID in Railway Variables."}

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



@app.get("/fixtures/{league_code}")
async def get_fixtures(league_code: str):
    league_id = LEAGUE_IDS.get(league_code.upper())
    if not league_id:
        raise HTTPException(404, "League not found")
    data = api_get("fixtures", {
        "league": league_id, "season": get_season(),
        "next": 20, "timezone": "Europe/London"
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
    return {"league": league_code, "fixtures": by_date}


@app.get("/today")
async def get_today_fixtures():
    """All fixtures for today — parallel fetch, returns partial results on timeout"""
    today_str = datetime.now().strftime("%Y-%m-%d")

    def fetch_league_today(league: dict) -> list:
        try:
            data = api_get("fixtures", {
                "league": league["id"], "date": today_str, "timezone": "Europe/London"
            }, timeout=5)
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
                        "venue": (f["fixture"]["venue"].get("name") or ""),
                        "odds_available": False,
                    })
                except Exception:
                    pass
            return results
        except Exception:
            return []  # Silently skip leagues that timeout

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=19) as executor:
        futures = [loop.run_in_executor(executor, fetch_league_today, lg) for lg in LEAGUES]
        results = await asyncio.gather(*futures, return_exceptions=True)

    all_matches = sorted(
        [m for r in results if isinstance(r, list) for m in r],
        key=lambda x: x["time"]
    )
    return {"matches": all_matches, "count": len(all_matches), "date": today_str}


@app.get("/next-round")
async def get_next_round_fixtures():
    """Next round of fixtures across all leagues — next 3 days with real odds"""
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
    return {"matches": all_matches, "count": len(all_matches)}


@app.get("/live")
async def get_live():
    """Live matches — only from our supported leagues"""
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
    return {"matches": matches, "count": len(matches)}

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
        return {"league": league_code, "standings": standings}
    except Exception as e:
        return {"league": league_code, "standings": [], "message": str(e)}


@app.get("/h2h/{team1_id}/{team2_id}")
async def get_h2h(team1_id: int, team2_id: int):
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
    return {"matches": matches}


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
    """Full odds across multiple bookmakers and markets"""
    data = api_get("odds", {"fixture": fixture_id})
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

    return {
        "bookmakers": bookmakers_data,
        "markets": markets,
        "best_odds": {
            "home": {"bookmaker": best["home"][0], "odds": best["home"][1]},
            "draw": {"bookmaker": best["draw"][0], "odds": best["draw"][1]},
            "away": {"bookmaker": best["away"][0], "odds": best["away"][1]}
        }
    }


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


@app.post("/generate-acca")
async def generate_acca(request: AccaRequest):
    try:
        leagues = request.leagues if request.leagues else ["PL", "ELC", "PD", "BL1", "SA", "FL1", "CL", "EL", "ECL", "TUR", "NED", "PPL", "BEL", "SPFL"]
        today = datetime.now().date()
        cutoff = today if request.today_only else today + timedelta(days=3)
        all_picks = []
        seen = set()
        random.shuffle(leagues)

        def fetch_league_picks(lc: str) -> list:
            lid = LEAGUE_IDS.get(lc)
            if not lid: return []
            picks = []
            try:
                if request.today_only:
                    # Fetch today's specific date — guarantees only today's games
                    data = api_get("fixtures", {"league": lid, "season": get_season(),
                                                "date": today.strftime("%Y-%m-%d"), "timezone": "Europe/London"})
                else:
                    data = api_get("fixtures", {"league": lid, "season": get_season(),
                                                "next": 15, "timezone": "Europe/London"})
                fixtures = sorted(data.get("response", []), key=lambda f: f["fixture"]["date"])
                for f in fixtures:
                    fid = f["fixture"]["id"]
                    fd = datetime.fromisoformat(f["fixture"]["date"].replace("Z", "+00:00")).date()
                    if fd < today or fd > cutoff: continue
                    # Skip already started/finished games
                    status = f["fixture"]["status"]["short"]
                    if status in ("1H","HT","2H","ET","P","FT","AET","PEN"): continue
                    hf = get_team_form(f["teams"]["home"]["id"], lid)
                    af = get_team_form(f["teams"]["away"]["id"], lid)
                    pick = analyze_and_pick(f, hf, af, request.risk, request.market)
                    if pick and pick["confidence"] >= 55:
                        odds = get_real_odds(fid)
                        pick["odds_home"] = odds.get("home", 0)
                        pick["odds_draw"] = odds.get("draw", 0)
                        pick["odds_away"] = odds.get("away", 0)
                        pick["odds_home_bk"] = odds.get("home_bk", "")
                        pick["odds_draw_bk"] = odds.get("draw_bk", "")
                        pick["odds_away_bk"] = odds.get("away_bk", "")
                        pick["odds_available"] = odds.get("available", False)
                        picks.append(pick)
            except Exception:
                pass
            return picks

        # Run all leagues in parallel
        with ThreadPoolExecutor(max_workers=14) as ex:
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
        picks = all_picks[:request.selections]

        if not picks:
            msg = "No picks found for today's fixtures yet — check back closer to kick-off." if request.today_only else "No picks found. Try different leagues or market."
            return {"message": msg, "total_selections": 0, "total_odds": 0, "confidence": 0, "selections": []}

        total_odds = 1.0
        for p in picks: total_odds *= p.get("odds", 1.0)
        avg_conf = sum(p["confidence"] for p in picks) / len(picks)
        label = "Today Only" if request.today_only else "Next 3 Days"

        return {
            "message": f"AI Acca — {request.risk.capitalize()} risk — {label}",
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


@app.post("/chat")
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
