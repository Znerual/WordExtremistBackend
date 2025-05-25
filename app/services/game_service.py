# app/services/game_service.py
from sqlalchemy.orm import Session # For type hinting db session if passed
import time
from typing import Dict, Any, Tuple, List, Literal

from app.models.game import SentencePromptPublic, GameStatePlayer # Pydantic models from your existing files
from app.models.user import UserPublic # For player details from matchmaking
from app.crud import crud_game_content # To fetch new sentences
from app.services.word_validator import validate_word_against_prompt

MAX_MISTAKES = 3
GAME_MAX_ROUNDS = 3 # Example, can be configured

# Define possible event types that game_service can return
GameEventType = Literal[
    "game_started",
    "game_state_reconnect", # For sending full state on reconnect
    "new_round_started",
    "game_over",
    "opponent_turn_ended",
    "opponent_timeout",
    "validation_result",
    "opponent_mistake", # If a mistake doesn't end turn but opponent should know
    "info_message_to_player",
    "error_message_to_player",
    "error_message_broadcast",
    "player_disconnected_inform",
    "emoji_broadcast"
]

class GameEvent:
    def __init__(self, event_type: GameEventType, payload: Dict[str, Any], target_player_id: int | None = None, broadcast: bool = False, exclude_player_id: int | None = None):
        self.type = event_type
        self.payload = payload
        self.target_player_id = target_player_id # If None and not broadcast, might be for acting player
        self.broadcast = broadcast
        self.exclude_player_id = exclude_player_id # For broadcasts

    def to_dict(self): # For sending over WebSocket
        return {"type": self.type, "payload": self.payload}


def initialize_new_game_state(
    game_id: str,
    matchmaking_game_info: Dict[str, Any], # From matchmaking_service.get_game_info()
    initial_sentence_prompt: SentencePromptPublic,
    db: Session # Required if fetching user details, etc. Not strictly needed if matchmaking_info is complete
) -> Tuple[Dict[str, Any], List[GameEvent]]:
    """
    Initializes a brand new game state when both players have connected.
    Returns the initial game state dictionary and a list of game events (typically a game_start event).
    """
    events = []
    original_player_ids = matchmaking_game_info["players"]
    p1_sid = original_player_ids[0]
    p2_sid = original_player_ids[1]

    # Assuming player_details in matchmaking_game_info are dicts from UserPublic.model_dump()
    p1_details_dict = matchmaking_game_info["player_details"][p1_sid]
    p2_details_dict = matchmaking_game_info["player_details"][p2_sid]
    
    p1_gs_player = GameStatePlayer(
        id=p1_sid, name=p1_details_dict.get("username") or f"Player {p1_sid}",
        score=0, mistakes_in_current_round=0, words_played=[]
    ).model_dump() # Use .model_dump() for consistent dict structure
    p2_gs_player = GameStatePlayer(
        id=p2_sid, name=p2_details_dict.get("username") or f"Player {p2_sid}",
        score=0, mistakes_in_current_round=0, words_played=[]
    ).model_dump()

    initial_gs_dict = {
        "game_id": game_id,
        "players": {p1_sid: p1_gs_player, p2_sid: p2_gs_player},
        "status": "in_progress",
        "current_player_id": p1_sid, # P1 from matchmaking starts
        "current_round": 1,
        "max_rounds": GAME_MAX_ROUNDS,
        "sentence_prompt": initial_sentence_prompt.model_dump(),
        "words_played_this_round_all": [],
        "last_action_timestamp": time.time(),
        "matchmaking_player_order": original_player_ids # Store for consistent p1/p2 reference
    }

    game_start_payload = {
        "game_id": game_id,
        "current_sentence": initial_sentence_prompt.sentence_text,
        "prompt": initial_sentence_prompt.prompt_text,
        "word_to_replace": initial_sentence_prompt.target_word,
        "round": initial_gs_dict["current_round"],
        "player1_server_id": str(p1_sid),
        "player2_server_id": str(p2_sid),
        "player1_state": p1_gs_player, # Already a dict
        "player2_state": p2_gs_player, # Already a dict
        "current_player_id": str(initial_gs_dict["current_player_id"]),
        "game_active": True
    }
    events.append(GameEvent(event_type="game_started", payload=game_start_payload, broadcast=True))
    
    return initial_gs_dict, events


def prepare_reconnect_state_payload(game_id: str, current_game_state: Dict[str, Any], target_player_id: int) -> GameEvent:
    """Prepares the payload for a player reconnecting to an existing game."""
    mm_player_order = current_game_state["matchmaking_player_order"]
    p1_id_reconnect = mm_player_order[0]
    p2_id_reconnect = mm_player_order[1]
    
    current_prompt_reconnect = SentencePromptPublic(**current_game_state["sentence_prompt"])
    
    reconnect_payload = {
        "game_id": game_id,
        "current_sentence": current_prompt_reconnect.sentence_text,
        "prompt": current_prompt_reconnect.prompt_text,
        "word_to_replace": current_prompt_reconnect.target_word,
        "round": current_game_state["current_round"],
        "player1_server_id": str(p1_id_reconnect),
        "player2_server_id": str(p2_id_reconnect),
        "player1_state": current_game_state["players"][p1_id_reconnect],
        "player2_state": current_game_state["players"][p2_id_reconnect],
        "current_player_id": str(current_game_state["current_player_id"]),
        "game_active": current_game_state.get("status") == "in_progress"
    }
    return GameEvent(event_type="game_state_reconnect", payload=reconnect_payload, target_player_id=target_player_id)


def _determine_next_player(current_player_id: int, p1_id: int, p2_id: int) -> int:
    return p2_id if current_player_id == p1_id else p1_id

def _prepare_next_round(current_game_state: Dict[str, Any], db: Session, round_winner_of_previous_round: int) -> Tuple[Dict[str, Any], List[GameEvent]]:
    """
    Modifies current_game_state for the next round.
    Returns the modified state and events (e.g., new_round_started).
    Assumes scores for the completed round are already updated in current_game_state.
    """
    events = []
    current_game_state["current_round"] += 1
    
    new_sentence_db = crud_game_content.get_random_sentence_prompt(db)
    if not new_sentence_db:
        error_payload = {"message": "Failed to load game content for the new round."}
        events.append(GameEvent(event_type="error_message_broadcast", payload=error_payload, broadcast=True))
        current_game_state["status"] = "error_content_load" # Mark game as errored
        return current_game_state, events # Return immediately, websockets.py should handle this by closing conns

    new_sentence_pydantic = SentencePromptPublic.model_validate(new_sentence_db)
    current_game_state["sentence_prompt"] = new_sentence_pydantic.model_dump()

    p1_id = current_game_state["matchmaking_player_order"][0]
    p2_id = current_game_state["matchmaking_player_order"][1]

    for pid_reset in [p1_id, p2_id]:
        current_game_state["players"][pid_reset]["mistakes_in_current_round"] = 0
        current_game_state["players"][pid_reset]["words_played"] = []
    current_game_state["words_played_this_round_all"] = []

    # Determine who starts next round (e.g., P1 starts odd, P2 starts even based on matchmaking order)
    current_game_state["current_player_id"] = p1_id if current_game_state["current_round"] % 2 == 1 else p2_id
    current_game_state["status"] = "in_progress" # Ensure status is correct
    current_game_state["last_action_timestamp"] = time.time()


    new_round_payload = {
        "round_winner_id": str(round_winner_of_previous_round),
        "new_round_number": current_game_state["current_round"],
        "player1_server_id": str(p1_id),
        "player2_server_id": str(p2_id),
        "player1_state": current_game_state["players"][p1_id], # Full state for P1
        "player2_state": current_game_state["players"][p2_id], # Full state for P2
        "current_sentence": new_sentence_pydantic.sentence_text,
        "prompt": new_sentence_pydantic.prompt_text,
        "word_to_replace": new_sentence_pydantic.target_word,
        "current_player_id": str(current_game_state["current_player_id"]),
        "game_active": True,
        "last_action_timestamp": current_game_state.get("last_action_timestamp")
    }
    events.append(GameEvent(event_type="new_round_started", payload=new_round_payload, broadcast=True))
    return current_game_state, events


def process_player_game_action(
    current_game_state: Dict[str, Any], # This is the mutable game state from matchmaking_service
    acting_player_id: int,
    action_type: str,
    action_payload: Dict[str, Any],
    db: Session # For fetching new sentences if a round ends
) -> Tuple[Dict[str, Any], List[GameEvent]]:
    """
    Processes a player's action, updates the game state, and returns events.
    Modifies current_game_state IN PLACE.
    """
    events: List[GameEvent] = []
    
    p1_id = current_game_state["matchmaking_player_order"][0]
    p2_id = current_game_state["matchmaking_player_order"][1]

    is_acting_players_turn = current_game_state["current_player_id"] == acting_player_id

    if action_type == "submit_word":
        if not is_acting_players_turn:
            events.append(GameEvent(event_type="error_message_to_player", payload={"message": "Not your turn."}, target_player_id=acting_player_id))
            return current_game_state, events

        word = action_payload.get("word", "").strip().lower()
        if not word:
            events.append(GameEvent(event_type="validation_result", payload={"word": word, "is_valid": False, "message": "Word cannot be empty."}, target_player_id=acting_player_id))
            return current_game_state, events

        # 1. Check for repeated word (mistake)
        if word in current_game_state["words_played_this_round_all"]:
            current_game_state["players"][acting_player_id]["mistakes_in_current_round"] += 1
            mistakes = current_game_state["players"][acting_player_id]["mistakes_in_current_round"]
            current_game_state["last_action_timestamp"] = time.time()
            events.append(GameEvent(event_type="validation_result", payload={"word": word, "is_valid": False, "message": "Word already played. Mistake!"}, target_player_id=acting_player_id))
            
            if mistakes >= MAX_MISTAKES:
                current_game_state, round_game_over_events = _handle_round_or_game_end(current_game_state, acting_player_id, "repeated_word", db)
                events.extend(round_game_over_events)
            # No 'else' needed to update matchmaking_service here; it's done after all processing for this action.
            return current_game_state, events

        # 2. Validate word against prompt
        prompt_obj = SentencePromptPublic(**current_game_state["sentence_prompt"])
        is_valid_replacement = validate_word_against_prompt(
            word, prompt_obj.target_word, prompt_obj.prompt_text, prompt_obj.sentence_text
        )

        if is_valid_replacement:
            current_game_state["players"][acting_player_id]["words_played"].append(action_payload.get("word")) # Store original case
            current_game_state["words_played_this_round_all"].append(word)
            events.append(GameEvent(event_type="validation_result", payload={"word": action_payload.get("word"), "is_valid": True}, target_player_id=acting_player_id))

            # Switch turn
            next_player_id = _determine_next_player(acting_player_id, p1_id, p2_id)
            current_game_state["current_player_id"] = next_player_id
            current_game_state["last_action_timestamp"] = time.time()
            
            opponent_turn_ended_payload = {
                "opponent_player_id": str(acting_player_id), 
                "opponent_played_word": action_payload.get("word"),
                "current_player_id": str(next_player_id),
                #f"player_{p1_id}_score": current_game_state["players"][p1_id]["score"],
                #f"player_{p1_id}_mistakes": current_game_state["players"][p1_id]["mistakes_in_current_round"],
                #f"player_{p2_id}_score": current_game_state["players"][p2_id]["score"],
                #f"player_{p2_id}_mistakes": current_game_state["players"][p2_id]["mistakes_in_current_round"],
                "game_id": current_game_state["game_id"], "current_sentence": prompt_obj.sentence_text,
                #"prompt": prompt_obj.prompt_text, "word_to_replace": prompt_obj.target_word,
                #"round": current_game_state["current_round"], 
                "game_active": True,
                "last_action_timestamp": current_game_state.get("last_action_timestamp")
            }
            events.append(GameEvent(event_type="opponent_turn_ended", payload=opponent_turn_ended_payload, target_player_id=next_player_id))
        
        else: # Invalid word - Mistake
            current_game_state["players"][acting_player_id]["mistakes_in_current_round"] += 1
            mistakes = current_game_state["players"][acting_player_id]["mistakes_in_current_round"]
            current_game_state["last_action_timestamp"] = time.time()
            events.append(GameEvent(event_type="validation_result", payload={"word": word, "is_valid": False, "message": "Not a valid replacement. Mistake!"}, target_player_id=acting_player_id))
            events.append(GameEvent(event_type="opponent_mistake", payload={"player_id": str(acting_player_id), "mistakes": mistakes}, target_player_id=next_player_id))
            if mistakes >= MAX_MISTAKES:
                current_game_state, round_game_over_events = _handle_round_or_game_end(current_game_state, acting_player_id, "invalid_word", db)
                events.extend(round_game_over_events)
        
        return current_game_state, events

    elif action_type == "timeout":
        if is_acting_players_turn:
            current_game_state["players"][acting_player_id]["mistakes_in_current_round"] += 1
            mistakes = current_game_state["players"][acting_player_id]["mistakes_in_current_round"]
            current_game_state["last_action_timestamp"] = time.time()
            #events.append(GameEvent(event_type="info_message_to_player", payload={"message": f"You timed out! Mistake {mistakes}/{MAX_MISTAKES}."}, target_player_id=acting_player_id))

            if mistakes >= MAX_MISTAKES:
                current_game_state, round_game_over_events = _handle_round_or_game_end(current_game_state, acting_player_id, "timeout", db)
                events.extend(round_game_over_events)
            else: # Timeout, mistake, but round continues for other player
                next_player_id = _determine_next_player(acting_player_id, p1_id, p2_id)
                current_game_state["current_player_id"] = next_player_id
                
                # Payload for opponent_turn_ended due to timeout
                timeout_turn_change_payload = {
                    "opponent_player_id": str(acting_player_id), 
                    "current_player_id": str(next_player_id),
                    "game_id": current_game_state["game_id"],
                    "game_active": True,
                    "last_action_timestamp": current_game_state.get("last_action_timestamp")
                }
                events.append(GameEvent(event_type="opponent_timeout", payload=timeout_turn_change_payload, target_player_id=next_player_id))
        else: # Timeout received but not their turn
            print(f"Player {acting_player_id} sent timeout but it's not their turn. Game {current_game_state['game_id']}.")
            # Optionally send an error event to the player who sent the rogue timeout
            # events.append(GameEvent(event_type="error_message_to_player", payload={"message": "Timeout received but not your turn."}, target_player_id=acting_player_id))

        return current_game_state, events

    elif action_type == "send_emoji":
        emoji = action_payload.get("emoji")
        if emoji:
            emoji_payload = {"action": "send_emoji", "emoji": emoji, "sender_id": str(acting_player_id)}
            events.append(GameEvent(event_type="emoji_broadcast", payload=emoji_payload, broadcast=True, exclude_player_id=acting_player_id))
        return current_game_state, events

    else: # Unknown action
        events.append(GameEvent(event_type="error_message_to_player", payload={"message": f"Unknown action type: {action_type}"}, target_player_id=acting_player_id))
        return current_game_state, events


def _handle_round_or_game_end(
    current_game_state: Dict[str, Any], 
    round_loser_id: int, 
    reason: str, 
    db: Session
) -> Tuple[Dict[str, Any], List[GameEvent]]:
    """
    Helper to manage round/game end logic. Modifies current_game_state.
    Returns updated state and list of events.
    """
    events = []
    p1_id = current_game_state["matchmaking_player_order"][0]
    p2_id = current_game_state["matchmaking_player_order"][1]
    round_winner_id = p1_id if round_loser_id == p2_id else p2_id

    current_game_state["players"][round_winner_id]["score"] += 1
    # current_game_state["last_action_timestamp"] = time.time() # Already set by caller

    p1_score = current_game_state["players"][p1_id]["score"]
    p2_score = current_game_state["players"][p2_id]["score"]
    
    print(f"G:{current_game_state['game_id']} R:{current_game_state['current_round']} ended. Loser:{round_loser_id} by {reason}. Winner:{round_winner_id}. Score P1({p1_id}):{p1_score}, P2({p2_id}):{p2_score}")

    max_rounds_val = current_game_state["max_rounds"]
    rounds_needed_to_win = (max_rounds_val // 2) + 1

    game_is_over = False
    if p1_score >= rounds_needed_to_win or \
       p2_score >= rounds_needed_to_win or \
       current_game_state["current_round"] >= max_rounds_val:
        game_is_over = True

    if game_is_over:
        final_winner_id = None
        if p1_score > p2_score: final_winner_id = p1_id
        elif p2_score > p1_score: final_winner_id = p2_id
        
        current_game_state["status"] = "finished"
        
        game_over_payload = {
            "game_winner_id": str(final_winner_id) if final_winner_id else None,
            "player1_server_id": str(p1_id), "player2_server_id": str(p2_id),
            "player1_final_score": p1_score, "player2_final_score": p2_score,
        }
        events.append(GameEvent(event_type="game_over", payload=game_over_payload, broadcast=True))
        print(f"Game {current_game_state['game_id']} Over. Final Winner: {final_winner_id}. Score: {p1_score}-{p2_score}")
    else: # Prepare for next round
        # _prepare_next_round modifies current_game_state and returns it along with its events
        current_game_state, next_round_events = _prepare_next_round(current_game_state, db, round_winner_of_previous_round=round_winner_id)
        events.extend(next_round_events)
        # No need to update status to in_progress, _prepare_next_round does it.
        
    return current_game_state, events


def handle_player_disconnect(
    current_game_state: Dict[str, Any], 
    disconnected_player_id: int, 
    db: Session # For potential new round if game continues by forfeit
) -> Tuple[Dict[str, Any], List[GameEvent]]:
    """
    Handles a player disconnect during an active game.
    - If game was 'in_progress', the other player might win by forfeit.
    - Returns updated game state and events.
    """
    events: List[GameEvent] = []
    if not current_game_state or current_game_state.get("status") != "in_progress":
        # Game wasn't running or already ended, just inform if needed.
        payload = {"player_id": str(disconnected_player_id), "message": "Player disconnected (game not active)."}
        # No broadcast needed if game wasn't active, or only one player was ever connected
        # events.append(GameEvent(event_type="player_disconnected_inform", payload=payload, broadcast=True, exclude_player_id=disconnected_player_id))
        return current_game_state, events

    print(f"G:{current_game_state['game_id']} - P:{disconnected_player_id} disconnected during active game.")
    
    # Inform other player(s)
    disconnect_inform_payload = {"player_id": str(disconnected_player_id)}
    events.append(GameEvent(event_type="player_disconnected_inform", payload=disconnect_inform_payload, broadcast=True, exclude_player_id=disconnected_player_id))

    # Forfeit logic: The disconnected player loses the current round and potentially the game.
    # This is similar to _handle_round_or_game_end but specific to disconnect.
    p1_id = current_game_state["matchmaking_player_order"][0]
    p2_id = current_game_state["matchmaking_player_order"][1]
    
    # The disconnected player is the round_loser_id
    # Update the game state as if the disconnected player lost due to "disconnect_forfeit"
    # This will trigger score updates and game over checks.
    current_game_state, end_events = _handle_round_or_game_end(
        current_game_state, 
        round_loser_id=disconnected_player_id, 
        reason="disconnect_forfeit", 
        db=db
    )
    events.extend(end_events)
    
    if current_game_state["status"] != "finished" and current_game_state["status"] != "error_content_load":
        # If game didn't end, but a new round started, mark it as waiting for the remaining player.
        # This state might be temporary if the server decides to fully end the game.
        # current_game_state["is_waiting_for_opponent"] = True # This field is not in the current GameState model
        pass

    return current_game_state, events