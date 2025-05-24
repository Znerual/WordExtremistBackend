# app/services/matchmaking_service.py
from typing import Dict, List, Tuple, Set, Any
from app.models.user import UserPublic
import uuid
import time

# In-memory stores for simplicity. For production, use Redis or a DB.
waiting_players: List[UserPublic] = []
active_games: Dict[str, Dict[str, Any]] = {} # game_id -> game_state_object (e.g., GameState Pydantic model)


def is_player_waiting(user_id: int) -> bool:
    """Checks if a player with the given ID is in the waiting pool."""
    return any(player.id == user_id for player in waiting_players)

def add_player_to_matchmaking_pool(player: UserPublic):
    """Adds a UserPublic object to the waiting pool if not already present."""
    if not is_player_waiting(player.id): # Check by ID
        waiting_players.append(player)
        print(f"Player '{player.username}' (ID: {player.id}) added to waiting pool. Pool size: {len(waiting_players)}")

def remove_player_from_matchmaking_pool(user_id: int):
    """Removes a player from the waiting pool by their ID."""
    global waiting_players
    initial_len = len(waiting_players)
    # Create a new list excluding the player to remove
    new_waiting_players = [p for p in waiting_players if p.id != user_id]
    # Assign the new list back (Pythonic way to remove item during iteration potential)
    
    waiting_players = new_waiting_players

    if len(waiting_players) < initial_len:
        print(f"Player ID {user_id} removed from waiting pool.")
    else:
        print(f"Player ID {user_id} not found in waiting pool for removal.")


# --- Modified to return UserPublic objects and only create basic game entry ---
def try_match_players() -> Tuple[str, UserPublic, UserPublic] | None:
    """
    Attempts to match two players from the pool.
    Returns (game_id, player1_obj, player2_obj) if successful, else None.
    Creates a basic game entry in active_games.
    """
    if len(waiting_players) >= 2:
        player1 = waiting_players.pop(0)
        player2 = waiting_players.pop(0)
        game_id = f"game_{uuid.uuid4()}"

        # Initialize only basic game info. Sentence/full state loaded on WS connection.
        active_games[game_id] = {
            "game_id": game_id,
            "players": [player1.id, player2.id], # Store only IDs
            "player_details": { # Store user objects temporarily for easy access on game start
                 player1.id: player1.model_dump(),
                 player2.id: player2.model_dump()
            },
            "status": "matched", # Ready for players to connect via WebSocket
            "created_at": time.time() # Add creation time for potential cleanup
        }
        print(f"Matched game {game_id} for '{player1.username}' (ID: {player1.id}) and '{player2.username}' (ID: {player2.id})")
        return game_id, player1, player2
    return None

def get_game_info(game_id: str) -> Dict | None:
    """Gets the basic game info stored when players were matched."""
    return active_games.get(game_id)

def get_full_game_state(game_id: str) -> Dict | None:
     """ Gets the potentially more detailed game state after it has been initialized. """
     game_info = active_games.get(game_id)
     # Return the 'full_state' if it exists, otherwise the basic info
     return game_info.get("full_state", game_info)


def update_game_state(game_id: str, new_state_dict: Dict):
    """Updates or adds the full game state dictionary."""
    if game_id in active_games:
        # Store the detailed state under a specific key or merge/replace
        active_games[game_id]["full_state"] = new_state_dict
        # Optionally update a 'last_updated' timestamp
        active_games[game_id]["last_updated"] = time.time()
    else:
        print(f"Warning: Attempted to update non-existent game: {game_id}")

def cleanup_game(game_id: str):
    if game_id in active_games:
        print(f"Cleaning up game {game_id}")
        # Perform any other cleanup related to this game if needed
        del active_games[game_id]
