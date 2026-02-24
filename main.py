print("♠ ACCAGENIUS - COMPLETE PLATFORM WITH AI CHAT & FORM ANALYSIS ♠")

import os
import requests
import random
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# =========================
# API KEYS
# =========================
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "0192e664450828fc0345770b74b75e9f")

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {
    "x-rapidapi-key": API_FOOTBALL_KEY,
    "x-rapidapi-host": "v3.football.api-sports.io"
}

# =========================
# FASTAPI APP
# =========================
app = FastAPI(title="AccaGenius Complete API with Form Analysis")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

print("✅ AccaGenius Backend with Form Analysis - Ready!")

# =========================
# LEAGUE MAPPINGS (17 LEAGUES)
# =========================
LEAGUES = [
    {"code": "BEL", "name": "Belgium Pro League", "country": "Belgium", "id": 144},
    {"code": "DEN", "name": "Denmark Superliga", "country": "Denmark", "id": 119},
    {"code": "PL", "name": "Premier League", "country": "England", "id": 39},
    {"code": "ELC", "name": "Championship", "country": "England", "id": 40},
    {"code": "EL1", "name": "League One", "country": "England", "id": 41},
    {"code": "EL2", "name": "League Two", "country": "England", "id": 42},
    {"code": "FL1", "name": "Ligue 1", "country": "France", "id": 61},
    {"code": "FL2", "name": "Ligue 2", "country": "France", "id": 62},
    {"code": "BL1", "name": "Bundesliga", "country": "Germany", "id": 78},
    {"code": "BL2", "name": "2. Bundesliga", "country": "Germany", "id": 79},
    {"code": "SA", "name": "Serie A", "country": "Italy", "id": 135},
    {"code": "NED", "name": "Eredivisie", "country": "Netherlands", "id": 88},
    {"code": "CL", "name": "Champions League", "country": "Europe", "id": 2},
    {"code": "POL", "name": "Ekstraklasa", "country": "Poland", "id": 106},
    {"code": "PPL", "name": "Primeira Liga", "country": "Portugal", "id": 94},
    {"code": "PD", "name": "La Liga", "country": "Spain", "id": 140},
    {"code": "TUR", "name": "Süper Lig", "country": "Turkey", "id": 203},
]

LEAGUE_IDS = {league["code"]: league["id"] for league in LEAGUES}

def get_current_season() -> int:
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

# =========================
# HELPER FUNCTIONS - FORM ANALYSIS
# =========================

def get_team_form(team_id: int, league_id: int) -> dict:
    """Get team's last 5 games with detailed stats"""
    try:
        url = f"{BASE_URL}/fixtures"
        params = {
            "team": team_id,
            "league": league_id,
            "season": get_current_season(),
            "last": 5,
            "timezone": "Europe/London"
        }
        
        response = requests.get(url, headers=HEADERS, params=params, timeout=10)
        
        if response.status_code != 200:
            return {"games": 0, "wins": 0, "draws": 0, "losses": 0, "gf": 0, "ga": 0, "form_rating": 1.5}
        
        fixtures = response.json().get("response", [])
        
        wins = 0
        draws = 0
        losses = 0
        goals_for = 0
        goals_against = 0
        
        for fixture in fixtures:
            if fixture["fixture"]["status"]["short"] != "FT":
                continue
            
            home_id = fixture["teams"]["home"]["id"]
            home_goals = fixture["goals"]["home"] or 0
            away_goals = fixture["goals"]["away"] or 0
            
            if home_id == team_id:
                goals_for += home_goals
                goals_against += away_goals
                
                if home_goals > away_goals:
                    wins += 1
                elif home_goals == away_goals:
                    draws += 1
                else:
                    losses += 1
            else:
                goals_for += away_goals
                goals_against += home_goals
                
                if away_goals > home_goals:
                    wins += 1
                elif away_goals == home_goals:
                    draws += 1
                else:
                    losses += 1
        
        games_played = wins + draws + losses
        form_rating = (wins * 3 + draws) / max(games_played, 1) if games_played > 0 else 1.5
        
        return {
            "games": games_played,
            "wins": wins,
            "draws": draws,
            "losses": losses,
            "gf": goals_for,
            "ga": goals_against,
            "form_rating": form_rating
        }
        
    except Exception as e:
        print(f"Form error: {str(e)}")
        return {"games": 0, "wins": 0, "draws": 0, "losses": 0, "gf": 0, "ga": 0, "form_rating": 1.5}


def analyze_and_pick(fixture: dict, home_form: dict, away_form: dict, risk: str) -> Optional[dict]:
    """Analyze form and make intelligent pick"""
    try:
        home_team = fixture["teams"]["home"]["name"]
        away_team = fixture["teams"]["away"]["name"]
        fixture_id = fixture["fixture"]["id"]
        home_id = fixture["teams"]["home"]["id"]
        away_id = fixture["teams"]["away"]["id"]
        
        # Calculate form ratings
        home_rating = home_form.get("form_rating", 1.5)
        away_rating = away_form.get("form_rating", 1.5)
        
        # Home advantage
        home_rating += 0.4
        
        # Goals averages
        home_games = max(home_form.get("games", 1), 1)
        away_games = max(away_form.get("games", 1), 1)
        
        home_gf_avg = home_form.get("gf", 0) / home_games
        away_gf_avg = away_form.get("gf", 0) / away_games
        home_ga_avg = home_form.get("ga", 0) / home_games
        away_ga_avg = away_form.get("ga", 0) / away_games
        
        total_goals_avg = home_gf_avg + away_gf_avg
        
        # DECISION LOGIC
        
        # 1. Clear favorite (form difference > 0.8)
        if abs(home_rating - away_rating) > 0.8:
            if home_rating > away_rating:
                confidence = min(85, 65 + int((home_rating - away_rating) * 12))
                odds = round(1.45 + (away_rating / home_rating) * 0.4, 2)
                return {
                    "id": fixture_id,
                    "home": home_team,
                    "away": away_team,
                    "home_id": home_id,
                    "away_id": away_id,
                    "bet": f"{home_team} Win",
                    "odds": odds,
                    "confidence": confidence,
                    "reasoning": f"{home_team} excellent form ({home_form['wins']}W-{home_form['draws']}D-{home_form['losses']}L, {home_form['gf']} goals) vs {away_team} struggling ({away_form['wins']}W-{away_form['draws']}D-{away_form['losses']}L, {away_form['ga']} conceded)"
                }
            else:
                confidence = min(82, 62 + int((away_rating - home_rating) * 12))
                odds = round(2.10 + (home_rating / away_rating) * 0.6, 2)
                return {
                    "id": fixture_id,
                    "home": home_team,
                    "away": away_team,
                    "home_id": home_id,
                    "away_id": away_id,
                    "bet": f"{away_team} Win",
                    "odds": odds,
                    "confidence": confidence,
                    "reasoning": f"{away_team} dominant form ({away_form['wins']}W-{away_form['draws']}D-{away_form['losses']}L, {away_form['gf']} goals) should overcome {home_team} ({home_form['wins']}W-{home_form['draws']}D-{home_form['losses']}L)"
                }
        
        # 2. High-scoring game (both teams scoring 1.3+ goals avg)
        if total_goals_avg > 2.6:
            confidence = min(78, 55 + int(total_goals_avg * 8))
            return {
                "id": fixture_id,
                "home": home_team,
                "away": away_team,
                "home_id": home_id,
                "away_id": away_id,
                "bet": "Over 2.5 Goals",
                "odds": 1.75,
                "confidence": confidence,
                "reasoning": f"Both teams scoring freely - {home_team} avg {round(home_gf_avg, 1)} goals, {away_team} avg {round(away_gf_avg, 1)} goals per game. High-scoring expected"
            }
        
        # 3. BTTS (both teams scoring 1+ goals avg)
        if home_gf_avg >= 1.0 and away_gf_avg >= 1.0:
            confidence = min(74, 58 + int((home_gf_avg + away_gf_avg) * 6))
            return {
                "id": fixture_id,
                "home": home_team,
                "away": away_team,
                "home_id": home_id,
                "away_id": away_id,
                "bet": "Both Teams to Score",
                "odds": 1.80,
                "confidence": confidence,
                "reasoning": f"Both teams finding net consistently - {home_team} {home_form['gf']} goals, {away_team} {away_form['gf']} goals in last {home_games} games"
            }
        
        # 4. Moderate favorite with home advantage
        if home_rating > away_rating + 0.3:
            confidence = 68
            return {
                "id": fixture_id,
                "home": home_team,
                "away": away_team,
                "home_id": home_id,
                "away_id": away_id,
                "bet": f"{home_team} Win or Draw",
                "odds": 1.35,
                "confidence": confidence,
                "reasoning": f"{home_team} slight edge at home with better recent form ({home_form['wins']}W vs {away_form['wins']}W)"
            }
        
        return None
        
    except Exception as e:
        print(f"Analyze error: {str(e)}")
        return None

# =========================
# ENDPOINTS
# =========================

@app.get("/")
async def root():
    return {
        "status": "AccaGenius API is running",
        "version": "2.0 - Form Analysis",
        "features": ["17 Leagues", "Form-Based AI", "Real Stats", "OpenRouter Chat"]
    }

@app.get("/fixtures/{league_code}")
async def get_fixtures(league_code: str):
    """Get upcoming fixtures for a league"""
    try:
        league_id = LEAGUE_IDS.get(league_code.upper())
        if not league_id:
            raise HTTPException(status_code=404, detail="League not found")
        
        url = f"{BASE_URL}/fixtures"
        params = {
            "league": league_id,
            "season": get_current_season(),
            "next": 20,
            "timezone": "Europe/London"
        }
        
        response = requests.get(url, headers=HEADERS, params=params, timeout=10)
        
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="API Error")
        
        data = response.json()
        fixtures_by_date = {}
        
        for fixture in data.get("response", []):
            date = fixture["fixture"]["date"].split("T")[0]
            
            if date not in fixtures_by_date:
                fixtures_by_date[date] = []
            
            fixtures_by_date[date].append({
                "id": fixture["fixture"]["id"],
                "date": fixture["fixture"]["date"],
                "time": datetime.fromisoformat(fixture["fixture"]["date"].replace("Z", "+00:00")).strftime("%H:%M"),
                "home": fixture["teams"]["home"]["name"],
                "away": fixture["teams"]["away"]["name"],
                "home_id": fixture["teams"]["home"]["id"],
                "away_id": fixture["teams"]["away"]["id"],
                "home_logo": fixture["teams"]["home"]["logo"],
                "away_logo": fixture["teams"]["away"]["logo"],
                "venue": fixture["fixture"]["venue"]["name"],
                "referee": fixture["fixture"]["referee"]
            })
        
        return {"league": league_code, "fixtures": fixtures_by_date}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate-acca")
async def generate_acca(request: AccaRequest):
    """Generate AI-powered accumulator with REAL FORM analysis"""
    try:
        selections = request.selections
        leagues = request.leagues if request.leagues else ["PL", "PD", "BL1", "SA", "FL1"]
        risk = request.risk
        
        all_picks = []
        
        for league_code in leagues:
            league_id = LEAGUE_IDS.get(league_code)
            if not league_id:
                continue
            
            # Get upcoming fixtures
            url = f"{BASE_URL}/fixtures"
            params = {
                "league": league_id,
                "season": get_current_season(),
                "next": 8,
                "timezone": "Europe/London"
            }
            
            fixtures_response = requests.get(url, headers=HEADERS, params=params, timeout=10)
            
            if fixtures_response.status_code != 200:
                continue
            
            fixtures = fixtures_response.json().get("response", [])
            
            for fixture in fixtures[:5]:
                home_team_id = fixture["teams"]["home"]["id"]
                away_team_id = fixture["teams"]["away"]["id"]
                
                # GET REAL FORM DATA
                home_form = get_team_form(home_team_id, league_id)
                away_form = get_team_form(away_team_id, league_id)
                
                # ANALYZE AND PICK
                pick = analyze_and_pick(fixture, home_form, away_form, risk)
                
                if pick and pick["confidence"] >= 60:
                    all_picks.append(pick)
        
        # Sort by confidence
        all_picks.sort(key=lambda x: x["confidence"], reverse=True)
        top_picks = all_picks[:selections]
        
        if not top_picks:
            return {
                "message": "No suitable picks found",
                "total_selections": 0,
                "total_odds": 0,
                "confidence": 0,
                "selections": []
            }
        
        # Calculate total odds
        total_odds = 1.0
        for pick in top_picks:
            total_odds *= pick["odds"]
        
        avg_confidence = sum(p["confidence"] for p in top_picks) / len(top_picks)
        
        return {
            "message": "AI Acca Generated with Form Analysis",
            "total_selections": len(top_picks),
            "total_odds": round(total_odds, 2),
            "confidence": round(avg_confidence),
            "risk_level": risk,
            "selections": top_picks
        }
        
    except Exception as e:
        print(f"Generate acca error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/standings/{league_code}")
async def get_standings(league_code: str):
    """Get league standings"""
    try:
        league_id = LEAGUE_IDS.get(league_code.upper())
        if not league_id:
            raise HTTPException(status_code=404, detail="League not found")
        
        url = f"{BASE_URL}/standings"
        params = {
            "league": league_id,
            "season": get_current_season()
        }
        
        response = requests.get(url, headers=HEADERS, params=params, timeout=10)
        
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="API Error")
        
        data = response.json()
        standings_data = data.get("response", [])[0].get("league", {}).get("standings", [[]])[0]
        
        standings = []
        for team in standings_data:
            standings.append({
                "position": team["rank"],
                "team": team["team"]["name"],
                "logo": team["team"]["logo"],
                "played": team["all"]["played"],
                "won": team["all"]["win"],
                "drawn": team["all"]["draw"],
                "lost": team["all"]["lose"],
                "gf": team["all"]["goals"]["for"],
                "ga": team["all"]["goals"]["against"],
                "gd": team["goalsDiff"],
                "points": team["points"]
            })
        
        return {"league": league_code, "standings": standings}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/live")
async def get_live():
    """Get live matches"""
    try:
        url = f"{BASE_URL}/fixtures"
        params = {"live": "all", "timezone": "Europe/London"}
        
        response = requests.get(url, headers=HEADERS, params=params, timeout=10)
        
        if response.status_code != 200:
            return {"matches": []}
        
        data = response.json()
        live_matches = []
        
        for fixture in data.get("response", [])[:15]:
            live_matches.append({
                "id": fixture["fixture"]["id"],
                "home": fixture["teams"]["home"]["name"],
                "away": fixture["teams"]["away"]["name"],
                "home_score": fixture["goals"]["home"],
                "away_score": fixture["goals"]["away"],
                "minute": fixture["fixture"]["status"]["elapsed"],
                "status": fixture["fixture"]["status"]["long"],
                "league": fixture["league"]["name"]
            })
        
        return {"matches": live_matches}
        
    except Exception as e:
        return {"matches": []}


@app.get("/h2h/{team1_id}/{team2_id}")
async def get_h2h(team1_id: int, team2_id: int):
    """Get head-to-head history"""
    try:
        url = f"{BASE_URL}/fixtures/headtohead"
        params = {
            "h2h": f"{team1_id}-{team2_id}",
            "last": 10,
            "timezone": "Europe/London"
        }
        
        response = requests.get(url, headers=HEADERS, params=params, timeout=10)
        
        if response.status_code != 200:
            return {"matches": []}
        
        data = response.json()
        h2h_matches = []
        
        for fixture in data.get("response", []):
            h2h_matches.append({
                "date": fixture["fixture"]["date"].split("T")[0],
                "home": fixture["teams"]["home"]["name"],
                "away": fixture["teams"]["away"]["name"],
                "home_score": fixture["goals"]["home"],
                "away_score": fixture["goals"]["away"]
            })
        
        return {"matches": h2h_matches}
        
    except Exception as e:
        return {"matches": []}


@app.get("/predictions/{fixture_id}")
async def get_predictions(fixture_id: int):
    """Get AI predictions for fixture"""
    try:
        url = f"{BASE_URL}/predictions"
        params = {"fixture": fixture_id}
        
        response = requests.get(url, headers=HEADERS, params=params, timeout=10)
        
        if response.status_code != 200:
            return None
        
        data = response.json()
        prediction = data.get("response", [{}])[0].get("predictions", {})
        
        return {
            "winner": prediction.get("winner", {}).get("name"),
            "confidence": prediction.get("percent", {}).get("home", "50%"),
            "advice": prediction.get("advice", "No prediction available")
        }
        
    except Exception as e:
        return None


@app.post("/chat")
async def chat(request: dict):
    """
    AI Chat endpoint using OpenRouter (FREE!)
    Multiple AI models: Claude, GPT-4, Llama, etc.
    """
    try:
        message = request.get("message", "")
        
        if not message:
            return {"response": "Please ask a question!"}
        
        # OpenRouter API - FREE tier with multiple AI models
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
            },
            json={
                "model": "meta-llama/llama-3.2-3b-instruct:free",
                "messages": [
                    {
                        "role": "system",
                        "content": "You are AccaGenius AI, an expert football betting analyst with deep knowledge of teams, leagues, form, and betting strategies. You analyze matches using stats, form, head-to-head records, and provide professional betting advice. Be concise, accurate, and helpful. Focus on Premier League, La Liga, Bundesliga, Serie A, and Ligue 1."
                    },
                    {
                        "role": "user",
                        "content": message
                    }
                ]
            },
            timeout=15
        )
        
        if response.status_code == 200:
            data = response.json()
            ai_response = data["choices"][0]["message"]["content"]
            return {"response": ai_response}
        else:
            return {"response": "I'm having trouble connecting right now. Please try again in a moment!"}
            
    except Exception as e:
        print(f"Chat error: {str(e)}")
        return {"response": "I'm temporarily unavailable. Feel free to use the AI Acca Generator in the meantime!"}


# Additional endpoints from original...
@app.get("/saved-accas")
async def get_saved_accas():
    return {"accas": []}

@app.post("/saved-accas")
async def save_acca(request: dict):
    return {"message": "Acca saved"}

@app.delete("/saved-accas")
async def delete_acca():
    return {"message": "Acca deleted"}

@app.get("/odds/{fixture_id}")
async def get_odds(fixture_id: int):
    """Get betting odds for a fixture"""
    try:
        url = f"{BASE_URL}/odds"
        params = {
            "fixture": fixture_id
        }
        
        response = requests.get(url, headers=HEADERS, params=params, timeout=10)
        
        if response.status_code != 200:
            return {"odds": []}
        
        data = response.json()
        bookmakers = data.get("response", [])[0].get("bookmakers", [])
        
        odds_list = []
        for bookmaker in bookmakers[:5]:
            for bet in bookmaker.get("bets", []):
                if bet["name"] == "Match Winner":
                    odds_list.append({
                        "bookmaker": bookmaker["name"],
                        "home": bet["values"][0]["odd"],
                        "draw": bet["values"][1]["odd"] if len(bet["values"]) > 1 else None,
                        "away": bet["values"][2]["odd"] if len(bet["values"]) > 2 else bet["values"][1]["odd"]
                    })
        
        return {"odds": odds_list}
        
    except Exception as e:
        return {"odds": []}
