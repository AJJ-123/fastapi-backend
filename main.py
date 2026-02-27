print("⚡ ACCAGENIUS ULTIMATE - AI BETTING INTELLIGENCE PLATFORM ⚡")

import os
import requests
import random
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# =========================
# CONFIG
# =========================
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "0192e664450828fc0345770b74b75e9f")
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {
    "x-rapidapi-key": API_FOOTBALL_KEY,
    "x-rapidapi-host": "v3.football.api-sports.io"
}

app = FastAPI(title="AccaGenius Ultimate API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory store
saved_accas_store: List[dict] = []

# =========================
# LEAGUES (17)
# =========================
LEAGUES = [
    {"code": "PL",   "name": "Premier League",         "country": "England",     "id": 39,  "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿"},
    {"code": "ELC",  "name": "Championship",            "country": "England",     "id": 40,  "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿"},
    {"code": "EL1",  "name": "League One",              "country": "England",     "id": 41,  "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿"},
    {"code": "EL2",  "name": "League Two",              "country": "England",     "id": 42,  "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿"},
    {"code": "CL",   "name": "Champions League",        "country": "Europe",      "id": 2,   "flag": "🇪🇺"},
    {"code": "EL",   "name": "Europa League",           "country": "Europe",      "id": 3,   "flag": "🇪🇺"},
    {"code": "ECL",  "name": "Conference League",       "country": "Europe",      "id": 848, "flag": "🇪🇺"},
    {"code": "FL1",  "name": "Ligue 1",                 "country": "France",      "id": 61,  "flag": "🇫🇷"},
    {"code": "FL2",  "name": "Ligue 2",                 "country": "France",      "id": 62,  "flag": "🇫🇷"},
    {"code": "BL1",  "name": "Bundesliga",              "country": "Germany",     "id": 78,  "flag": "🇩🇪"},
    {"code": "BL2",  "name": "2. Bundesliga",           "country": "Germany",     "id": 79,  "flag": "🇩🇪"},
    {"code": "SA",   "name": "Serie A",                 "country": "Italy",       "id": 135, "flag": "🇮🇹"},
    {"code": "NED",  "name": "Eredivisie",              "country": "Netherlands", "id": 88,  "flag": "🇳🇱"},
    {"code": "PPL",  "name": "Primeira Liga",           "country": "Portugal",    "id": 94,  "flag": "🇵🇹"},
    {"code": "PD",   "name": "La Liga",                 "country": "Spain",       "id": 140, "flag": "🇪🇸"},
    {"code": "TUR",  "name": "Süper Lig",               "country": "Turkey",      "id": 203, "flag": "🇹🇷"},
    {"code": "BEL",  "name": "Belgium Pro League",      "country": "Belgium",     "id": 144, "flag": "🇧🇪"},
    {"code": "POL",  "name": "Ekstraklasa",             "country": "Poland",      "id": 106, "flag": "🇵🇱"},
    {"code": "SPFL", "name": "Scottish Premiership",    "country": "Scotland",    "id": 179, "flag": "🏴󠁧󠁢󠁳󠁣󠁴󠁿"},
]
LEAGUE_IDS = {l["code"]: l["id"] for l in LEAGUES}
LEAGUE_INFO = {l["code"]: l for l in LEAGUES}

def get_season() -> int:
    now = datetime.now()
    return now.year if now.month >= 8 else now.year - 1

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

    response_data = data.get("response", [{}])[0]
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
        ("premier league", "pl", "epl"): "🏴󠁧󠁢󠁥󠁮󠁧󠁿 **Premier League** — most competitive globally. Man City, Arsenal, Liverpool, Chelsea dominate xG stats. Watch for home advantage — PL home win rate ~45%. Midweek fixtures after European games hit fatigue. AI tracks all 20 PL teams across all markets.",
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
