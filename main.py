print("♠ ACCAGENIUS - COMPLETE BACKEND WITH AUTH ♠")

import os
import requests
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
from jose import JWTError, jwt

# =========================
# API KEYS
# =========================
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "0192e664450828fc0345770b74b75e9f")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "AIzaSyC62FAT55vqRGVDAxgV9f-3rUY2eXngzWc")
SECRET_KEY = os.getenv("SECRET_KEY", "accagenius-secret-key-change-in-production-2026")

API_FOOTBALL_BASE = "https://v3.football.api-sports.io"

# =========================
# AUTHENTICATION SETUP
# =========================
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7

# In-memory storage (use database in production!)
users_db = {}
saved_bets_db = {}

# =========================
# FASTAPI APP
# =========================
app = FastAPI(title="AccaGenius API with Auth")

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
# MODELS
# =========================
class UserRegister(BaseModel):
    email: EmailStr
    password: str
    age_confirmed: bool

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class SavedBet(BaseModel):
    fixture_id: int
    home_team: str
    away_team: str
    prediction: str
    odds: Optional[float] = None
    stake: Optional[float] = None

class ChatRequest(BaseModel):
    message: str
    history: Optional[List[Dict[str, str]]] = []

# =========================
# AUTH HELPERS
# =========================
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None or email not in users_db:
            raise HTTPException(status_code=401, detail="Invalid authentication")
        return users_db[email]
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication")

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
        print(f"❌ API-Football error: {e}")
        raise HTTPException(status_code=500, detail=f"API error: {str(e)}")

# =========================
# LEAGUE MAPPINGS
# =========================
LEAGUE_IDS = {
    "CL": 2, "PL": 39, "ELC": 40, "PD": 140,
    "BL1": 78, "SA": 135, "FL1": 61, "PPL": 94, "DED": 88
}

LEAGUES_DATA = [
    {"code": "CL", "name": "Champions League", "country": "Europe", "id": 2},
    {"code": "PL", "name": "Premier League", "country": "England", "id": 39},
    {"code": "ELC", "name": "Championship", "country": "England", "id": 40},
    {"code": "PD", "name": "La Liga", "country": "Spain", "id": 140},
    {"code": "BL1", "name": "Bundesliga", "country": "Germany", "id": 78},
    {"code": "SA", "name": "Serie A", "country": "Italy", "id": 135},
    {"code": "FL1", "name": "Ligue 1", "country": "France", "id": 61},
    {"code": "PPL", "name": "Primeira Liga", "country": "Portugal", "id": 94},
    {"code": "DED", "name": "Eredivisie", "country": "Netherlands", "id": 88},
]

def get_league_id(code: str) -> int:
    return LEAGUE_IDS.get(code, 39)

def get_current_season() -> int:
    now = datetime.now()
    return now.year if now.month >= 8 else now.year - 1

# =========================
# AUTHENTICATION ENDPOINTS
# =========================
@app.post("/register")
def register(user: UserRegister):
    if not user.age_confirmed:
        raise HTTPException(status_code=400, detail="Must be 18+ to register")
    
    if user.email in users_db:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    if len(user.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    
    users_db[user.email] = {
        "email": user.email,
        "password_hash": get_password_hash(user.password),
        "created_at": datetime.utcnow().isoformat(),
        "saved_bets": []
    }
    
    token = create_access_token({"sub": user.email})
    
    return {
        "token": token,
        "user": {
            "email": user.email,
            "created_at": users_db[user.email]["created_at"]
        }
    }

@app.post("/login")
def login(user: UserLogin):
    if user.email not in users_db:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    user_data = users_db[user.email]
    if not verify_password(user.password, user_data["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    token = create_access_token({"sub": user.email})
    
    return {
        "token": token,
        "user": {
            "email": user.email,
            "created_at": user_data["created_at"]
        }
    }

@app.get("/me")
def get_me(current_user: dict = Depends(get_current_user)):
    return {
        "email": current_user["email"],
        "created_at": current_user["created_at"],
        "saved_bets_count": len(current_user["saved_bets"])
    }

# =========================
# SAVED BETS
# =========================
@app.post("/saved-bets")
def save_bet(bet: SavedBet, current_user: dict = Depends(get_current_user)):
    bet_data = bet.dict()
    bet_data["saved_at"] = datetime.utcnow().isoformat()
    bet_data["id"] = len(current_user["saved_bets"]) + 1
    
    current_user["saved_bets"].append(bet_data)
    
    return {"message": "Bet saved successfully", "bet": bet_data}

@app.get("/saved-bets")
def get_saved_bets(current_user: dict = Depends(get_current_user)):
    return {"bets": current_user["saved_bets"]}

@app.delete("/saved-bets/{bet_id}")
def delete_bet(bet_id: int, current_user: dict = Depends(get_current_user)):
    current_user["saved_bets"] = [
        b for b in current_user["saved_bets"] if b["id"] != bet_id
    ]
    return {"message": "Bet deleted successfully"}

# =========================
# PUBLIC ENDPOINTS
# =========================
@app.get("/")
def root():
    return {
        "status": "online",
        "service": "AccaGenius API",
        "version": "2.0-auth",
        "features": ["authentication", "predictions", "odds", "lineups"]
    }

@app.get("/leagues")
def get_leagues():
    return LEAGUES_DATA

@app.get("/fixtures/{competition_code}")
def get_fixtures(competition_code: str):
    try:
        league_id = get_league_id(competition_code)
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
            
            fixture_data = {
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
            }
            
            grouped[match_date].append(fixture_data)
        
        return {"fixtures": grouped}
        
    except Exception as e:
        print(f"❌ Error fetching fixtures: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/standings/{competition_code}")
def get_standings(competition_code: str):
    try:
        league_id = get_league_id(competition_code)
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
        print(f"❌ Error fetching standings: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/predictions/{fixture_id}")
def get_predictions(fixture_id: int):
    try:
        params = {"fixture": fixture_id}
        data = api_football_get("predictions", params)
        
        if not data.get("response") or not data["response"]:
            return {"prediction": None}
        
        pred = data["response"][0]
        
        return {
            "prediction": {
                "winner": pred["predictions"]["winner"]["name"] if pred["predictions"]["winner"] else "Draw",
                "win_or_draw": pred["predictions"]["win_or_draw"],
                "under_over": pred["predictions"]["under_over"],
                "goals_home": pred["predictions"]["goals"]["home"],
                "goals_away": pred["predictions"]["goals"]["away"],
                "advice": pred["predictions"]["advice"],
                "percent_home": int(pred["predictions"]["percent"]["home"].replace("%", "")),
                "percent_draw": int(pred["predictions"]["percent"]["draw"].replace("%", "")),
                "percent_away": int(pred["predictions"]["percent"]["away"].replace("%", ""))
            }
        }
        
    except Exception as e:
        print(f"❌ Error fetching predictions: {e}")
        return {"prediction": None}

@app.get("/odds/{fixture_id}")
def get_odds(fixture_id: int):
    try:
        params = {"fixture": fixture_id}
        data = api_football_get("odds", params)
        
        if not data.get("response"):
            return {"odds": []}
        
        all_odds = []
        for item in data["response"]:
            for bookmaker in item["bookmakers"][:10]:  # Limit to 10 bookmakers
                for bet in bookmaker["bets"]:
                    if bet["name"] == "Match Winner":
                        odds_data = {
                            "bookmaker": bookmaker["name"],
                            "home": next((v["odd"] for v in bet["values"] if v["value"] == "Home"), None),
                            "draw": next((v["odd"] for v in bet["values"] if v["value"] == "Draw"), None),
                            "away": next((v["odd"] for v in bet["values"] if v["value"] == "Away"), None),
                        }
                        all_odds.append(odds_data)
                        break
        
        return {"odds": all_odds}
        
    except Exception as e:
        print(f"❌ Error fetching odds: {e}")
        return {"odds": []}

@app.get("/h2h/{team1_id}/{team2_id}")
def get_h2h(team1_id: int, team2_id: int):
    try:
        params = {"h2h": f"{team1_id}-{team2_id}", "last": 10}
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

@app.get("/live")
def get_live_matches():
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
                "status": fixture["status"]["elapsed"],
                "time": f"{fixture['status']['elapsed']}'"
            })
        
        return {"matches": live_matches}
        
    except Exception as e:
        print(f"❌ Error fetching live matches: {e}")
        return {"matches": []}

@app.post("/ai-chat-gemini")
async def ai_chat_gemini(request: ChatRequest):
    if not gemini_available:
        raise HTTPException(status_code=503, detail="Gemini AI not available")
    
    try:
        model = genai.GenerativeModel('gemini-2.0-flash-exp', tools='google_search_retrieval')
        
        chat_history = []
        for msg in request.history[-10:]:
            role = "user" if msg["role"] == "user" else "model"
            chat_history.append({"role": role, "parts": [msg["content"]]})
        
        chat = model.start_chat(history=chat_history)
        
        enhanced_message = f"""You are an expert football betting analyst with access to live data and web search.

User question: {request.message}

Provide a helpful, accurate response. Use web search to get the latest information about:
- Current form, injuries, and team news
- Head-to-head records
- Recent match results
- Expert opinions and predictions

Be specific with odds, stats, and recommendations where appropriate."""
        
        response = chat.send_message(enhanced_message)
        
        return {"response": response.text, "success": True}
        
    except Exception as e:
        print(f"❌ Gemini chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.on_event("startup")
async def startup_event():
    print("=" * 50)
    print("♠ ACCAGENIUS BACKEND STARTED ♠")
    print("=" * 50)
    print(f"✅ API-Football Pro: Active")
    print(f"✅ Google Gemini AI: {'Active' if gemini_available else 'Disabled'}")
    print(f"✅ Authentication: Enabled")
    print(f"✅ Season: {get_current_season()}/{get_current_season() + 1}")
    print("=" * 50)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
