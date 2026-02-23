print("♠ ACCAGENIUS - COMPLETE PLATFORM BACKEND ♠")

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
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "AIzaSyC62FAT55vqRGVDAxgV9f-3rUY2eXngzWc")

API_FOOTBALL_BASE = "https://v3.football.api-sports.io"

# =========================
# FASTAPI APP
# =========================
app = FastAPI(title="AccaGenius Complete API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# GOOGLE GEMINI AI
# =========================
try:
    import google.generativeai as genai
    if GOOGLE_API_KEY:
        genai.configure(api_key=GOOGLE_API_KEY)
        gemini_available = True
        print("✅ Google Gemini AI initialized")
    else:
        gemini_available = False
except ImportError:
    gemini_available = False

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
    market: str = "winner"  # winner, over25, btts, 1h_over05
    leagues: List[str] = []
    risk: str = "balanced"  # safe, balanced, risky

class ChatRequest(BaseModel):
    message: str
    history: Optional[List[Dict[str, str]]] = []

class SavedAcca(BaseModel):
    name: str
    selections: List[Dict]
    total_odds: float
    stake: Optional[float] = None

# In-memory storage
saved_accas_db = []

# =========================
# API-FOOTBALL HELPER
# =========================
def api_football_get(endpoint: str, params: Dict = None) -> Dict[str, Any]:
    url = f"{API_FOOTBALL_BASE}/{endpoint}"
    headers = {
        "x-rapidapi-key": API_FOOTBALL_KEY,
        "x-rapidapi-host": "v3.football.api-sports.io"
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"❌ API error: {e}")
        raise HTTPException(status_code=500, detail=f"API error: {str(e)}")

# =========================
# ENDPOINTS
# =========================

@app.get("/")
def root():
    return {
        "status": "online",
        "service": "AccaGenius Complete Platform",
        "version": "3.0",
        "features": ["AI Acca Generator", "17 Leagues", "Multiple Markets", "Bookmaker Odds"]
    }

@app.get("/leagues")
def get_leagues():
    """Get all 17 supported leagues"""
    return LEAGUES

@app.get("/fixtures/{league_code}")
def get_fixtures(league_code: str):
    """Get upcoming fixtures for a league"""
    try:
        league_id = LEAGUE_IDS.get(league_code)
        if not league_id:
            raise HTTPException(status_code=404, detail="League not found")
        
        season = get_current_season()
        today = datetime.now()
        end_date = today + timedelta(days=10)
        
        params = {
            "league": league_id,
            "season": season,
            "from": today.strftime("%Y-%m-%d"),
            "to": end_date.strftime("%Y-%m-%d")
        }
        
        data = api_football_get("fixtures", params)
        
        if not data.get("response"):
            return {"fixtures": {}}
        
        grouped = {}
        for match in data["response"]:
            fixture = match["fixture"]
            teams = match["teams"]
            
            match_date = fixture["date"][:10]
            if match_date not in grouped:
                grouped[match_date] = []
            
            grouped[match_date].append({
                "id": fixture["id"],
                "date": fixture["date"],
                "time": datetime.fromisoformat(fixture["date"].replace('Z', '+00:00')).strftime("%H:%M"),
                "home": teams["home"]["name"],
                "away": teams["away"]["name"],
                "home_id": teams["home"]["id"],
                "away_id": teams["away"]["id"],
                "home_logo": teams["home"]["logo"],
                "away_logo": teams["away"]["logo"],
                "venue": fixture["venue"]["name"] if fixture.get("venue") else None,
                "status": fixture["status"]["short"]
            })
        
        return {"fixtures": grouped}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/odds/{fixture_id}")
def get_odds(fixture_id: int):
    """Get all bookmaker odds for a fixture"""
    try:
        params = {"fixture": fixture_id}
        data = api_football_get("odds", params)
        
        if not data.get("response"):
            return {"odds": []}
        
        all_odds = []
        for item in data["response"]:
            for bookmaker in item["bookmakers"][:10]:
                for bet in bookmaker["bets"]:
                    if bet["name"] == "Match Winner":
                        all_odds.append({
                            "bookmaker": bookmaker["name"],
                            "home": next((v["odd"] for v in bet["values"] if v["value"] == "Home"), None),
                            "draw": next((v["odd"] for v in bet["values"] if v["value"] == "Draw"), None),
                            "away": next((v["odd"] for v in bet["values"] if v["value"] == "Away"), None),
                        })
                        break
        
        return {"odds": all_odds}
        
    except Exception as e:
        return {"odds": []}

@app.get("/markets/{fixture_id}")
def get_markets(fixture_id: int):
    """Get all betting markets for a fixture"""
    try:
        params = {"fixture": fixture_id}
        data = api_football_get("odds", params)
        
        if not data.get("response"):
            return {"markets": {}}
        
        markets = {
            "match_winner": [],
            "over_under": [],
            "btts": [],
            "first_half": []
        }
        
        for item in data["response"]:
            for bookmaker in item["bookmakers"][:5]:
                for bet in bookmaker["bets"]:
                    if bet["name"] == "Match Winner":
                        markets["match_winner"].append({
                            "bookmaker": bookmaker["name"],
                            "home": next((v["odd"] for v in bet["values"] if v["value"] == "Home"), None),
                            "draw": next((v["odd"] for v in bet["values"] if v["value"] == "Draw"), None),
                            "away": next((v["odd"] for v in bet["values"] if v["value"] == "Away"), None),
                        })
                    elif "Over/Under" in bet["name"]:
                        for value in bet["values"]:
                            markets["over_under"].append({
                                "bookmaker": bookmaker["name"],
                                "line": value["value"],
                                "odds": value["odd"]
                            })
                    elif "Both Teams Score" in bet["name"]:
                        markets["btts"].append({
                            "bookmaker": bookmaker["name"],
                            "yes": next((v["odd"] for v in bet["values"] if v["value"] == "Yes"), None),
                            "no": next((v["odd"] for v in bet["values"] if v["value"] == "No"), None),
                        })
        
        return {"markets": markets}
        
    except Exception as e:
        return {"markets": {}}

@app.post("/generate-acca")
async def generate_acca(request: AccaRequest):
    """AI-generated accumulator bet"""
    try:
        # Get fixtures from selected leagues
        all_fixtures = []
        leagues_to_use = request.leagues if request.leagues else [l["code"] for l in LEAGUES[:5]]
        
        for league_code in leagues_to_use:
            fixtures_data = get_fixtures(league_code)
            for date, matches in fixtures_data.get("fixtures", {}).items():
                all_fixtures.extend(matches)
        
        if len(all_fixtures) < request.selections:
            raise HTTPException(status_code=400, detail="Not enough fixtures available")
        
        # Simulate AI selection (in production, use real ML model)
        selected = random.sample(all_fixtures, min(request.selections, len(all_fixtures)))
        
        acca_selections = []
        total_odds = 1.0
        
        for match in selected:
            # Simulate odds based on market type
            if request.market == "winner":
                odds = round(random.uniform(1.5, 3.0), 2)
                prediction = random.choice(["home", "away"])
                bet_text = f"{match['home'] if prediction == 'home' else match['away']} to Win"
            elif request.market == "over25":
                odds = round(random.uniform(1.6, 2.2), 2)
                bet_text = f"Over 2.5 Goals"
            elif request.market == "btts":
                odds = round(random.uniform(1.7, 2.1), 2)
                bet_text = f"Both Teams to Score"
            else:
                odds = round(random.uniform(1.5, 2.5), 2)
                bet_text = f"1H Over 0.5 Goals"
            
            total_odds *= odds
            
            acca_selections.append({
                "fixture_id": match["id"],
                "home": match["home"],
                "away": match["away"],
                "bet": bet_text,
                "odds": odds,
                "reasoning": f"Strong form and favorable stats",
                "confidence": random.randint(70, 95)
            })
        
        return {
            "selections": acca_selections,
            "total_odds": round(total_odds, 2),
            "total_selections": len(acca_selections),
            "risk_level": request.risk,
            "confidence": random.randint(65, 85)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/standings/{league_code}")
def get_standings(league_code: str):
    """Get league standings"""
    try:
        league_id = LEAGUE_IDS.get(league_code)
        if not league_id:
            raise HTTPException(status_code=404, detail="League not found")
        
        season = get_current_season()
        params = {"league": league_id, "season": season}
        data = api_football_get("standings", params)
        
        if not data.get("response") or not data["response"]:
            return []
        
        standings_data = data["response"][0]["league"]["standings"][0]
        
        result = []
        for team in standings_data:
            result.append({
                "position": team["rank"],
                "team": team["team"]["name"],
                "logo": team["team"]["logo"],
                "played": team["all"]["played"],
                "won": team["all"]["win"],
                "drawn": team["all"]["draw"],
                "lost": team["all"]["lose"],
                "goalsFor": team["all"]["goals"]["for"],
                "goalsAgainst": team["all"]["goals"]["against"],
                "goalDifference": team["goalsDiff"],
                "points": team["points"],
                "form": team["form"]
            })
        
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/live")
def get_live_matches():
    """Get live matches"""
    try:
        data = api_football_get("fixtures", {"live": "all"})
        
        if not data.get("response"):
            return {"matches": []}
        
        live_matches = []
        for match in data["response"][:20]:
            fixture = match["fixture"]
            teams = match["teams"]
            goals = match["goals"]
            
            live_matches.append({
                "id": fixture["id"],
                "league": match["league"]["name"],
                "home": teams["home"]["name"],
                "away": teams["away"]["name"],
                "home_score": goals["home"],
                "away_score": goals["away"],
                "minute": fixture["status"]["elapsed"]
            })
        
        return {"matches": live_matches}
        
    except Exception as e:
        return {"matches": []}

@app.get("/h2h/{team1_id}/{team2_id}")
def get_h2h(team1_id: int, team2_id: int):
    """Get head to head matches between two teams"""
    try:
        params = {"h2h": f"{team1_id}-{team2_id}", "last": 10}
        data = api_football_get("fixtures/headtohead", params)
        
        matches = []
        if data.get("response"):
            for match in data["response"]:
                fixture = match["fixture"]
                teams = match["teams"]
                goals = match["goals"]
                
                matches.append({
                    "date": fixture["date"],
                    "home": teams["home"]["name"],
                    "away": teams["away"]["name"],
                    "home_score": goals["home"],
                    "away_score": goals["away"]
                })
        
        return {"matches": matches}
        
    except Exception as e:
        print(f"❌ H2H error: {e}")
        return {"matches": []}

@app.get("/predictions/{fixture_id}")
def get_prediction(fixture_id: int):
    """Get AI prediction for a specific fixture"""
    try:
        params = {"fixture": fixture_id}
        data = api_football_get("predictions", params)
        
        if data.get("response") and len(data["response"]) > 0:
            pred_data = data["response"][0]
            predictions = pred_data.get("predictions", {})
            
            return {
                "prediction": {
                    "winner": predictions.get("winner", {}).get("name", "Unknown"),
                    "percent_home": predictions.get("percent", {}).get("home", "0"),
                    "percent_away": predictions.get("percent", {}).get("away", "0"),
                    "goals_home": predictions.get("goals", {}).get("home", "0"),
                    "goals_away": predictions.get("goals", {}).get("away", "0"),
                    "advice": predictions.get("advice", "No prediction available")
                }
            }
        
        return {"prediction": None}
        
    except Exception as e:
        print(f"❌ Prediction error: {e}")
        return {"prediction": None}

@app.post("/save-acca")
def save_acca(acca: SavedAcca):
    """Save an accumulator bet"""
    acca_data = acca.dict()
    acca_data["id"] = len(saved_accas_db) + 1
    acca_data["created_at"] = datetime.now().isoformat()
    saved_accas_db.append(acca_data)
    return {"message": "Acca saved", "id": acca_data["id"]}

@app.get("/saved-accas")
def get_saved_accas():
    """Get all saved accas"""
    return {"accas": saved_accas_db}

@app.delete("/saved-accas/{acca_id}")
def delete_acca(acca_id: int):
    """Delete a saved acca"""
    global saved_accas_db
    saved_accas_db = [a for a in saved_accas_db if a["id"] != acca_id]
    return {"message": "Acca deleted"}

@app.post("/ai-chat")
async def ai_chat(request: ChatRequest):
    """AI chat with Gemini"""
    if not gemini_available:
        raise HTTPException(status_code=503, detail="AI not available")
    
    try:
        model = genai.GenerativeModel('gemini-2.0-flash-exp', tools='google_search_retrieval')
        
        chat_history = []
        for msg in request.history[-10:]:
            role = "user" if msg["role"] == "user" else "model"
            chat_history.append({"role": role, "parts": [msg["content"]]})
        
        chat = model.start_chat(history=chat_history)
        
        enhanced_message = f"""You are an expert football betting analyst.

User question: {request.message}

Provide helpful betting advice based on current stats, form, and news."""
        
        response = chat.send_message(enhanced_message)
        return {"response": response.text, "success": True}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.on_event("startup")
async def startup_event():
    print("=" * 50)
    print("♠ ACCAGENIUS COMPLETE PLATFORM ♠")
    print("=" * 50)
    print(f"✅ 17 Leagues Supported")
    print(f"✅ AI Acca Generator")
    print(f"✅ Multiple Markets")
    print(f"✅ Gemini AI: {'Active' if gemini_available else 'Disabled'}")
    print("=" * 50)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
