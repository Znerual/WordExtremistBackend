# app/core/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    PROJECT_NAME: str = "Word Extremist Backend"
    API_V1_STR: str = "/api/v1"
    POSTGRES_DATABASE_URL: str = "postgresql://postgres:1234@localhost:5432/word_extremist_db"
    GOOGLE_CLIENT_ID: str = "YOUR_GOOGLE_WEB_CLIENT_ID.apps.googleusercontent.com" # From Google Cloud Console

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
    XP_FOR_GAME_WIN: int = 100
    XP_FOR_GAME_LOSS: int = 5
    XP_FOR_GAME_WIN_BY_FORFEIT: int = 10
    # Please set your Gemini API Key in the .env file
    GEMINI_API_KEY: str = "YOUR_GEMINI_API_KEY_HERE" # Added this line

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

@lru_cache()
def get_settings():
    return Settings()

settings = get_settings()

# --- ADD THIS DEBUG PRINT ---
print("--- DEBUG: Loaded Settings ---")
print(f"DATABASE_URL from settings: {settings.POSTGRES_DATABASE_URL}")
print(f"PROJECT_NAME from settings: {settings.PROJECT_NAME}") # Just to see if other .env vars load
print("--- END DEBUG ---")