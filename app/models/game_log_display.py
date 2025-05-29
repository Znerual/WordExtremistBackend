# app/models/game_log_display.py
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class GamePlayerPublic(BaseModel):
    id: int
    game_id: int
    user_id: int
    score: int

    class Config:
        from_attributes = True

class WordSubmissionPublic(BaseModel):
    id: int
    game_id: int
    round_number: int
    user_id: int
    sentence_prompt_id: int
    submitted_word: str
    time_taken_ms: Optional[int] = None
    is_valid: bool
    submission_timestamp: datetime
    creativity_score: Optional[int] = None # <--- ADD THIS LINE

    class Config:
        from_attributes = True

class GamePublic(BaseModel):
    id: int
    matchmaking_game_id: str
    language: str # Added language field
    winner_user_id: Optional[int] = None
    status: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    
    players_association: List[GamePlayerPublic] = [] # For displaying scores
    word_submissions: List[WordSubmissionPublic] = [] # For count, or details if needed

    class Config:
        from_attributes = True