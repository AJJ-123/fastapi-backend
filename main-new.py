print("♠ ACCAGENIUS BACKEND - API-FOOTBALL PRO ♠")

import os
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# =========================
# API KEYS
# =========================
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "0192e664450828fc0345770b74b75e9f")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "AIzaSyC62FAT55vqRGVDAxgV9f-3rUY2eXngzWc")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# API-Football Pro endpoint
API_FOOTBALL_BASE = "https://v3.football.api-sports.io"

# =========================
# FASTAPI APP
# =========================
app = FastAPI(title="AccaGenius API")

# CORS
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
        print("⚠️ Google Gemini API key not set")
except ImportError:
    gemini_available = False
    print("⚠️ google-generativeai package not installed")

# =========================
# HELPER FUNCTIONS
# =========================
def api_football_get(endpoint: str, params: Dict = None) -> Dict[str, Any]:
    """
    Make request to API-Football Pro
    """
    url = f"{API_FOOTBALL_BASE}/{endpoint}"
    headers = {
        "x-rapidapi-key": API_FOOTBALL_KEY,
        "x-rapidapi-host": "v3.football.api-sports.io"
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data
    except requests.exceptions.RequestException as e:
        print(f"❌ API-Football error: {e}")
        raise HTTPException(status_code=500, detail=f"API error: {str(e)}")

# =========================
# LEAGUE MAPPINGS
# =========================
LEAGUE_IDS = {
    "CL": 2,      # Champions League
    "PL": 39,     # Premier League
    "ELC": 40,    # Championship
    "PD": 140,    # La Liga
    "BL1": 78,    # Bundesliga
    "SA": 135,    # Serie A
    "FL1": 61,    # Ligue 1
    "PPL": 94,    # Primeira Liga
    "DED": 88,    # Eredivisie
}

LEAGUES_DATA = [
    {"code": "CL", "name": "Champions League", "country": "Europe", "flag": "CL", "id": 2},
    {"code": "PL", "name": "Premier League", "country": "England", "flag": "PL", "id": 39},
    {"code": "ELC", "name": "Championship", "country": "England", "flag": "PL", "id": 40},
    {"code": "PD", "name": "La Liga", "country": "Spain", "flag": "PD", "id": 140},
    {"code": "BL1", "name": "Bundesliga", "country": "Germany", "flag": "BL1", "id": 78},
    {"code": "SA", "name": "Serie A", "country": "Italy", "flag": "SA", "id": 135},
    {"code": "FL1", "name": "Ligue 1", "country": "France", "flag": "FL1", "id": 61},
    {"code": "PPL", "name": "Primeira Liga", "country": "Portugal", "flag": "PT", "id": 94},
    {"code": "DED", "name": "Eredivisie", "country": "Netherlands", "flag": "NL", "id": 88},
]

def get_league_id(code: str) -> int:
    """Convert league code to API-Football ID"""
    return LEAGUE_IDS.get(code, 39)  # Default to Premier League

def get_current_season() -> int:
    """Get current season year"""
    now = datetime.now()
    # Football season spans two years, starts in August
    if now.month >= 8:
        return now.year
    else:
        return now.year - 1

# =========================
# HEALTH CHECK
# =========================
@app.get("/")
def root():
    return {
        "status": "online",
        "service": "AccaGenius API",
        "api": "API-Football Pro",
        "version": "2.0"
    }

# =========================
# LEAGUES
# =========================
@app.get("/leagues")
def get_leagues():
    """Return available leagues"""
    return LEAGUES_DATA

# =========================
# FIXTURES
# =========================
@app.get("/fixtures/{competition_code}")
def get_fixtures(competition_code: str):
    """
    Get upcoming fixtures for a league
    """
    try:
        league_id = get_league_id(competition_code)
        season = get_current_season()
        
        # Get fixtures for next 10 days
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
        
        # Group by date
        grouped = {}
        for match in data["response"]:
            fixture = match["fixture"]
            teams = match["teams"]
            
            match_date = fixture["date"][:10]  # YYYY-MM-DD
            
            if match_date not in grouped:
                grouped[match_date] = []
            
            fixture_data = {
                "id": fixture["id"],
                "date": fixture["date"],
                "time": datetime.fromisoformat(fixture["date"].replace('Z', '+00:00')).strftime("%H:%M"),
                "home": teams["home"]["name"],
                "away": teams["away"]["name"],
                "home_logo": teams["home"]["logo"],
                "away_logo": teams["away"]["logo"],
                "venue": fixture["venue"]["name"] if fixture.get("venue") else None,
                "status": fixture["status"]["short"]
            }
            
            # Add odds if available
            if "odds" in match and match["odds"]:
                try:
                    bookmaker = match["odds"][0]
                    bet = bookmaker["bets"][0]
                    values = bet["values"]
                    
                    fixture_data["odds"] = {
                        "home": next((v["odd"] for v in values if v["value"] == "Home"), None),
                        "draw": next((v["odd"] for v in values if v["value"] == "Draw"), None),
                        "away": next((v["odd"] for v in values if v["value"] == "Away"), None),
                    }
                except:
                    pass
            
            grouped[match_date].append(fixture_data)
        
        return {"fixtures": grouped}
        
    except Exception as e:
        print(f"❌ Error fetching fixtures: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# =========================
# STANDINGS
# =========================
@app.get("/standings/{competition_code}")
def get_standings(competition_code: str):
    """
    Get league standings
    """
    try:
        league_id = get_league_id(competition_code)
        season = get_current_season()
        
        params = {
            "league": league_id,
            "season": season
        }
        
        data = api_football_get("standings", params)
        
        if not data.get("response") or not data["response"]:
            return []
        
        # Get the standings
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
        print(f"❌ Error fetching standings: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# =========================
# ODDS
# =========================
@app.get("/odds/{fixture_id}")
def get_odds(fixture_id: int):
    """
    Get odds for a specific fixture
    """
    try:
        params = {"fixture": fixture_id}
        data = api_football_get("odds", params)
        
        if not data.get("response"):
            return {"odds": []}
        
        all_odds = []
        for item in data["response"]:
            for bookmaker in item["bookmakers"]:
                for bet in bookmaker["bets"]:
                    if bet["name"] == "Match Winner":
                        odds_data = {
                            "bookmaker": bookmaker["name"],
                            "home": next((v["odd"] for v in bet["values"] if v["value"] == "Home"), None),
                            "draw": next((v["odd"] for v in bet["values"] if v["value"] == "Draw"), None),
                            "away": next((v["odd"] for v in bet["values"] if v["value"] == "Away"), None),
                        }
                        all_odds.append(odds_data)
        
        return {"odds": all_odds}
        
    except Exception as e:
        print(f"❌ Error fetching odds: {e}")
        return {"odds": []}

# =========================
# LIVE MATCHES
# =========================
@app.get("/live")
def get_live_matches():
    """
    Get live matches
    """
    try:
        data = api_football_get("fixtures", {"live": "all"})
        
        if not data.get("response"):
            return {"matches": []}
        
        live_matches = []
        for match in data["response"][:20]:  # Limit to 20
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
                "status": fixture["status"]["elapsed"],
                "time": f"{fixture['status']['elapsed']}'"
            })
        
        return {"matches": live_matches}
        
    except Exception as e:
        print(f"❌ Error fetching live matches: {e}")
        return {"matches": []}

# =========================
# HEAD TO HEAD
# =========================
@app.get("/h2h/{team1_id}/{team2_id}")
def get_h2h(team1_id: int, team2_id: int):
    """
    Get head-to-head stats
    """
    try:
        params = {
            "h2h": f"{team1_id}-{team2_id}",
            "last": 10
        }
        
        data = api_football_get("fixtures/headtohead", params)
        
        if not data.get("response"):
            return {"matches": []}
        
        h2h_matches = []
        for match in data["response"]:
            fixture = match["fixture"]
            teams = match["teams"]
            goals = match["goals"]
            
            h2h_matches.append({
                "date": fixture["date"][:10],
                "home": teams["home"]["name"],
                "away": teams["away"]["name"],
                "home_score": goals["home"],
                "away_score": goals["away"]
            })
        
        return {"matches": h2h_matches}
        
    except Exception as e:
        print(f"❌ Error fetching H2H: {e}")
        return {"matches": []}

# =========================
# PREDICTIONS (API-FOOTBALL PRO)
# =========================
@app.get("/predictions/{fixture_id}")
def get_predictions(fixture_id: int):
    """
    Get AI predictions from API-Football Pro
    """
    try:
        params = {"fixture": fixture_id}
        data = api_football_get("predictions", params)
        
        if not data.get("response") or not data["response"]:
            return {"prediction": None}
        
        pred = data["response"][0]
        
        return {
            "prediction": {
                "winner": pred["predictions"]["winner"]["name"],
                "win_or_draw": pred["predictions"]["win_or_draw"],
                "under_over": pred["predictions"]["under_over"],
                "goals_home": pred["predictions"]["goals"]["home"],
                "goals_away": pred["predictions"]["goals"]["away"],
                "advice": pred["predictions"]["advice"],
                "percent_home": pred["predictions"]["percent"]["home"],
                "percent_draw": pred["predictions"]["percent"]["draw"],
                "percent_away": pred["predictions"]["percent"]["away"]
            }
        }
        
    except Exception as e:
        print(f"❌ Error fetching predictions: {e}")
        return {"prediction": None}

# =========================
# AI CHAT WITH GEMINI
# =========================
class ChatRequest(BaseModel):
    message: str
    history: Optional[List[Dict[str, str]]] = []

@app.post("/ai-chat-gemini")
async def ai_chat_gemini(request: ChatRequest):
    """
    AI Chat using Google Gemini with web search
    """
    if not gemini_available:
        raise HTTPException(status_code=503, detail="Gemini AI not available")
    
    try:
        # Create model with web search
        model = genai.GenerativeModel(
            'gemini-2.0-flash-exp',
            tools='google_search_retrieval'
        )
        
        # Build conversation history
        chat_history = []
        for msg in request.history[-10:]:  # Last 10 messages
            role = "user" if msg["role"] == "user" else "model"
            chat_history.append({
                "role": role,
                "parts": [msg["content"]]
            })
        
        # Start chat
        chat = model.start_chat(history=chat_history)
        
        # Add context for football betting
        enhanced_message = f"""You are an expert football betting analyst with access to live data and web search.

User question: {request.message}

Provide a helpful, accurate response. Use web search to get the latest information about:
- Current form, injuries, and team news
- Head-to-head records
- Recent match results
- Expert opinions and predictions

Be specific with odds, stats, and recommendations where appropriate."""
        
        # Send message
        response = chat.send_message(enhanced_message)
        
        return {
            "response": response.text,
            "success": True
        }
        
    except Exception as e:
        print(f"❌ Gemini chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# =========================
# STARTUP
# =========================
@app.on_event("startup")
async def startup_event():
    print("=" * 50)
    print("♠ ACCAGENIUS BACKEND STARTED ♠")
    print("=" * 50)
    print(f"✅ API-Football Pro: {'Active' if API_FOOTBALL_KEY else 'Missing Key'}")
    print(f"✅ Google Gemini AI: {'Active' if gemini_available else 'Disabled'}")
    print(f"✅ Season: {get_current_season()}/{get_current_season() + 1}")
    print("=" * 50)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
