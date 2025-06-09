# app/core/config.py
import pathlib
import logging
from typing import Dict, List
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

logger = logging.getLogger("app.core.config")  # Logger for this module

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent.parent

class Settings(BaseSettings):
    PROJECT_NAME: str = "Word Extremist Backend"
    API_V1_STR: str = "/api/v1"
    POSTGRES_DATABASE_URL: str = "postgresql://postgres:1234@localhost:5432/word_extremist_db"
    GOOGLE_CLIENT_ID: str = "YOUR_GOOGLE_WEB_CLIENT_ID.apps.googleusercontent.com" # From Google Cloud Console
    MONITORING_SNAPSHOT_INTERVAL_SECONDS: int = 3600  # Default to 1 hour

    STATIC_FILES_BASE_URL: str = "http://10.0.2.2:8000"
    # The local directory path where uploaded files are stored.
    UPLOADS_DIR: pathlib.Path = BASE_DIR / "static" / "uploads"

    GOOGLE_WEB_CLIENT_SECRET: str = "YOUR_WEB_OAUTH_CLIENT_SECRET"
    # You might also need your Play Games Services Project ID here if making direct PGS API calls
    # PLAY_GAMES_PROJECT_ID: str = "YOUR_PGS_PROJECT_ID_NUMERIC"
    REDIRECT_URI: str = "postmessage" # Often "postmessage" for this flow, or a configured one if needed
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_SECRET_KEY: str = "your-super-secret-and-long-random-string-for-jwt-CHANGE-THIS-IMMEDIATELY" 
    JWT_ALGORITHM: str = "HS256"
    
    XP_PER_LEVEL_BASE: int = 100
    XP_PER_LEVEL_MULTIPLIER: float = 1.25  # Adjust this multiplier to change level progression difficulty

    XP_FOR_ROUND_WIN: int = 25
    XP_FOR_ROUND_LOSS: int = 5
    XP_FOR_ROUND_DRAW: int = 10
    XP_FOR_GAME_WIN: int = 100
    XP_FOR_GAME_LOSS: int = 5
    XP_FOR_GAME_DRAW: int = 10
    XP_FOR_GAME_WIN_BY_FORFEIT: int = 10

    MAX_MISTAKES: int = 3 
    GAME_MAX_ROUNDS: int = 3
    DEFAULT_TURN_DURATION_SECONDS: int  = 30
    # Please set your Gemini API Key in the .env file
    GEMINI_API_KEY: str = "YOUR_GEMINI_API_KEY_HERE" # Added this line
    MATCHMAKING_BOT_THRESHOLD_SECONDS: int = 15
    BOT_USERNAMES: Dict[str, List[str]] = {
        "en": ["RoboPlayer", "WordBot", "SyntaxSlayer", "VerbViper", "Lexi-CON", "AI-Opponent"],
        "es": ["PalabraBot", "Jugador-IA", "SintaxSlayer", "VerboVíbora", "Lexi-CON", "Oponente-IA"]
    }

    # --- Constants for Probability Scaling ---
    # For a level 1 player, bot has a 20% chance to make a mistake
    MAX_MISTAKE_PROBABILITY: float = 0.20
    # For a level 30+ player, bot has a 3% chance to make a mistake
    MIN_MISTAKE_PROBABILITY: float = 0.03
    # For a level 1 player, bot has a 10% chance to "time out"
    MAX_TIMEOUT_PROBABILITY: float = 0.10
    # For a level 30+ player, bot has a 1% chance to "time out"
    MIN_TIMEOUT_PROBABILITY: float = 0.01
    # The level at which the bot reaches its minimum probability for errors
    LEVEL_CAP_FOR_SCALING: int = 30

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

@lru_cache()
def get_settings():
    settings_instance = Settings()
    settings_instance.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"Uploads directory set to: {settings_instance.UPLOADS_DIR}")
    return settings_instance

settings = get_settings()
