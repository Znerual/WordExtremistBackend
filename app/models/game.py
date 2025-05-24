# app/models/game.py
from pydantic import BaseModel, Field
from typing import List, Dict, Any

class SentencePromptPublic(BaseModel):
    id: int
    sentence_text: str
    target_word: str
    prompt_text: str

    class Config:
        from_attributes = True

class GameStatePlayer(BaseModel):
    id: int # internal player_id
    name: str
    score: int = 0
    mistakes_in_current_round: int = 0
    words_played: List[str] = []

class GameState(BaseModel):
    game_id: str
    players: Dict[int, GameStatePlayer] # player_id -> PlayerState
    current_player_id: int | None = None
    current_round: int = 1
    max_rounds: int = 3
    sentence_prompt: SentencePromptPublic | None = None
    words_played_this_round_all: List[str] = [] # All unique words in the current round
    is_waiting_for_opponent: bool = False
    # Timers might be managed client-side but server can validate/enforce
    last_action_timestamp: float | None = None
    status: str = "starting"


class PlayerAction(BaseModel):
    action_type: str # "submit_word", "send_emoji"
    payload: Dict[str, Any] | None = None # e.g., {"word": "amazing"} or {"emoji": "THUMBS_UP"}