# app/schemas/user.py
from sqlalchemy import Column, String, Integer, DateTime, Boolean
from sqlalchemy.sql import func
from app.db.base_class import Base

class User(Base):
    id = Column(Integer, primary_key=True, index=True) # Your internal DB ID
    client_provided_id = Column(String, unique=True, index=True, nullable=True)
    play_games_player_id = Column(String, unique=True, index=True, nullable=False) # Verified PGS Player ID
    google_id = Column(String, unique=True, index=True, nullable=True) # Might be null for these users
    # Optional: store email if you get it during the auth code exchange,
    # but PGS ID is the primary identifier from Play Games.
    # Email might not always be available or could be different from their main Google account email.
    email = Column(String, unique=True, index=True, nullable=True)
    username = Column(String, index=True, nullable=True) # Gamer Tag or display name
    profile_pic_url = Column(String, nullable=True) # From PGS if available
    is_active = Column(Boolean(), default=True)
    is_superuser = Column(Boolean(), default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_login_at = Column(DateTime(timezone=True), onupdate=func.now(), default=func.now())
    hashed_password = Column(String, nullable=True) # Store the hash of the client-generated password
    level = Column(Integer, default=1, nullable=False)
    experience = Column(Integer, default=0, nullable=False)
    words_count = Column(Integer, default=0, nullable=False) # Total words submitted by this user
    # Store Google OAuth refresh token securely if you need long-term offline access to PGS APIs
    # google_refresh_token = Column(String, nullable=True)