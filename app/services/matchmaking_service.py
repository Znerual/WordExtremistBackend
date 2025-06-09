# app/services/matchmaking_service.py
import logging
from typing import Dict, List, Optional, Tuple, Set, Any
from app.models.game import GameState, GameStatePlayer
from app.models.user import UserPublic
import random
import uuid
import time
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud import crud_user

logger = logging.getLogger("app.services.matchmaking_service")  # Logger for this module

# In-memory stores for simplicity. For production, use Redis or a DB.
waiting_players_by_lang: Dict[str, List[Tuple[UserPublic, float]]] = {}
active_games: Dict[str, GameState] = {} # game_id -> game_state_object (e.g., GameState Pydantic model)

DEFAULT_GAME_LANGUAGE = "en"

def is_player_waiting(user_id: int) -> bool:
    """Checks if a player with the given ID is in the waiting pool."""
    for lang_pool in waiting_players_by_lang.values():
        if any(player.id == user_id for player, timestamp in lang_pool):
            return True
    return False

def add_player_to_matchmaking_pool(player: UserPublic, requested_language: str | None = None):
    """Adds a UserPublic object to the waiting pool if not already present."""
    lang_key = (requested_language or DEFAULT_GAME_LANGUAGE).lower()
    
    if not waiting_players_by_lang.get(lang_key):
        waiting_players_by_lang[lang_key] = []

    if is_player_waiting(player.id):
        logger.error(f"Player '{player.username}' (ID: {player.id}) is already in a matchmaking pool. Not adding again.")
        return
    
    waiting_players_by_lang[lang_key].append((player, time.time()))  # Store player with current timestamp for potential timeout handling
    logger.info(f"Player '{player.username}' (ID: {player.id}) added to '{lang_key}' waiting pool. Pool size for '{lang_key}': {len(waiting_players_by_lang[lang_key])}")


def remove_player_from_matchmaking_pool(user_id: int):
    """Removes a player from the waiting pool by their ID."""
    global waiting_players_by_lang
    removed = False
    for lang_key in list(waiting_players_by_lang.keys()): # Iterate over keys copy
        initial_len = len(waiting_players_by_lang[lang_key])
        waiting_players_by_lang[lang_key] = [(p, t) for p, t in waiting_players_by_lang[lang_key] if p.id != user_id]
        if len(waiting_players_by_lang[lang_key]) < initial_len:
            removed = True
            logger.info(f"Player ID {user_id} removed from '{lang_key}' waiting pool.")
        if not waiting_players_by_lang[lang_key]: # Cleanup empty list
            del waiting_players_by_lang[lang_key]
    if not removed:
        logger.error(f"Player ID {user_id} not found in any waiting pool for removal.")


# --- Modified to return UserPublic objects and only create basic game entry ---
def try_match_players() -> Tuple[str, UserPublic, UserPublic, str] | None:
    """
    Attempts to match two players from any language pool that has >= 2 players.
    Returns (game_id, player1_obj, player2_obj, game_language) if successful, else None.
    Creates a basic game entry in active_games.
    """
    for lang_key, lang_pool in waiting_players_by_lang.items():
        if len(lang_pool) >= 2:
            player1_tuple = lang_pool.pop(0)
            player2_tuple = lang_pool.pop(0)
            player1, _ = player1_tuple # Unpack tuple
            player2, _ = player2_tuple # Unpack tuple

            game_id = f"game_{uuid.uuid4().hex[:12]}" # Shorter UUID for readability

            p1_gs_player = GameStatePlayer(
                id=player1.id, name=player1.username or f"Player {player1.id}",
                score=0, mistakes_in_current_round=0, level=player1.level, words_played=[]
            )
            p2_gs_player = GameStatePlayer(
                id=player2.id, name=player2.username or f"Player {player2.id}",
                score=0, mistakes_in_current_round=0, level=player2.level, words_played=[]
            )

            # Basic game state, to be fully initialized by game_service on first connection
            # This structure is now based on the GameState Pydantic model
            active_games[game_id] = GameState(
                game_id=game_id,
                language=lang_key, # Set the game language
                players={player1.id : p1_gs_player, player2.id : p2_gs_player },
                status="matched", # Ready for players to connect via WebSocket
                last_action_timestamp=time.time(), # Custom field, not in GameState model, for cleanup
                matchmaking_player_order=[player1.id, player2.id]
            )
            logger.info(f"Matched game {game_id} (lang: {lang_key}) for '{player1.username}' vs '{player2.username}'")
           
            if not lang_pool: # Cleanup empty list
                del waiting_players_by_lang[lang_key]
            return game_id, player1, player2, lang_key
    return None

def create_bot_match(player: UserPublic, lang: str, db: Session) -> Tuple[str, UserPublic]:
    """
    Creates a new game state by matching a human player with a bot.
    Returns the game_id and the customized bot UserPublic object.
    """
    logger.info(f"Creating bot match for player {player.username} in language '{lang}'.")
    
    # 1. Get the bot user template from the DB
    bot_user_template = crud_user.get_or_create_bot_user(db)
    
    # 2. Customize the bot for this specific game with a random name
    bot_names = settings.BOT_USERNAMES.get(lang, settings.BOT_USERNAMES.get("en", ["Bot"]))
    bot_game_user = UserPublic.model_validate(bot_user_template)
    bot_game_user.username = random.choice(bot_names)
    bot_level = max(player.level + random.randint(-5, 5), 1)  # Bot level can vary around the player's level
    
    # 3. Create the GameState object
    game_id = f"game_{uuid.uuid4().hex[:12]}"
    
    human_gs_player = GameStatePlayer(
        id=player.id, name=player.username or f"Player {player.id}",
        is_bot=False, score=0, level=player.level, mistakes_in_current_round=0, words_played=[]
    )
    bot_gs_player = GameStatePlayer(
        id=bot_game_user.id, name=bot_game_user.username,
        is_bot=True, score=0, level=bot_level, mistakes_in_current_round=0, words_played=[]
    )
    
    # Randomize who goes first
    players_in_order = random.sample([player.id, bot_game_user.id], 2)
    
    active_games[game_id] = GameState(
        game_id=game_id,
        language=lang,
        players={player.id: human_gs_player, bot_game_user.id: bot_gs_player},
        status="matched",
        last_action_timestamp=time.time(),
        matchmaking_player_order=players_in_order,
        is_bot_game=True, 
    )
    
    logger.info(f"Created bot match {game_id} for '{player.username}' vs '{bot_game_user.username}' (P1: {players_in_order[0]})")
    
    if lang in waiting_players_by_lang and not waiting_players_by_lang[lang]: # Cleanup empty list
        del waiting_players_by_lang[lang]
    return game_id, bot_game_user



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
        active_games[game_id].last_action_timestamp = time.time() # Custom field for management
    else:
        print(f"Warning: Attempted to update non-existent game: {game_id}")

def cleanup_game(game_id: str):
    if game_id in active_games:
        print(f"Cleaning up game data for {game_id}")
        del active_games[game_id]
