from sqlalchemy import Column, String, Integer, DateTime, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid # For game_uuid if you want one separate from matchmaking game_id

from app.db.base_class import Base

class Game(Base):
    __tablename__ = "games" # Explicitly set table name

    id = Column(Integer, primary_key=True, index=True)
    # This game_uuid can be the one from matchmaking or a new one generated here.
    # For simplicity, we'll assume the matchmaking_service.game_id (string) will be stored here.
    # If you want to use the integer id as the primary key and still store the string game_id:
    matchmaking_game_id = Column(String, unique=True, index=True, nullable=False)
    language = Column(String(2), default="en", nullable=False, index=True) # Added language field

    start_time = Column(DateTime(timezone=True), server_default=func.now())
    end_time = Column(DateTime(timezone=True), nullable=True)
    status = Column(String, default="in_progress") # e.g., "in_progress", "finished", "abandoned"
    
    # Overall game winner (if applicable, can be derived from game_players scores too)
    winner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    winner = relationship("User") # Relationship to the User model

    # Relationships
    players_association = relationship("GamePlayer", back_populates="game", cascade="all, delete-orphan")
    word_submissions = relationship("WordSubmission", back_populates="game", cascade="all, delete-orphan")

class GamePlayer(Base):
    __tablename__ = "game_players" # Explicitly set table name

    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    score = Column(Integer, default=0) # Final score for this player in this game
    # player_order = Column(Integer, nullable=True) # e.g., 1 or 2 if you need to distinguish consistently

    game = relationship("Game", back_populates="players_association")
    user = relationship("User") # Relationship to the User model

    __table_args__ = (UniqueConstraint('game_id', 'user_id', name='_game_user_uc'),)


class WordSubmission(Base):
    __tablename__ = "word_submissions" # Explicitly set table name

    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False) # Link to the specific game instance in our DB
    round_number = Column(Integer, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    sentence_prompt_id = Column(Integer, ForeignKey("sentenceprompts.id"), nullable=False) # sentenceprompts from SentencePrompt table
    
    submitted_word = Column(String, nullable=False)
    time_taken_ms = Column(Integer, nullable=True) # Time in milliseconds for this word attempt
    is_valid = Column(Boolean, nullable=False) # Was the word valid for the prompt?
    submission_timestamp = Column(DateTime(timezone=True), server_default=func.now())
    creativity_score = Column(Integer, nullable=True) # <--- ADD THIS LINE

    game = relationship("Game", back_populates="word_submissions")
    user = relationship("User")
    sentence_prompt = relationship("SentencePrompt") # Relationship to SentencePrompt model