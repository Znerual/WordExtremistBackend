# app/models/game.py
from pydantic import BaseModel, Field
from typing import List, Dict, Any

class SentencePromptPublic(BaseModel):
    id: int
    sentence_text: str
    target_word: str
    prompt_text: str
    difficulty: int
    language: str = Field(default="en", max_length=2)  # ISO 639-1 language code

    class Config:
        from_attributes = True

class GameStatePlayer(BaseModel):
    id: int # internal player_id
    name: str
    score: int = 0
    mistakes_in_current_round: int = 0
    words_played: List[str] = []
    is_bot: bool = False # True if this player is a bot

class GameState(BaseModel):
    game_id: str
    db_game_id: int | None = None # The ID from the 'games' table in the database
    language: str = "en"
    players: Dict[int, GameStatePlayer] # player_id -> PlayerState
    current_player_id: int | None = None
    current_round: int = 1
    max_rounds: int = 3
    sentence_prompt: SentencePromptPublic | None = None
    words_played_this_round_all: List[str] = [] # All unique words in the current round
    is_waiting_for_opponent: bool = False
    # Timers might be managed client-side but server can validate/enforce
    last_action_timestamp: float | None = None
    consecutive_timeouts: int = 0
    status: str = "starting"
    matchmaking_player_order: List[int] = []
    winner_user_id: int | None = None # The player_id of the winner, if any
    turn_duration_seconds: int = Field(default=30, description="Duration of a player's turn in seconds.")
    ready_player_ids: List[int] = Field(default_factory=list, description="List of player IDs who have signaled they are ready for the current round.")
    
class PlayerAction(BaseModel):
    action_type: str # "submit_word", "send_emoji"
    payload: Dict[str, Any] | None = None # e.g., {"word": "amazing"} or {"emoji": "THUMBS_UP"}