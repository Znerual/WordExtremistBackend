# app/core/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    PROJECT_NAME: str = "Word Extremist Backend"
    API_V1_STR: str = "/api/v1"
    DATABASE_URL: str = "postgresql://user:password@host:port/dbname"
    GOOGLE_CLIENT_ID: str = "YOUR_GOOGLE_WEB_CLIENT_ID.apps.googleusercontent.com" # From Google Cloud Console

    GOOGLE_WEB_CLIENT_SECRET: str = "YOUR_WEB_OAUTH_CLIENT_SECRET"
    # You might also need your Play Games Services Project ID here if making direct PGS API calls
    # PLAY_GAMES_PROJECT_ID: str = "YOUR_PGS_PROJECT_ID_NUMERIC"
    REDIRECT_URI: str = "postmessage" # Often "postmessage" for this flow, or a configured one if needed
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

@lru_cache()
def get_settings():
    return Settings()

settings = get_settings()