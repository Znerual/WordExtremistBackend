# app/services/matchmaking_service.py
from typing import Dict, List, Optional, Tuple, Set, Any
from app.models.game import GameState
from app.models.user import UserPublic
import uuid
import time

# In-memory stores for simplicity. For production, use Redis or a DB.
waiting_players_by_lang: Dict[str, List[UserPublic]] = {}
active_games: Dict[str, Dict[str, Any]] = {} # game_id -> game_state_object (e.g., GameState Pydantic model)

DEFAULT_GAME_LANGUAGE = "en"

def is_player_waiting(user_id: int) -> bool:
    """Checks if a player with the given ID is in the waiting pool."""
    for lang_pool in waiting_players_by_lang.values():
        if any(player.id == user_id for player in lang_pool):
            return True
    return False

def add_player_to_matchmaking_pool(player: UserPublic, requested_language: str | None = None):
    """Adds a UserPublic object to the waiting pool if not already present."""
    lang_key = (requested_language or DEFAULT_GAME_LANGUAGE).lower()
    
    if not waiting_players_by_lang.get(lang_key):
        waiting_players_by_lang[lang_key] = []

    if is_player_waiting(player.id):
        print(f"Player '{player.username}' (ID: {player.id}) is already in a matchmaking pool. Not adding again.")
        return
    
    waiting_players_by_lang[lang_key].append(player)
    print(f"Player '{player.username}' (ID: {player.id}) added to '{lang_key}' waiting pool. Pool size for '{lang_key}': {len(waiting_players_by_lang[lang_key])}")


def remove_player_from_matchmaking_pool(user_id: int):
    """Removes a player from the waiting pool by their ID."""
    global waiting_players_by_lang
    removed = False
    for lang_key in list(waiting_players_by_lang.keys()): # Iterate over keys copy
        initial_len = len(waiting_players_by_lang[lang_key])
        waiting_players_by_lang[lang_key] = [p for p in waiting_players_by_lang[lang_key] if p.id != user_id]
        if len(waiting_players_by_lang[lang_key]) < initial_len:
            removed = True
            print(f"Player ID {user_id} removed from '{lang_key}' waiting pool.")
        if not waiting_players_by_lang[lang_key]: # Cleanup empty list
            del waiting_players_by_lang[lang_key]
    if not removed:
        print(f"Player ID {user_id} not found in any waiting pool for removal.")


# --- Modified to return UserPublic objects and only create basic game entry ---
def try_match_players() -> Tuple[str, UserPublic, UserPublic, str] | None:
    """
    Attempts to match two players from any language pool that has >= 2 players.
    Returns (game_id, player1_obj, player2_obj, game_language) if successful, else None.
    Creates a basic game entry in active_games.
    """
    for lang_key, lang_pool in waiting_players_by_lang.items():
        if len(lang_pool) >= 2:
            player1 = lang_pool.pop(0)
            player2 = lang_pool.pop(0)
            game_id = f"game_{uuid.uuid4().hex[:12]}" # Shorter UUID for readability

            # Basic game state, to be fully initialized by game_service on first connection
            # This structure is now based on the GameState Pydantic model
            active_games[game_id] = GameState(
                game_id=game_id,
                language=lang_key, # Set the game language
                players={}, # Will be populated by game_service
                status="matched", # Ready for players to connect via WebSocket
                created_at=time.time(), # Custom field, not in GameState model, for cleanup
                # Temporary storage for player details until game_service initializes GameStatePlayers
                _temp_player_details={
                    player1.id: player1.model_dump(),
                    player2.id: player2.model_dump()
                },
                _temp_player_ids_ordered=[player1.id, player2.id] # Store order from matchmaking
            )
            print(f"Matched game {game_id} (lang: {lang_key}) for '{player1.username}' vs '{player2.username}'")
            
            if not lang_pool: # Cleanup empty list
                del waiting_players_by_lang[lang_key]
            return game_id, player1, player2, lang_key
    return None


def get_game_info(game_id: str) -> Optional[GameState]:
    """Gets the basic game info stored when players were matched."""
    game_state_obj = active_games.get(game_id)
    if game_state_obj:
        # If you stored it as a Pydantic model directly:
        return game_state_obj
        # If you stored it as a dict and want to return the dict:
        # return game_state_obj.model_dump() # Or just game_state_obj if it's already a dict
    return None

def get_full_game_state(game_id: str) -> Optional[GameState]:
    """ Gets the potentially more detailed game state after it has been initialized. """
    return active_games.get(game_id)


def update_game_state(game_id: str, new_state: GameState): # Expects GameState model
    """Updates the full game state. new_state should be a GameState Pydantic object."""
    if game_id in active_games:
        active_games[game_id] = new_state # Store the Pydantic model directly
        active_games[game_id].last_updated_timestamp = time.time() # Custom field for management
    else:
        print(f"Warning: Attempted to update non-existent game: {game_id}")

def cleanup_game(game_id: str):
    if game_id in active_games:
        print(f"Cleaning up game data for {game_id}")
        del active_games[game_id]
