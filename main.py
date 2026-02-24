print("♠ ACCAGENIUS V4 - SMART PRE-GAME STATS + HT ACCA + FULL FOOTBALL AI ♠")

import os
import requests
import random
from datetime import datetime
from typing import Optional, List
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

app = FastAPI(title="AccaGenius V4 API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory saved accas
saved_accas_store: List[dict] = []

LEAGUES = [
    {"code": "PL",  "name": "Premier League",    "id": 39},
    {"code": "ELC", "name": "Championship",       "id": 40},
    {"code": "EL1", "name": "League One",         "id": 41},
    {"code": "EL2", "name": "League Two",         "id": 42},
    {"code": "FL1", "name": "Ligue 1",            "id": 61},
    {"code": "FL2", "name": "Ligue 2",            "id": 62},
    {"code": "BL1", "name": "Bundesliga",         "id": 78},
    {"code": "BL2", "name": "2. Bundesliga",      "id": 79},
    {"code": "SA",  "name": "Serie A",            "id": 135},
    {"code": "NED", "name": "Eredivisie",         "id": 88},
    {"code": "CL",  "name": "Champions League",   "id": 2},
    {"code": "POL", "name": "Ekstraklasa",        "id": 106},
    {"code": "PPL", "name": "Primeira Liga",      "id": 94},
    {"code": "PD",  "name": "La Liga",            "id": 140},
    {"code": "TUR", "name": "Süper Lig",          "id": 203},
    {"code": "BEL", "name": "Belgium Pro League", "id": 144},
    {"code": "DEN", "name": "Denmark Superliga",  "id": 119},
]
LEAGUE_IDS = {l["code"]: l["id"] for l in LEAGUES}

def get_season():
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

# =========================
# DATA FETCHERS
# =========================

def get_last_fixtures(team_id: int, league_id: int, n: int = 10) -> list:
    """Get last N completed fixtures for a team"""
    try:
        r = requests.get(f"{BASE_URL}/fixtures", headers=HEADERS, params={
            "team": team_id, "league": league_id,
            "season": get_season(), "last": n, "timezone": "Europe/London"
        }, timeout=10)
        if r.status_code != 200:
            return []
        return [f for f in r.json().get("response", []) if f["fixture"]["status"]["short"] == "FT"]
    except:
        return []

def get_fixture_stats(fixture_id: int) -> dict:
    """Get stats for a completed fixture"""
    try:
        r = requests.get(f"{BASE_URL}/fixtures/statistics", headers=HEADERS,
                         params={"fixture": fixture_id}, timeout=10)
        if r.status_code != 200:
            return {}
        teams = r.json().get("response", [])
        result = {}
        for team_stats in teams:
            tid = team_stats["team"]["id"]
            stats = {s["type"]: s["value"] for s in team_stats.get("statistics", [])}
            result[tid] = stats
        return result
    except:
        return {}

def get_team_form(team_id: int, league_id: int) -> dict:
    """Basic form: last 5 W/D/L, goals"""
    fixtures = get_last_fixtures(team_id, league_id, 5)
    wins = draws = losses = gf = ga = 0
    for f in fixtures:
        is_home = f["teams"]["home"]["id"] == team_id
        g_for  = (f["goals"]["home"] if is_home else f["goals"]["away"]) or 0
        g_ag   = (f["goals"]["away"] if is_home else f["goals"]["home"]) or 0
        gf += g_for; ga += g_ag
        if g_for > g_ag: wins += 1
        elif g_for == g_ag: draws += 1
        else: losses += 1
    games = wins + draws + losses
    rating = (wins * 3 + draws) / max(games, 1)
    return {"wins": wins, "draws": draws, "losses": losses, "gf": gf, "ga": ga,
            "games": games, "form_rating": rating}

def get_pregame_stats(team_id: int, league_id: int) -> dict:
    """
    Advanced pre-game stats from last 10 fixtures:
    - First half goals scored / conceded
    - First goal scored (did they score first?)
    - Attack in first 20 mins (goals or shots in mins 0-20)
    - BTTS rate, Over 2.5 rate
    - Clean sheet rate
    - HT lead / HT draw / HT behind rates
    """
    fixtures = get_last_fixtures(team_id, league_id, 10)
    if not fixtures:
        return _empty_pregame()

    fh_goals_for = 0
    fh_goals_ag  = 0
    scored_first  = 0
    early_attack  = 0   # scored in first 20 mins
    btts_count    = 0
    over25_count  = 0
    clean_sheets  = 0
    ht_lead       = 0
    ht_draw_count = 0
    ht_behind     = 0
    games         = 0

    for f in fixtures:
        fid = f["fixture"]["id"]
        is_home = f["teams"]["home"]["id"] == team_id

        # Full-time goals
        ft_for = (f["goals"]["home"] if is_home else f["goals"]["away"]) or 0
        ft_ag  = (f["goals"]["away"] if is_home else f["goals"]["home"]) or 0
        total_goals = ft_for + ft_ag

        # HT score from fixture data
        ht_home = (f.get("score", {}).get("halftime", {}) or {}).get("home") or 0
        ht_away = (f.get("score", {}).get("halftime", {}) or {}).get("away") or 0
        ht_for  = ht_home if is_home else ht_away
        ht_ag   = ht_away if is_home else ht_home

        fh_goals_for += ht_for
        fh_goals_ag  += ht_ag

        if ht_for > ht_ag:  ht_lead += 1
        elif ht_for == ht_ag: ht_draw_count += 1
        else: ht_behind += 1

        if total_goals >= 2 and ft_for >= 1 and ft_ag >= 1:
            btts_count += 1
        if total_goals > 2:
            over25_count += 1
        if ft_ag == 0:
            clean_sheets += 1

        # Detect early goals from events
        try:
            r = requests.get(f"{BASE_URL}/fixtures/events", headers=HEADERS,
                             params={"fixture": fid, "team": team_id, "type": "Goal"}, timeout=8)
            if r.status_code == 200:
                events = r.json().get("response", [])
                goal_mins = [e["time"]["elapsed"] for e in events
                             if e["time"]["elapsed"] is not None]
                if goal_mins and min(goal_mins) <= 20:
                    early_attack += 1
                # First goal of match
                all_r = requests.get(f"{BASE_URL}/fixtures/events", headers=HEADERS,
                                     params={"fixture": fid, "type": "Goal"}, timeout=8)
                if all_r.status_code == 200:
                    all_events = sorted(all_r.json().get("response", []),
                                        key=lambda e: e["time"]["elapsed"] or 999)
                    if all_events and all_events[0]["team"]["id"] == team_id:
                        scored_first += 1
        except:
            pass

        games += 1

    g = max(games, 1)
    return {
        "games": games,
        "fh_goals_for_avg": round(fh_goals_for / g, 2),
        "fh_goals_ag_avg":  round(fh_goals_ag  / g, 2),
        "scored_first_pct": round(scored_first  / g * 100),
        "early_attack_pct": round(early_attack  / g * 100),
        "btts_pct":         round(btts_count    / g * 100),
        "over25_pct":       round(over25_count  / g * 100),
        "clean_sheet_pct":  round(clean_sheets  / g * 100),
        "ht_lead_pct":      round(ht_lead       / g * 100),
        "ht_draw_pct":      round(ht_draw_count / g * 100),
        "ht_behind_pct":    round(ht_behind     / g * 100),
        "fh_goals_for_total": fh_goals_for,
        "fh_goals_ag_total":  fh_goals_ag,
    }

def _empty_pregame():
    return {
        "games": 0, "fh_goals_for_avg": 0, "fh_goals_ag_avg": 0,
        "scored_first_pct": 0, "early_attack_pct": 0,
        "btts_pct": 0, "over25_pct": 0, "clean_sheet_pct": 0,
        "ht_lead_pct": 0, "ht_draw_pct": 0, "ht_behind_pct": 0,
        "fh_goals_for_total": 0, "fh_goals_ag_total": 0,
    }

def get_real_odds(fixture_id: int) -> dict:
    try:
        r = requests.get(f"{BASE_URL}/odds", headers=HEADERS,
                         params={"fixture": fixture_id, "bookmaker": 8}, timeout=10)
        if r.status_code != 200:
            return {"home": 2.10, "draw": 3.30, "away": 3.50}
        resp = r.json().get("response", [])
        if not resp:
            return {"home": 2.10, "draw": 3.30, "away": 3.50}
        for bk in resp[0].get("bookmakers", []):
            for bet in bk.get("bets", []):
                if bet["name"] == "Match Winner":
                    v = bet.get("values", [])
                    if len(v) >= 3:
                        return {"home": float(v[0]["odd"]),
                                "draw": float(v[1]["odd"]),
                                "away": float(v[2]["odd"])}
        return {"home": 2.10, "draw": 3.30, "away": 3.50}
    except:
        return {"home": 2.10, "draw": 3.30, "away": 3.50}

# =========================
# SMART PICK ENGINE
# =========================

def smart_pick(fixture: dict, home_form: dict, away_form: dict,
               home_pre: dict, away_pre: dict,
               market: str, risk: str) -> Optional[dict]:
    """
    Market-aware pick using pre-game stats.
    Every decision is backed by real historical data.
    """
    try:
        home  = fixture["teams"]["home"]["name"]
        away  = fixture["teams"]["away"]["name"]
        fid   = fixture["fixture"]["id"]
        home_id = fixture["teams"]["home"]["id"]
        away_id = fixture["teams"]["away"]["id"]
        dt    = datetime.fromisoformat(
            fixture["fixture"]["date"].replace("Z", "+00:00"))
        date_str = dt.strftime("%d/%m %H:%M")

        base = {"id": fid, "home": home, "away": away, "date": date_str,
                "home_id": home_id, "away_id": away_id}

        odds = get_real_odds(fid)

        # ---- MATCH WINNER ----
        if market == "winner":
            hr = home_form["form_rating"] + 0.4  # home advantage
            ar = away_form["form_rating"]
            diff = hr - ar
            if abs(diff) > 0.7:
                if diff > 0:
                    conf = min(85, 60 + int(diff * 14))
                    return {**base, "bet": f"{home} Win",
                            "odds": round(odds["home"], 2),
                            "confidence": conf,
                            "reasoning": f"{home} strong form ({home_form['wins']}W-{home_form['draws']}D-{home_form['losses']}L, {home_form['gf']} goals) with home advantage. Early attack rate {home_pre['early_attack_pct']}%."}
                else:
                    conf = min(82, 58 + int(abs(diff) * 14))
                    return {**base, "bet": f"{away} Win",
                            "odds": round(odds["away"], 2),
                            "confidence": conf,
                            "reasoning": f"{away} dominant away form ({away_form['wins']}W-{away_form['draws']}D-{away_form['losses']}L, {away_form['gf']} goals). Scored first in {away_pre['scored_first_pct']}% of recent games."}
            return None

        # ---- OVER 2.5 GOALS ----
        if market == "over25":
            home_avg = home_form["gf"] / max(home_form["games"], 1)
            away_avg = away_form["gf"] / max(away_form["games"], 1)
            total_avg = home_avg + away_avg
            both_btts = (home_pre["btts_pct"] + away_pre["btts_pct"]) / 2
            if total_avg > 2.2 and both_btts > 45:
                conf = min(80, 50 + int(total_avg * 8) + int(both_btts * 0.2))
                return {**base, "bet": "Over 2.5 Goals",
                        "odds": round(1.65 + (total_avg - 2.2) * 0.1, 2),
                        "confidence": conf,
                        "reasoning": f"Combined avg {round(total_avg,1)} goals/game. {home} BTTS {home_pre['btts_pct']}%, {away} BTTS {away_pre['btts_pct']}%. Both teams scoring freely."}
            return None

        # ---- BTTS ----
        if market == "btts":
            if home_pre["btts_pct"] >= 50 and away_pre["btts_pct"] >= 50:
                avg_btts = (home_pre["btts_pct"] + away_pre["btts_pct"]) / 2
                conf = min(78, 50 + int(avg_btts * 0.5))
                return {**base, "bet": "Both Teams to Score",
                        "odds": round(1.70 + avg_btts * 0.003, 2),
                        "confidence": conf,
                        "reasoning": f"{home} BTTS in {home_pre['btts_pct']}% of games, {away} in {away_pre['btts_pct']}%. Both defences conceding regularly."}
            return None

        # ---- FIRST GOAL / TEAM TO SCORE FIRST ----
        if market == "first_goal":
            # Pick the team more likely to score first
            home_first = home_pre["scored_first_pct"] + home_pre["early_attack_pct"] * 0.5
            away_first = away_pre["scored_first_pct"] + away_pre["early_attack_pct"] * 0.5
            # Home advantage bonus
            home_first += 10
            if home_first > 55:
                conf = min(76, 45 + int(home_first * 0.5))
                return {**base,
                        "bet": f"{home} to Score First",
                        "odds": round(1.55 + (100 - home_first) * 0.01, 2),
                        "confidence": conf,
                        "reasoning": f"{home} scored first in {home_pre['scored_first_pct']}% of recent games, early attack in {home_pre['early_attack_pct']}% of games. Strong opening aggression."}
            elif away_first > home_first + 15:
                conf = min(72, 42 + int(away_first * 0.45))
                return {**base,
                        "bet": f"{away} to Score First",
                        "odds": round(2.10 + (100 - away_first) * 0.01, 2),
                        "confidence": conf,
                        "reasoning": f"{away} scored first in {away_pre['scored_first_pct']}% of games, fast starters with {away_pre['early_attack_pct']}% early attack rate."}
            return None

        # ---- HT/FT - based on HT trends ----
        if market == "ht_over05":
            avg_fh = home_pre["fh_goals_for_avg"] + away_pre["fh_goals_for_avg"]
            if avg_fh > 1.2:
                conf = min(76, 50 + int(avg_fh * 15))
                return {**base, "bet": "HT Over 0.5 Goals",
                        "odds": round(1.35 + (2.0 - avg_fh) * 0.1, 2),
                        "confidence": conf,
                        "reasoning": f"{home} avg {home_pre['fh_goals_for_avg']} FH goals, {away} avg {away_pre['fh_goals_for_avg']} FH goals. High-scoring first halves expected."}
            return None

        # ---- HT OVER 1.5 ----
        if market == "ht_over15":
            avg_fh = home_pre["fh_goals_for_avg"] + away_pre["fh_goals_for_avg"]
            if avg_fh > 2.0:
                conf = min(74, 44 + int(avg_fh * 12))
                return {**base, "bet": "HT Over 1.5 Goals",
                        "odds": round(2.20 + (3.0 - avg_fh) * 0.15, 2),
                        "confidence": conf,
                        "reasoning": f"Combined FH goal avg {round(avg_fh,1)}. {home} HT lead in {home_pre['ht_lead_pct']}% of games, {away} FH avg {away_pre['fh_goals_for_avg']} goals."}
            return None

        return None
    except Exception as e:
        print(f"Pick error: {e}")
        return None


def ht_acca_pick(fixture: dict, home_pre: dict, away_pre: dict,
                 home_form: dict, away_form: dict) -> Optional[dict]:
    """
    Half-Time specific acca pick.
    Uses: FH goals avg, HT lead/draw/behind rates, early attack pct, scored first pct.
    Picks the best HT market for each game.
    """
    try:
        home  = fixture["teams"]["home"]["name"]
        away  = fixture["teams"]["away"]["name"]
        fid   = fixture["fixture"]["id"]
        home_id = fixture["teams"]["home"]["id"]
        away_id = fixture["teams"]["away"]["id"]
        dt    = datetime.fromisoformat(
            fixture["fixture"]["date"].replace("Z", "+00:00"))
        date_str = dt.strftime("%d/%m %H:%M")
        base  = {"id": fid, "home": home, "away": away, "date": date_str,
                 "home_id": home_id, "away_id": away_id}

        avg_fh_goals = home_pre["fh_goals_for_avg"] + away_pre["fh_goals_for_avg"]

        # Option 1: HT Over 0.5 (most reliable — easier to hit)
        if avg_fh_goals > 1.3:
            conf = min(80, 52 + int(avg_fh_goals * 14))
            return {**base,
                    "bet": "HT Over 0.5 Goals",
                    "odds": round(max(1.20, 1.50 - (avg_fh_goals - 1.3) * 0.1), 2),
                    "confidence": conf,
                    "market_type": "HT Goals",
                    "reasoning": f"{home} avg {home_pre['fh_goals_for_avg']} FH goals, {away} avg {away_pre['fh_goals_for_avg']}. Combined {round(avg_fh_goals,1)}/game. {home} lead at HT in {home_pre['ht_lead_pct']}% of games."}

        # Option 2: Home team leading at HT
        if home_pre["ht_lead_pct"] >= 50 and home_form["form_rating"] > away_form["form_rating"]:
            conf = min(72, 40 + int(home_pre["ht_lead_pct"] * 0.6))
            return {**base,
                    "bet": f"{home} HT Win",
                    "odds": round(2.10 + (100 - home_pre["ht_lead_pct"]) * 0.02, 2),
                    "confidence": conf,
                    "market_type": "HT Result",
                    "reasoning": f"{home} lead at half-time in {home_pre['ht_lead_pct']}% of recent games. Score first in {home_pre['scored_first_pct']}% — early pressure predicted."}

        # Option 3: HT Draw (both teams evenly matched)
        if (home_pre["ht_draw_pct"] >= 40 and away_pre["ht_draw_pct"] >= 40
                and abs(home_form["form_rating"] - away_form["form_rating"]) < 0.4):
            avg_draw = (home_pre["ht_draw_pct"] + away_pre["ht_draw_pct"]) / 2
            conf = min(68, 38 + int(avg_draw * 0.6))
            return {**base,
                    "bet": "HT Draw",
                    "odds": round(2.30 + (50 - avg_draw) * 0.02, 2),
                    "confidence": conf,
                    "market_type": "HT Result",
                    "reasoning": f"Even teams: {home} draw at HT {home_pre['ht_draw_pct']}%, {away} {away_pre['ht_draw_pct']}%. Closely matched form."}

        return None
    except Exception as e:
        print(f"HT pick error: {e}")
        return None

# =========================
# ENDPOINTS
# =========================

@app.get("/")
@app.head("/")
async def root():
    return {"status": "AccaGenius V4 API running", "version": "4.0"}


@app.get("/fixtures/{league_code}")
async def get_fixtures(league_code: str):
    league_id = LEAGUE_IDS.get(league_code.upper())
    if not league_id:
        raise HTTPException(404, "League not found")
    try:
        r = requests.get(f"{BASE_URL}/fixtures", headers=HEADERS, params={
            "league": league_id, "season": get_season(),
            "next": 20, "timezone": "Europe/London"
        }, timeout=10)
        if r.status_code != 200:
            raise HTTPException(500, "API Error")
        fixtures_by_date = {}
        for f in r.json().get("response", []):
            date = f["fixture"]["date"].split("T")[0]
            fixtures_by_date.setdefault(date, []).append({
                "id":        f["fixture"]["id"],
                "date":      f["fixture"]["date"],
                "time":      datetime.fromisoformat(f["fixture"]["date"].replace("Z", "+00:00")).strftime("%H:%M"),
                "home":      f["teams"]["home"]["name"],
                "away":      f["teams"]["away"]["name"],
                "home_id":   f["teams"]["home"]["id"],
                "away_id":   f["teams"]["away"]["id"],
                "home_logo": f["teams"]["home"]["logo"],
                "away_logo": f["teams"]["away"]["logo"],
                "venue":     f["fixture"]["venue"]["name"],
                "referee":   f["fixture"]["referee"],
                "league_id": league_id,
            })
        return {"league": league_code, "fixtures": fixtures_by_date}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/generate-acca")
async def generate_acca(req: AccaRequest):
    """Generate smart acca using pre-game stats"""
    leagues = req.leagues if req.leagues else ["PL", "PD", "BL1", "SA", "FL1"]
    random.shuffle(leagues)
    picks = []
    seen  = set()
    today = datetime.now().date()

    for code in leagues:
        lid = LEAGUE_IDS.get(code)
        if not lid:
            continue
        r = requests.get(f"{BASE_URL}/fixtures", headers=HEADERS, params={
            "league": lid, "season": get_season(),
            "next": 20, "timezone": "Europe/London"
        }, timeout=10)
        if r.status_code != 200:
            continue
        fixtures = r.json().get("response", [])
        random.shuffle(fixtures)

        for f in fixtures:
            fid = f["fixture"]["id"]
            if fid in seen:
                continue
            if req.today_only:
                fd = datetime.fromisoformat(f["fixture"]["date"].replace("Z", "+00:00")).date()
                if fd != today:
                    continue

            hid = f["teams"]["home"]["id"]
            aid = f["teams"]["away"]["id"]

            home_form = get_team_form(hid, lid)
            away_form = get_team_form(aid, lid)
            home_pre  = get_pregame_stats(hid, lid)
            away_pre  = get_pregame_stats(aid, lid)

            pick = smart_pick(f, home_form, away_form, home_pre, away_pre, req.market, req.risk)
            if pick and pick["confidence"] >= 58:
                seen.add(fid)
                picks.append(pick)

        if len(picks) >= req.selections * 3:
            break

    picks.sort(key=lambda x: x["confidence"], reverse=True)
    # Slight shuffle to vary results
    top = picks[:max(req.selections * 2, 8)]
    random.shuffle(top)
    top_picks = top[:req.selections]

    if not top_picks:
        return {"message": "No picks found", "total_selections": 0,
                "total_odds": 0, "confidence": 0, "selections": []}

    total_odds = 1.0
    for p in top_picks:
        total_odds *= p["odds"]
    avg_conf = sum(p["confidence"] for p in top_picks) / len(top_picks)

    return {
        "message": f"AI Acca – {req.market} market",
        "total_selections": len(top_picks),
        "total_odds": round(total_odds, 2),
        "confidence": round(avg_conf),
        "market": req.market,
        "selections": top_picks,
    }


@app.post("/generate-ht-acca")
async def generate_ht_acca(req: HTAccaRequest):
    """
    Half-Time Acca Generator.
    Uses first-half goals averages, HT lead/draw rates, early attack %, scored-first %.
    All stats are pre-game historical data — no live dependency.
    """
    leagues = req.leagues if req.leagues else ["PL", "PD", "BL1", "SA", "FL1", "ELC"]
    random.shuffle(leagues)
    picks = []
    seen  = set()

    for code in leagues:
        lid = LEAGUE_IDS.get(code)
        if not lid:
            continue
        r = requests.get(f"{BASE_URL}/fixtures", headers=HEADERS, params={
            "league": lid, "season": get_season(),
            "next": 20, "timezone": "Europe/London"
        }, timeout=10)
        if r.status_code != 200:
            continue
        fixtures = r.json().get("response", [])
        random.shuffle(fixtures)

        for f in fixtures:
            fid = f["fixture"]["id"]
            if fid in seen:
                continue
            hid = f["teams"]["home"]["id"]
            aid = f["teams"]["away"]["id"]

            home_form = get_team_form(hid, lid)
            away_form = get_team_form(aid, lid)
            home_pre  = get_pregame_stats(hid, lid)
            away_pre  = get_pregame_stats(aid, lid)

            pick = ht_acca_pick(f, home_pre, away_pre, home_form, away_form)
            if pick and pick["confidence"] >= 58:
                seen.add(fid)
                picks.append(pick)

        if len(picks) >= req.selections * 3:
            break

    picks.sort(key=lambda x: x["confidence"], reverse=True)
    top = picks[:max(req.selections * 2, 8)]
    random.shuffle(top)
    top_picks = top[:req.selections]

    if not top_picks:
        return {"message": "No HT picks found", "total_selections": 0,
                "total_odds": 0, "confidence": 0, "selections": []}

    total_odds = 1.0
    for p in top_picks:
        total_odds *= p["odds"]
    avg_conf = sum(p["confidence"] for p in top_picks) / len(top_picks)

    return {
        "message": "HT Acca Generated from Pre-Game First Half Stats",
        "total_selections": len(top_picks),
        "total_odds": round(total_odds, 2),
        "confidence": round(avg_conf),
        "selections": top_picks,
    }


@app.get("/standings/{league_code}")
async def get_standings(league_code: str):
    lid = LEAGUE_IDS.get(league_code.upper())
    if not lid:
        raise HTTPException(404, "League not found")
    try:
        r = requests.get(f"{BASE_URL}/standings", headers=HEADERS,
                         params={"league": lid, "season": get_season()}, timeout=10)
        data = r.json().get("response", [])[0].get("league", {}).get("standings", [[]])[0]
        return {"league": league_code, "standings": [{
            "position": t["rank"], "team": t["team"]["name"], "logo": t["team"]["logo"],
            "played": t["all"]["played"], "won": t["all"]["win"], "drawn": t["all"]["draw"],
            "lost": t["all"]["lose"], "gf": t["all"]["goals"]["for"],
            "ga": t["all"]["goals"]["against"], "gd": t["goalsDiff"],
            "goalDifference": t["goalsDiff"], "points": t["points"]
        } for t in data]}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/live")
async def get_live():
    try:
        r = requests.get(f"{BASE_URL}/fixtures", headers=HEADERS,
                         params={"live": "all", "timezone": "Europe/London"}, timeout=10)
        if r.status_code != 200:
            return {"matches": []}
        return {"matches": [{
            "id":         f["fixture"]["id"],
            "home":       f["teams"]["home"]["name"],
            "away":       f["teams"]["away"]["name"],
            "home_score": f["goals"]["home"],
            "away_score": f["goals"]["away"],
            "minute":     f["fixture"]["status"]["elapsed"],
            "status":     f["fixture"]["status"]["long"],
            "league":     f["league"]["name"],
        } for f in r.json().get("response", [])[:15]]}
    except:
        return {"matches": []}


@app.get("/h2h/{t1}/{t2}")
async def get_h2h(t1: int, t2: int):
    try:
        r = requests.get(f"{BASE_URL}/fixtures/headtohead", headers=HEADERS,
                         params={"h2h": f"{t1}-{t2}", "last": 10,
                                 "timezone": "Europe/London"}, timeout=10)
        if r.status_code != 200:
            return {"matches": []}
        return {"matches": [{
            "date":       f["fixture"]["date"].split("T")[0],
            "home":       f["teams"]["home"]["name"],
            "away":       f["teams"]["away"]["name"],
            "home_score": f["goals"]["home"],
            "away_score": f["goals"]["away"],
        } for f in r.json().get("response", [])]}
    except:
        return {"matches": []}


@app.get("/predictions/{fixture_id}")
async def get_predictions(fixture_id: int):
    try:
        r = requests.get(f"{BASE_URL}/predictions", headers=HEADERS,
                         params={"fixture": fixture_id}, timeout=10)
        if r.status_code != 200:
            return {"prediction": None}
        preds = r.json().get("response", [])
        if not preds:
            return {"prediction": None}
        p = preds[0].get("predictions", {})
        teams = preds[0].get("teams", {})
        winner = (p.get("winner") or {})
        pct    = (p.get("percent") or {})
        goals  = (p.get("goals") or {})
        return {"prediction": {
            "winner":       winner.get("name"),
            "percent_home": pct.get("home", "0%"),
            "percent_draw": pct.get("draw", "0%"),
            "percent_away": pct.get("away", "0%"),
            "goals_home":   goals.get("home"),
            "goals_away":   goals.get("away"),
            "advice":       p.get("advice", "No advice available"),
        }, "predictions": p}
    except:
        return {"prediction": None}


@app.get("/odds/{fixture_id}")
async def get_odds(fixture_id: int):
    try:
        r = requests.get(f"{BASE_URL}/odds", headers=HEADERS,
                         params={"fixture": fixture_id}, timeout=10)
        if r.status_code != 200:
            return {"odds": []}
        bks = r.json().get("response", [{}])[0].get("bookmakers", [])
        odds_list = []
        for bk in bks[:5]:
            for bet in bk.get("bets", []):
                if bet["name"] == "Match Winner":
                    v = bet.get("values", [])
                    if len(v) >= 3:
                        odds_list.append({
                            "bookmaker": bk["name"],
                            "home": v[0]["odd"], "draw": v[1]["odd"], "away": v[2]["odd"]
                        })
        return {"odds": odds_list}
    except:
        return {"odds": []}


@app.get("/form/{team_id}/{league_id}")
async def get_form_endpoint(team_id: int, league_id: int):
    try:
        form = get_team_form(team_id, league_id)
        pre  = get_pregame_stats(team_id, league_id)
        fixtures = get_last_fixtures(team_id, league_id, 5)
        recent = []
        for f in fixtures:
            is_home = f["teams"]["home"]["id"] == team_id
            gf = (f["goals"]["home"] if is_home else f["goals"]["away"]) or 0
            ga = (f["goals"]["away"] if is_home else f["goals"]["home"]) or 0
            opp = f["teams"]["away"]["name"] if is_home else f["teams"]["home"]["name"]
            opp_logo = f["teams"]["away"]["logo"] if is_home else f["teams"]["home"]["logo"]
            result = "W" if gf > ga else ("D" if gf == ga else "L")
            ht_home = (f.get("score", {}).get("halftime", {}) or {}).get("home") or 0
            ht_away = (f.get("score", {}).get("halftime", {}) or {}).get("away") or 0
            ht_for = ht_home if is_home else ht_away
            ht_ag  = ht_away if is_home else ht_home
            recent.append({
                "date": f["fixture"]["date"].split("T")[0],
                "opponent": opp, "opponent_logo": opp_logo,
                "venue": "H" if is_home else "A",
                "goals_for": gf, "goals_against": ga,
                "ht_for": ht_for, "ht_against": ht_ag,
                "result": result,
            })
        return {**form, **pre, "recent_fixtures": recent}
    except Exception as e:
        return {"games": 0, "wins": 0, "draws": 0, "losses": 0, "gf": 0, "ga": 0,
                "form_rating": 0, "recent_fixtures": [], **_empty_pregame()}


# =========================
# SAVED ACCAS
# =========================

@app.get("/saved-accas")
async def get_saved():
    return {"accas": saved_accas_store}

@app.post("/saved-accas")
@app.post("/save-acca")
async def save_acca(req: dict):
    acca = {
        "id": len(saved_accas_store),
        "name": req.get("name", "My Acca"),
        "selections": req.get("selections", []),
        "total_odds": req.get("total_odds", 0),
        "stake": req.get("stake", 10),
        "created_at": datetime.now().isoformat(),
        "result": "pending",
    }
    saved_accas_store.append(acca)
    return {"message": "Saved", "id": acca["id"], "acca": acca}

@app.delete("/saved-accas/{acca_id}")
async def delete_saved(acca_id: int):
    global saved_accas_store
    saved_accas_store = [a for a in saved_accas_store if a.get("id") != acca_id]
    return {"message": "Deleted"}

@app.patch("/saved-accas/{acca_id}/result")
async def update_result(acca_id: int, req: dict):
    for a in saved_accas_store:
        if a.get("id") == acca_id:
            a["result"] = req.get("result", "pending")
            return {"message": "Updated", "acca": a}
    raise HTTPException(404, "Not found")


# =========================
# AI CHAT — FULL FOOTBALL KNOWLEDGE
# =========================

FOOTBALL_KNOWLEDGE = {
    # Tactics
    "tiki taka": "Tiki-taka is a Spanish possession-based style of play emphasising short passing, movement, and maintaining possession. Perfected by Pep Guardiola's Barcelona (2008–2012) with players like Xavi, Iniesta, and Messi. The system requires technically gifted players and uses a high defensive line to press quickly after losing the ball.",
    "gegenpressing": "Gegenpressing is a high-intensity pressing system pioneered by Jürgen Klopp, first at Borussia Dortmund then Liverpool. The idea: immediately press the opponent after losing the ball, using the team's positional structure to win it back quickly. Requires extreme fitness and positional discipline.",
    "4231": "The 4-2-3-1 is one of the most popular formations in modern football. Two defensive midfielders protect the backline, three attacking midfielders support one striker. It offers defensive stability whilst allowing creativity. Used by many top clubs and national teams.",
    "433": "The 4-3-3 uses three forwards — typically two wide players and a central striker — backed by three midfielders and a back four. Allows wide overloads and quick transitions. Famously used by Barcelona, Liverpool, and the Netherlands national team.",
    "false 9": "A False 9 is a centre-forward who drops deep into midfield to create space and confusion for defenders. Rather than a traditional striker, they link play and drag defenders out of position. Messi played this role brilliantly under Guardiola.",
    "offside trap": "The offside trap is a defensive tactic where defenders step up simultaneously to put attackers offside at the moment the ball is played. Requires perfect timing and communication — getting it wrong leaves attackers clean through on goal.",

    # Rules
    "var": "VAR (Video Assistant Referee) was introduced to football to review clear and obvious errors in four match-changing situations: goals, penalties, red cards, and mistaken identity. Officials review footage and can advise the on-field referee to change a decision or check the monitor themselves.",
    "offside": "A player is in an offside position if any part of the head, body or feet is in the opponent's half and nearer to the opponent's goal line than both the ball and the second-to-last opponent at the moment the ball is played. Being offside only becomes an offside offence if they become actively involved in play.",
    "handball": "A handball offence occurs if a player deliberately handles the ball, or if an accidental handball leads directly to a goal or clear goal-scoring opportunity. The laws were updated in 2020/21 to include accidental handball that leads to a goal, even without clear intent.",
    "penalty": "A penalty kick is awarded when a foul or handball that would normally result in a direct free-kick occurs inside the penalty area. The kick is taken from the penalty spot — 12 yards from goal — with only the goalkeeper to beat.",
    "extra time": "Extra time consists of two 15-minute periods played when a knockout match is level after 90 minutes. If still level after extra time, the match proceeds to a penalty shootout.",
    "goal line technology": "Goal Line Technology (GLT) uses cameras or magnetic sensors to determine definitively whether the ball has fully crossed the goal line. The referee receives an instant signal on their watch. Used in the Premier League, World Cup, and other major competitions.",

    # Competitions
    "champions league": "The UEFA Champions League is Europe's premier club competition, contested annually by the top clubs from European domestic leagues. The current format features a league phase (36 teams, 8 games each) followed by knockout rounds. Real Madrid are the record winners with 15 titles.",
    "premier league": "The Premier League is England's top division, founded in 1992. It features 20 clubs playing 38 games each season. Manchester City, Manchester United, Arsenal, Liverpool, and Chelsea have dominated the modern era. Known globally for its pace, physicality, and atmosphere.",
    "world cup": "The FIFA World Cup is the world's most prestigious international football tournament, held every four years. 32 teams (expanding to 48 in 2026) compete for the title. Brazil are the most successful nation with 5 titles, followed by Germany and Italy with 4 each.",
    "euros": "The UEFA European Championship is held every four years between European nations. Spain are the most successful with 4 titles. The 2024 tournament in Germany saw Spain beat England 2-1 in the final.",
    "fa cup": "The FA Cup is the world's oldest football competition, first held in 1872. It is an open knockout competition, meaning any English club from any level can enter. Known for upsets and giant-killings.",

    # History
    "maradona": "Diego Maradona (1960–2020) is widely considered one of the greatest footballers ever. Famous for the 1986 World Cup where he almost single-handedly won it for Argentina, including the infamous 'Hand of God' goal and the 'Goal of the Century' vs England. He captained Napoli to two Serie A titles.",
    "pele": "Pelé (1940–2022) is regarded as one of the greatest players in history, winning three World Cups with Brazil (1958, 1962, 1970). He scored over 1,000 goals in his career and is the all-time top scorer for Brazil.",
    "1966 world cup": "England hosted and won the 1966 World Cup, beating West Germany 4-2 in the final at Wembley. Geoff Hurst scored a hat-trick — still the only one in a World Cup final. The famous controversial goal where the ball hit the crossbar and the line was ruled to have crossed by the linesman.",
    "treble": "The Treble refers to winning three major trophies in a single season: typically the domestic league, domestic cup, and European cup. Manchester United won the first English Treble in 1999. Manchester City won it in 2023.",

    # Modern players
    "messi": "Lionel Messi is widely considered the greatest player of all time. He won 8 Ballon d'Or awards, 4 Champions Leagues, 10 La Liga titles with Barcelona, and the 2021 Copa América and 2022 FIFA World Cup with Argentina. He currently plays for Inter Miami in MLS.",
    "ronaldo": "Cristiano Ronaldo has won 5 Ballon d'Or awards, 5 Champions Leagues (3 with Real Madrid, 1 each with United and Juventus), and Premier League, La Liga and Serie A titles. He is the all-time top scorer in the Champions League and for the Portugal national team.",
    "haaland": "Erling Haaland joined Manchester City in 2022 and broke the Premier League single-season scoring record in his debut season with 36 goals. Known for his pace, physicality, and clinical finishing.",
    "bellingham": "Jude Bellingham joined Real Madrid in 2023 and had an exceptional debut season, winning La Liga and contributing significantly in the Champions League. He plays attacking midfield with high energy, goals, and leadership.",
    "salah": "Mohamed Salah joined Liverpool in 2017 and broke the Premier League single-season scoring record (shared) with 32 goals in 2017/18. He has been consistently one of the best players in the world, winning the Premier League, FA Cup, League Cup, Champions League and Club World Cup with Liverpool.",
}

BETTING_KNOWLEDGE = {
    "value bet": "A value bet is when you believe the probability of an outcome is higher than what the bookmaker's odds imply. For example, if you assess a team has a 60% chance of winning but the odds imply only 45%, that's value. Long-term profitable betting is built on identifying value, not just picking winners.",
    "kelly criterion": "The Kelly Criterion is a mathematical formula for calculating optimal bet size based on your edge. Stake = (bp - q) / b where b = decimal odds minus 1, p = probability of winning, q = probability of losing. Many professionals use half-Kelly to reduce variance.",
    "asian handicap": "Asian Handicap betting eliminates the draw by giving one team a head start. For example, -1.5 means the team must win by 2+ goals for your bet to win. Half-ball handicaps eliminate the draw completely, refunding stakes if the handicap lands exactly.",
    "each way": "Each way betting is most common in horse racing but applies to football outright markets. You're placing two bets: one on the team to win, one on them to 'place' (usually top 4 or reach a final). If they win, both parts pay out.",
    "accumulator": "An accumulator (acca) combines multiple selections into one bet. All selections must win for the bet to pay out. The odds multiply together — a 5-fold acca at average 2.0 odds returns 32x your stake if all legs win.",
    "dutching": "Dutching means backing multiple outcomes in the same event to guarantee a profit regardless of which one wins. You calculate stake sizes so the total return is the same whichever selection wins.",
    "lay bet": "A lay bet is a bet on something NOT to happen — you're acting as the bookmaker. Available on exchanges like Betfair. If the selection wins, you pay out; if it loses, you win the stake. Liability can be high on short-priced selections.",
}

@app.post("/chat")
async def chat(req: dict):
    """Full football AI — tactics, history, rules, players, betting"""
    msg = req.get("message", "").strip()
    if not msg:
        return {"response": "Ask me anything about football!"}

    ml = msg.lower()

    # ---- Check football knowledge base ----
    for keyword, answer in FOOTBALL_KNOWLEDGE.items():
        if keyword in ml:
            return {"response": f"📚 **{keyword.title()}**\n\n{answer}"}

    for keyword, answer in BETTING_KNOWLEDGE.items():
        if keyword in ml:
            return {"response": f"💰 **{keyword.title()}**\n\n{answer}"}

    # ---- Formation questions ----
    if any(f in ml for f in ["formation", "4-4-2", "4-3-3", "4-2-3-1", "3-5-2", "3-4-3", "5-3-2"]):
        return {"response": """📋 **Common Football Formations:**

**4-4-2 (Classic)** — Two banks of four with two strikers. Solid, simple, hard to beat but predictable attacking.

**4-3-3** — Three forwards, great for wide overloads. Used by Liverpool, Barcelona, Man City. Requires energetic wingers and midfielders.

**4-2-3-1** — Double pivot shields the defence. Three attacking mids behind one striker. Flexible and dominant in modern football.

**3-5-2** — Three centre-backs, wing-backs push forward. Packs midfield but vulnerable wide. Used by Italy, Conte's teams.

**5-3-2 / 5-4-1** — Ultra-defensive, strong in cup ties. Hard to beat but limits attacking options.

**4-3-3 vs 4-2-3-1** — The most common clash in modern football. The 4-2-3-1 usually wins the midfield battle due to the double pivot, unless the 4-3-3 presses intensely."""}

    # ---- Referee / VAR questions ----
    if any(w in ml for w in ["referee", "ref", "var decision", "red card", "yellow card"]):
        return {"response": """🟥 **Referee Decisions & VAR:**

**Yellow Card** — Caution for fouls, dissent, time-wasting, unsporting behaviour. Two yellows = red card.

**Red Card** — Immediate dismissal for serious foul play, violent conduct, denying an obvious goal-scoring opportunity (DOGSO), or two yellows.

**VAR Reviews:** Only used for clear and obvious errors in:
1. Goals (offside, handball, foul in build-up)
2. Penalty decisions
3. Red card incidents
4. Mistaken identity

**VAR cannot:** Change subjective decisions — if a referee deems something not a penalty, VAR can only intervene if it's a clear and obvious error, not just a different opinion."""}

    # ---- Injuries / fitness ----
    if any(w in ml for w in ["injury", "injured", "fitness", "hamstring", "knee", "acl"]):
        return {"response": """🏥 **Football Injuries — What You Need to Know:**

**Most Common:**
• **Hamstring** — 2-8 weeks. Common in sprinters and players who push for pace.
• **Ankle ligament** — 2-6 weeks. Often from tackles.
• **ACL (Anterior Cruciate Ligament)** — 9-12 months. Season-ending. Very serious.
• **Meniscus** — 6-12 weeks. Knee cartilage tear.
• **Groin/adductor** — 2-6 weeks. Common in players changing direction quickly.

**Betting relevance:** Always check team news the day before a match. Key injury absences can significantly affect odds and outcomes. Sites like BBC Sport, Sky Sports, and Physio Room track injuries in real time.

**Recovery tech:** Modern clubs use cryotherapy, hyperbaric chambers, and GPS-tracked load management to reduce injury risk."""}

    # ---- Transfer questions ----
    if any(w in ml for w in ["transfer", "signing", "fee", "release clause", "contract"]):
        return {"response": """💸 **Football Transfers:**

**How they work:** A club agrees a fee with the selling club, then the player agrees personal terms (salary, contract length). The move is only complete when all parties sign.

**Record fees:** Neymar remains the world record transfer at €222m (PSG, 2017). Mbappé joined Real Madrid on a free in 2024 — the most expensive free in history with wages/signing on fees.

**Release clauses:** A pre-agreed fee that automatically activates a transfer. Common in La Liga. A club must pay the clause directly to La Liga, not the selling club.

**Transfer windows:** 
• Summer window: June–August (varies by country)
• Winter window: January (usually 31 days)

**Free transfers:** When a player's contract expires, they can leave for free. Bosman ruling (1995) established this right in European football."""}

    # ---- Pressing / defensive systems ----
    if any(w in ml for w in ["press", "pressing", "high press", "low block", "defensive", "counter attack"]):
        return {"response": """⚡ **Pressing & Defensive Systems:**

**High Press** — Team presses high up the pitch immediately after losing possession. Forces opponents into mistakes in their own half. Klopp's Liverpool and Guardiola's City are the best examples. Requires extreme fitness.

**Mid-block** — Team defends in their own half, compact shape, but press when triggered. Balanced approach. Most Premier League sides use this.

**Low block / Parking the bus** — Deep defensive shape, typically 5-4-1 or 4-5-1. Hard to break down but offers few counter-attack opportunities.

**Counter-attack** — Absorb pressure, then hit fast on the break with pace. Leicester's 2016 title was built on this. Requires fast forwards and disciplined defenders.

**PPDA (Passes Allowed Per Defensive Action)** — The stat used to measure pressing intensity. Lower = more intense press. Man City and Liverpool typically have the lowest PPDA in the Premier League."""}

    # ---- Stats / analytics ----
    if any(w in ml for w in ["xg", "expected goals", "analytics", "stats", "data", "opta"]):
        return {"response": """📊 **Football Analytics & Stats:**

**xG (Expected Goals)** — The most important modern stat. Measures the quality of scoring chances on a scale of 0-1. A penalty = ~0.76 xG. A one-on-one = ~0.45 xG. Tells you whether results reflect performance.

**xA (Expected Assists)** — The xG of the shot that came from a specific pass. Shows creative contribution more accurately than raw assists.

**PPDA** — Passes allowed Per Defensive Action. Measures pressing intensity. Lower = harder press.

**Progressive Passes/Carries** — Passes/carries that move the ball significantly toward the opponent's goal.

**Key companies:** Opta (Stats Perform), StatsBomb, WyScout, Transfermarkt for values.

**Where to find free stats:** FBref.com (powered by StatsBomb) is the best free football stats database. Understat.com has xG for major European leagues."""}

    # ---- History / iconic moments ----
    if any(w in ml for w in ["greatest", "best ever", "goat", "iconic", "legend"]):
        return {"response": """🏆 **Football's Greatest Players:**

**The GOAT debate:**
• **Lionel Messi** — 8 Ballon d'Or, 2022 World Cup, 4 Champions Leagues. The most naturally gifted player ever.
• **Cristiano Ronaldo** — 5 Ballon d'Or, 5 Champions Leagues, all-time UCL scorer. The most driven and athletic.
• **Pelé** — 3 World Cups, 1000+ goals. Dominated an era with less protection and worse pitches.
• **Maradona** — 1986 World Cup near single-handedly. Genius with a flawed genius's life.

**Most decorated clubs:** Real Madrid (15 UCL titles), Barcelona (5), Bayern Munich (6).

**Most World Cup wins:** Brazil 5, Germany 4, Italy 4, Argentina 3, France 2."""}

    # ---- AccaGenius feature questions ----
    if any(w in ml for w in ["how does", "accagenius", "how to", "feature", "tool"]):
        return {"response": """🤖 **How AccaGenius Works:**

**AI Acca Generator** — Picks bets based on real pre-game stats:
• Team form (last 5-10 games W/D/L)
• First-half goals average
• Early attack % (goals in first 20 mins)
• Scored first % per team
• BTTS rate, Over 2.5 rate, clean sheet rate

**HT Acca Generator** — Specifically analyses first-half patterns:
• HT lead / draw / behind rates
• FH goals for and against averages
• Early attack pressure stats

**Markets available:**
• Match Winner, Over 2.5, BTTS
• First Goal (team to score first based on early attack stats)
• HT Over 0.5, HT Over 1.5

**Analyse Match** — View H2H, pre-game form stats, AI prediction and bookmaker odds for any fixture.

**Saved Accas** — Save your bets, mark results, track P&L and win rate over time."""}

    # ---- Betting advice ----
    if any(w in ml for w in ["tip", "advice", "recommend", "should i", "bet on"]):
        return {"response": """💡 **Betting Tips & Strategy:**

1. **Find value, not just winners** — A 60% chance on 1.40 odds is a losing bet long-term. Look for odds that underestimate the true probability.

2. **Bank management** — Never bet more than 2-5% of your bankroll on a single bet. Flat staking is the safest approach for beginners.

3. **Use pre-game stats** — AccaGenius analyses early attack %, scored-first rate, first-half goals and BTTS history — not just league position or recent form headlines.

4. **Shop for best odds** — Always compare bookmakers before placing. Even 0.10 difference in odds adds up significantly over time.

5. **Track everything** — Use the Saved Accas tracker to monitor your P&L, win rate, and identify which markets work best for you.

6. **Avoid chasing losses** — The biggest mistake punters make. Set a daily/weekly limit and stick to it.

7. **Specialise** — Knowing 2-3 leagues really well beats having a surface opinion on 10+."""}

    # ---- Greetings ----
    if any(w in ml for w in ["hello", "hi", "hey", "what can you", "help"]):
        return {"response": """👋 **Welcome to AccaGenius AI Co-Pilot!**

I'm a full football AI — ask me about anything:

⚽ **Tactics** — formations, pressing, tiki-taka, gegenpressing
📖 **Rules** — VAR, offside, handballs, penalties
🏆 **History** — World Cups, legendary players, iconic moments
📊 **Stats** — xG, expected assists, analytics explained
💰 **Betting** — value bets, Kelly criterion, Asian handicaps, market tips
🤖 **AccaGenius** — how the AI picks work, which markets suit which teams

Just ask! I know football inside and out. ⚽"""}

    if any(w in ml for w in ["thank", "thanks", "cheers", "brilliant", "great"]):
        return {"response": "You're welcome! Any other football questions — tactics, stats, history, betting — just ask! ⚽🍀"}

    # ---- Catch-all with context ----
    return {"response": f"""🤔 I'm not sure exactly what you mean by *"{msg}"* — but here's what I can help with:

⚽ **Ask me about:**
• Specific teams: "Tell me about Liverpool's pressing style"
• Tactics: "What is a false 9?" or "Explain gegenpressing"  
• Rules: "How does VAR work?" or "What is offside?"
• Players: "Tell me about Messi" or "Who is Haaland?"
• Stats: "What is xG?" or "Explain expected assists"
• History: "Who won the 2022 World Cup?" or "Tell me about the 1966 final"
• Betting: "What is a value bet?" or "Explain Asian handicap"
• AccaGenius: "How do the HT picks work?"

Try asking in a bit more detail and I'll give you a full breakdown! ⚽"""}

print("✅ AccaGenius V4 Backend Ready!")
