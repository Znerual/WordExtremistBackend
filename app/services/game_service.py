# app/services/game_service.py
import logging
from sqlalchemy.orm import Session # For type hinting db session if passed
import time
from typing import Dict, Any, Tuple, List, Literal

from app.models.game import GameState, SentencePromptPublic
from app.models.validation import WordValidationResult
from app.crud import crud_game_content, crud_user, crud_system # To fetch new sentences
from app.services.word_validator import validate_word_against_prompt
from app.crud import crud_game_log
from app.core.config import settings # For XP and other constants
from app.models.enums import RoundEndReason # For round end reasons

logger = logging.getLogger("app.services.game_service")  # Logger for this module


# Define possible event types that game_service can return
GameEventType = Literal[
    "game_setup_ready", # Tell clients to prepare (animations, etc.)
    "round_started", # Tell clients the round is now active and the timer is running
    "game_state_reconnect", # For sending full state on reconnect
    "new_round_started",
    "game_over",
    "opponent_turn_ended",
    "timeout",
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
    initial_game_state_from_matchmaking: GameState, # From matchmaking_service.get_game_info()
    db: Session # Required if fetching user details, etc. Not strictly needed if matchmaking_info is complete
) -> Tuple[GameState, List[GameEvent]]:
    """
    Initializes a brand new game state when both players have connected.
    Returns the initial game state dictionary and a list of game events (typically a game_start event).
    """
    events = []

    logger.info(f"G:{game_id} - Initializing new game state for players {initial_game_state_from_matchmaking.matchmaking_player_order}")

    p1_id = initial_game_state_from_matchmaking.matchmaking_player_order[0]
    p2_id = initial_game_state_from_matchmaking.matchmaking_player_order[1]
    game_language = initial_game_state_from_matchmaking.language
    
    # Fetch initial sentence prompt for the game's language
    initial_sentence_db = crud_game_content.get_random_sentence_prompt(db, language=game_language)
    if not initial_sentence_db:
        error_msg = f"Failed to load game content for language '{game_language}'. Game cannot start."
        logger.error(f"G:{game_id} - {error_msg}")
        crud_system.create_alert(
            db, 
            level="CRITICAL", 
            message="Game content loading failed", 
            details=f"G:{game_id} - No sentence prompts found for language '{game_language}'. Check the `sentenceprompts` table."
        )
        error_payload = {"message": error_msg}
        error_event = GameEvent(event_type="error_message_broadcast", payload=error_payload, broadcast=True)
        events.append(error_event)
        initial_game_state_from_matchmaking.status = "error_content_load"
      
        return initial_game_state_from_matchmaking, events

    initial_sentence_prompt = SentencePromptPublic.model_validate(initial_sentence_db)

    try:
        db_game_instance = crud_game_log.create_game_record(
            db, 
            matchmaking_game_id=game_id, # Store the string ID from matchmaking
            player1_id=initial_game_state_from_matchmaking.matchmaking_player_order[0],
            player2_id=initial_game_state_from_matchmaking.matchmaking_player_order[1],
            language=game_language # Pass language to DB
        )
        db_game_id_for_logging = db_game_instance.id # The integer PK for logging
        logger.info(f"G:{game_id} - Created database game record with DB ID: {db_game_id_for_logging}")
    except Exception as e:
        logger.exception(f"G:{game_id} - CRITICAL: Failed to create game record in database: {e}")
        # Decide how to handle: proceed without logging or raise error
        db_game_id_for_logging = None # Signal that logging might fail
        # Potentially add an error event
        error_event = GameEvent(
            "error_message_broadcast",
            {"message": "Critical error initializing game records. Game may not be logged."},
            broadcast=True
        )
        # events.append(error_event) # Add to events list if needed

    initial_game_state_from_matchmaking.db_game_id = db_game_id_for_logging # Store for logging later
    initial_game_state_from_matchmaking.status = "waiting_for_ready" # Set initial status
    initial_game_state_from_matchmaking.sentence_prompt = initial_sentence_prompt # Store Pydantic model
    initial_game_state_from_matchmaking.current_player_id= initial_game_state_from_matchmaking.matchmaking_player_order[0] # P1 starts
    initial_game_state_from_matchmaking.current_round = 1
    initial_game_state_from_matchmaking.max_rounds = settings.GAME_MAX_ROUNDS # Set max rounds
    initial_game_state_from_matchmaking.last_action_timestamp = time.time() # Set initial timestamp
    initial_game_state_from_matchmaking.words_played_this_round_all = [] # Reset for new game
    initial_game_state_from_matchmaking.consecutive_timeouts = 0 # Reset timeout counter
    initial_game_state_from_matchmaking.turn_duration_seconds = settings.DEFAULT_TURN_DURATION_SECONDS
    initial_game_state_from_matchmaking.ready_player_ids = []

    logger.info(f"G:{game_id} - STATE CHANGE: 'matched' -> 'waiting_for_ready'.")
    logger.debug(f"G:{game_id} - Fully initialized game state:\n{initial_game_state_from_matchmaking.model_dump_json(indent=2)}")


    game_start_payload = {
        "game_id": game_id,
        "game_language": game_language, # Send game language to client
        "current_sentence": initial_sentence_prompt.sentence_text,
        "prompt": initial_sentence_prompt.prompt_text,
        "word_to_replace": initial_sentence_prompt.target_word,
        "round": initial_game_state_from_matchmaking.current_round,
        "player1_server_id": str(p1_id),
        "player2_server_id": str(p2_id),
        "player1_state": initial_game_state_from_matchmaking.players[p1_id].model_dump(),
        "player2_state": initial_game_state_from_matchmaking.players[p2_id].model_dump(),
        "current_player_id": str(initial_game_state_from_matchmaking.current_player_id),
        "game_active": False,
        "max_rounds": initial_game_state_from_matchmaking.max_rounds,
        "last_action_timestamp": initial_game_state_from_matchmaking.last_action_timestamp,
        "turn_duration_seconds": initial_game_state_from_matchmaking.turn_duration_seconds,
        "game_status": "waiting_for_ready", # Send status to client
    }
    events.append(GameEvent(event_type="game_setup_ready", payload=game_start_payload, broadcast=True))
    
    return initial_game_state_from_matchmaking, events


def prepare_reconnect_state_payload(game_id: str, current_game_state: GameState, target_player_id: int) -> GameEvent:
    """Prepares the payload for a player reconnecting to an existing game."""
    p1_id_reconnect = current_game_state.matchmaking_player_order[0]
    p2_id_reconnect = current_game_state.matchmaking_player_order[1]
    
    # current_game_state.sentence_prompt is already a Pydantic model or None
    current_prompt_reconnect = current_game_state.sentence_prompt
    
    reconnect_payload = {
        "game_id": game_id,
        "game_language": current_game_state.language, # Send game language
        "current_sentence": current_prompt_reconnect.sentence_text if current_prompt_reconnect else "N/A",
        "prompt": current_prompt_reconnect.prompt_text if current_prompt_reconnect else "N/A",
        "word_to_replace": current_prompt_reconnect.target_word if current_prompt_reconnect else "N/A",
        "round": current_game_state.current_round,
        "player1_server_id": str(p1_id_reconnect),
        "player2_server_id": str(p2_id_reconnect),
        "player1_state": current_game_state.players[p1_id_reconnect].model_dump(),
        "player2_state": current_game_state.players[p2_id_reconnect].model_dump(),
        "current_player_id": str(current_game_state.current_player_id) if current_game_state.current_player_id else None,
        "game_active": current_game_state.status == "in_progress",
        "max_rounds": current_game_state.max_rounds,
        "last_action_timestamp": current_game_state.last_action_timestamp,
        "turn_duration_seconds": current_game_state.turn_duration_seconds,
        "game_status": current_game_state.status, # Send status to client
    }
    return GameEvent(event_type="game_state_reconnect", payload=reconnect_payload, target_player_id=target_player_id)


def _determine_next_player(current_player_id: int, p1_id: int, p2_id: int) -> int:
    return p2_id if current_player_id == p1_id else p1_id

def _prepare_next_round(
        current_game_state: GameState, 
        db: Session, 
        round_winner_of_previous_round: int, 
        previous_round_end_reason: RoundEndReason) -> Tuple[GameState, List[GameEvent]]:
    """
    Modifies current_game_state for the next round.
    Returns the modified state and events (e.g., new_round_started).
    Assumes scores for the completed round are already updated in current_game_state.
    """
    events = []
    logger.info(f"G:{current_game_state.game_id} - Preparing Next Round (current is {current_game_state.current_round + 1}).")
    logger.debug(f"G:{current_game_state.game_id} - State before _prepare_next_round:\n{current_game_state.model_dump_json(indent=2)}")

    current_game_state.current_round += 1
    
    new_sentence_db = crud_game_content.get_random_sentence_prompt(db, language=current_game_state.language)
    if not new_sentence_db:
        error_payload = {"message": f"Failed to load game content for language '{current_game_state.language}' for the new round."}
        events.append(GameEvent(event_type="error_message_broadcast", payload=error_payload, broadcast=True))
        current_game_state.status = "error_content_load"
        logger.error(f"G:{current_game_state.game_id} - STATE CHANGE: -> 'error_content_load'. Failed to load new sentence prompt for language '{current_game_state.language}'.")
        return current_game_state, events # Return immediately, websockets.py should handle this by closing conns

    new_sentence_pydantic = SentencePromptPublic.model_validate(new_sentence_db)
    current_game_state.sentence_prompt = new_sentence_pydantic
    logger.info(f"G:{current_game_state.game_id} - Loaded prompt ID {new_sentence_pydantic.id} for new round.")

    p1_id = current_game_state.matchmaking_player_order[0]
    p2_id = current_game_state.matchmaking_player_order[1]

    for pid_reset in [p1_id, p2_id]:
        current_game_state.players[pid_reset].mistakes_in_current_round = 0
        current_game_state.players[pid_reset].words_played = []
    current_game_state.words_played_this_round_all = []

    # Determine who starts next round (e.g., P1 starts odd, P2 starts even based on matchmaking order)
    current_game_state.current_player_id = p1_id if current_game_state.current_round % 2 == 1 else p2_id
    current_game_state.ready_player_ids = []
    current_game_state.last_action_timestamp = time.time()

    old_status = current_game_state.status
    current_game_state.status = "waiting_for_ready"
    logger.info(f"G:{current_game_state.game_id} - STATE CHANGE: '{old_status}' -> 'waiting_for_ready'. Waiting for client_ready action.")
    logger.debug(f"G:{current_game_state.game_id} - State after _prepare_next_round:\n{current_game_state.model_dump_json(indent=2)}")

    new_round_payload = {
        "round_winner_id": str(round_winner_of_previous_round),
        "previous_round_end_reason": previous_round_end_reason.value,
        "new_round_number": current_game_state.current_round,
        "player1_server_id": str(p1_id),
        "player2_server_id": str(p2_id),
        "player1_state": current_game_state.players[p1_id].model_dump(),
        "player2_state": current_game_state.players[p2_id].model_dump(),
        "current_sentence": new_sentence_pydantic.sentence_text,
        "prompt": new_sentence_pydantic.prompt_text,
        "word_to_replace": new_sentence_pydantic.target_word,
        "current_player_id": str(current_game_state.current_player_id),
        "game_active": False,
        "last_action_timestamp": current_game_state.last_action_timestamp,
        "turn_duration_seconds": current_game_state.turn_duration_seconds,
        "game_status": current_game_state.status, # Send status to client
    }
    events.append(GameEvent(event_type="new_round_started", payload=new_round_payload, broadcast=True))
    return current_game_state, events


def process_player_game_action(
    current_game_state: GameState,
    acting_player_id: int,
    action_type: str,
    action_payload: Dict[str, Any],
    db: Session # For fetching new sentences if a round ends
) -> Tuple[GameState, List[GameEvent]]:
    """
    Processes a player's action, updates the game state, and returns events.
    Modifies current_game_state IN PLACE.
    """
    events: List[GameEvent] = []

    game_id = current_game_state.game_id
    round_num = current_game_state.current_round
    logger.info(f"G:{game_id} R:{round_num} P:{acting_player_id} - Received action '{action_type}' with payload: {action_payload}")
    logger.debug(f"G:{game_id} - State before processing action '{action_type}':\n{current_game_state.model_dump_json(indent=2)}")

    crud_user.log_daily_active_user(db, user_id=acting_player_id)
    
    p1_id = current_game_state.matchmaking_player_order[0]
    p2_id = current_game_state.matchmaking_player_order[1]

    is_acting_players_turn = current_game_state.current_player_id == acting_player_id

    db_game_id_for_logging = current_game_state.db_game_id
    current_prompt_details = current_game_state.sentence_prompt # This is now a Pydantic model
    sentence_prompt_db_id = current_prompt_details.id if current_prompt_details else None

    if action_type == "client_ready":
        if current_game_state.status != "waiting_for_ready":
            logger.warning(f"G:{game_id} P:{acting_player_id} - Sent 'client_ready' but game status is '{current_game_state.status}'. Ignoring.")
            return current_game_state, events
        
        if acting_player_id not in current_game_state.ready_player_ids:
            current_game_state.ready_player_ids.append(acting_player_id)
            logger.info(f"G:{game_id} P:{acting_player_id} - Confirmed ready for round {round_num}. Ready players: {len(current_game_state.ready_player_ids)}/{len(current_game_state.players)}")

        # Check if all players are ready
        all_players_ready = len(current_game_state.ready_player_ids) == len(current_game_state.players)
        bot_game_one_ready = len(current_game_state.ready_player_ids) == 1 and current_game_state.is_bot_game
        if all_players_ready or bot_game_one_ready:
            old_status = current_game_state.status
            current_game_state.status = "in_progress"
            logger.info(f"G:{game_id} - All players ready. STATE CHANGE: '{old_status}' -> '{current_game_state.status}' for round {round_num}.")
            
            # Set the starting player for the round (P1 on odd rounds, P2 on even)
            current_game_state.current_player_id = p1_id if current_game_state.current_round % 2 == 1 else p2_id
            current_game_state.last_action_timestamp = time.time()
            logger.info(f"G:{game_id} R:{round_num} - Turn starts for P:{current_game_state.current_player_id}.")

            # Create an event to inform clients the round has officially started and the timer is running
            round_started_payload = {
                "game_id": current_game_state.game_id,
                "round": current_game_state.current_round,
                "current_player_id": str(current_game_state.current_player_id),
                "last_action_timestamp": current_game_state.last_action_timestamp,
                "turn_duration_seconds": current_game_state.turn_duration_seconds,
            }
            events.append(GameEvent("round_started", round_started_payload, broadcast=True))
        return current_game_state, events

    elif action_type == "submit_word":
        if not is_acting_players_turn:
            logger.warning(f"G:{game_id} P:{acting_player_id} - Tried to submit word but it's not their turn (current turn: P:{current_game_state.current_player_id}). Ignoring.")
            events.append(GameEvent(event_type="error_message_to_player", payload={"message": "Not your turn."}, target_player_id=acting_player_id))
            return current_game_state, events

        word = action_payload.get("word", "").strip().lower()

        time_taken_ms = int((time.time() - (current_game_state.last_action_timestamp or time.time())) * 1000)
        current_game_state.last_action_timestamp = time.time()

        if not word:
            logger.warning(f"G:{game_id} P:{acting_player_id} - Submitted an empty word. This should be handled client-side, but rejecting.")
            if db_game_id_for_logging and sentence_prompt_db_id:
                try:
                    crud_game_log.log_word_submission(
                        db, game_db_id=db_game_id_for_logging, round_number=current_game_state.current_round,
                        user_id=acting_player_id, sentence_prompt_id=sentence_prompt_db_id,
                        submitted_word=word, time_taken_ms=time_taken_ms, is_valid=False
                    )
                except Exception as log_e:
                    logger.exception(f"G:{game_id} - Error logging empty word submission: {log_e}")
            events.append(GameEvent(event_type="validation_result", payload={"word": word, "is_valid": False, "message": "Word cannot be empty."}, target_player_id=acting_player_id))
            logger.warning(f"Player {acting_player_id} submitted an empty word in game {current_game_state.game_id}.")
            return current_game_state, events

        # 1. Check for repeated word (mistake)
        if word in current_game_state.words_played_this_round_all:
            current_game_state.players[acting_player_id].mistakes_in_current_round += 1
            mistakes = current_game_state.players[acting_player_id].mistakes_in_current_round
            current_game_state.last_action_timestamp = time.time()
            if db_game_id_for_logging and sentence_prompt_db_id:
                try:
                    crud_game_log.log_word_submission(
                        db, game_db_id=db_game_id_for_logging, round_number=current_game_state.current_round,
                        user_id=acting_player_id, sentence_prompt_id=sentence_prompt_db_id,
                        submitted_word=word, time_taken_ms=time_taken_ms, is_valid=False # Repeated is not "valid" in context of game rules
                    )
                except Exception as log_e:
                    logger.exception(f"G:{game_id} - Error logging repeated word submission: {log_e}")
            logger.info(f"G:{game_id} R:{round_num} P:{acting_player_id} - MISTAKE: Submitted a repeated word '{word}'. Mistake count: {mistakes}")
            events.append(GameEvent(event_type="validation_result", payload={"word": word, "is_valid": False, "message": "Word already played. Mistake!"}, target_player_id=acting_player_id))
            
            if mistakes >= settings.MAX_MISTAKES:
                logger.info(f"G:{game_id} R:{round_num} P:{acting_player_id} - Reached max mistakes ({mistakes}) for repeated words. Ending round.")
                current_game_state, round_game_over_events = _handle_round_or_game_end(current_game_state, acting_player_id, RoundEndReason.REPEATED_WORD_settings.MAX_MISTAKES, db)
                events.extend(round_game_over_events)
    
            # No 'else' needed to update matchmaking_service here; it's done after all processing for this action.
            return current_game_state, events

        # 2. Validate word against prompt
        validation_result, gemini_latency = validate_word_against_prompt(
            db=db, word=word, sentence_prompt_id=sentence_prompt_db_id,
            target_word=current_prompt_details.target_word,
            prompt_text=current_prompt_details.prompt_text,
            sentence_text=current_prompt_details.sentence_text,
            language=current_game_state.language # Pass game language to validator
        )

        if not validation_result.from_cache and db_game_id_for_logging and sentence_prompt_db_id:
            try: crud_game_log.log_word_submission(
                db=db, 
                game_db_id=db_game_id_for_logging, 
                round_number=current_game_state.current_round, 
                user_id=acting_player_id, 
                sentence_prompt_id=sentence_prompt_db_id, 
                submitted_word=word, 
                time_taken_ms=time_taken_ms, 
                is_valid=validation_result.is_valid, 
                creativity_score=validation_result.creativity_score,
                validation_latency_ms=gemini_latency)
            except Exception as log_e: logger.exception(f"G:{game_id} - Error logging new word submission (validity: {validation_result.is_valid}): {log_e}")

        if validation_result.is_valid:
            logger.info(f"G:{game_id} R:{round_num} P:{acting_player_id} - Submitted VALID word '{word}' (Creativity: {validation_result.creativity_score}).")
            updated_player_after_word_count = crud_user.increment_user_words_count(db, user_id=acting_player_id)
            if updated_player_after_word_count:
                # Optionally, if you wanted to send this specific update to clients, you could.
                # For now, the count will be reflected next time user profile is fetched (e.g., on LauncherActivity onResume).
                # Or if you had level/xp/words_count in GameStatePlayer, you would update it here.
                logger.debug(f"P:{acting_player_id} new words_count: {updated_player_after_word_count.words_count}")

            current_game_state.players[acting_player_id].words_played.append(action_payload.get("word")) # Original case
            current_game_state.words_played_this_round_all.append(word)
            events.append(GameEvent(event_type="validation_result", payload={"word": action_payload.get("word"), "is_valid": True, "creativity_score": validation_result.creativity_score}, target_player_id=acting_player_id))
            
            next_player_id = _determine_next_player(acting_player_id, p1_id, p2_id)
            current_game_state.current_player_id = next_player_id
            logger.info(f"G:{game_id} R:{round_num} - Turn ended for P:{acting_player_id}. New turn for P:{next_player_id}.")
            opponent_turn_ended_payload = {
                "opponent_player_id": str(acting_player_id), "opponent_played_word": action_payload.get("word"),
                "creativity_score": validation_result.creativity_score, "current_player_id": str(next_player_id),
                "game_id": current_game_state.game_id, "current_sentence": current_prompt_details.sentence_text,
                "game_active": True, "last_action_timestamp": current_game_state.last_action_timestamp
            }
            events.append(GameEvent(event_type="opponent_turn_ended", payload=opponent_turn_ended_payload, target_player_id=next_player_id))
            logger.debug(f"Player {acting_player_id} submitted valid word '{word}' in game {current_game_state.game_id}. Now it's Player {next_player_id}'s turn.")
        else: # Invalid word
            current_game_state.players[acting_player_id].mistakes_in_current_round += 1
            mistakes = current_game_state.players[acting_player_id].mistakes_in_current_round
            other_player_id = _determine_next_player(acting_player_id, p1_id, p2_id)
            
            logger.info(f"G:{game_id} R:{round_num} P:{acting_player_id} - MISTAKE: Submitted INVALID word '{word}'. Reason: {validation_result.error_message}. Mistake count: {mistakes}")
            events.append(GameEvent(event_type="validation_result", payload={"word": word, "is_valid": False, "message": validation_result.error_message or "Not valid. Mistake!", "creativity_score": validation_result.creativity_score}, target_player_id=acting_player_id))
            events.append(GameEvent(event_type="opponent_mistake", payload={"player_id": str(acting_player_id), "mistakes": mistakes}, target_player_id=other_player_id))
           
            if mistakes >= settings.MAX_MISTAKES:
                logger.info(f"G:{game_id} R:{round_num} P:{acting_player_id} - Reached max mistakes ({mistakes}) for invalid words. Ending round.")
                current_game_state, round_game_over_events = _handle_round_or_game_end(current_game_state, acting_player_id, RoundEndReason.INVALID_WORD_settings.MAX_MISTAKES, db)
                events.extend(round_game_over_events)
                
        return current_game_state, events


    elif action_type == "timeout":
        if not is_acting_players_turn:
            logger.warning(f"G:{game_id} R:{round_num} P:{acting_player_id} - Sent timeout but not their turn (current: P:{current_game_state.current_player_id}). Ignoring.")
            return current_game_state, events
        
        logger.info(f"G:{game_id} R:{round_num} P:{acting_player_id} - Timed out.")
        current_game_state.consecutive_timeouts += 1
        current_game_state.players[acting_player_id].mistakes_in_current_round += 1
        mistakes = current_game_state.players[acting_player_id].mistakes_in_current_round
        current_game_state.last_action_timestamp = time.time()
        logger.info(f"G:{game_id} R:{round_num} P:{acting_player_id} - Mistake count now {mistakes}. Consecutive timeouts: {current_game_state.consecutive_timeouts}.")

        if current_game_state.consecutive_timeouts >= 2:
            logger.info(f"G:{game_id} - Double timeout detected! Ending round.")
            p1_words = len(current_game_state.players[p1_id].words_played)
            p2_words = len(current_game_state.players[p2_id].words_played)
            determined_round_loser_id: int
            if p1_words > p2_words:
                determined_round_loser_id = p2_id
                logger.info(f"G:{game_id} - P1 ({p1_id}) wins double timeout round with {p1_words} words vs P2 ({p2_id}) {p2_words} words.")
            elif p2_words > p1_words:
                determined_round_loser_id = p1_id
                logger.info(f"G:{game_id} - P2 ({p2_id}) wins double timeout round with {p2_words} words vs P1 ({p1_id}) {p1_words} words.")
            else:
                determined_round_loser_id = None
                logger.info(f"G:{game_id} - Words tied ({p1_words}) in double timeout. Round is a draw.")

            current_game_state, round_game_over_events = _handle_round_or_game_end(current_game_state, determined_round_loser_id, RoundEndReason.DOUBLE_TIMEOUT, db) # <--- USE ENUM
            events.extend(round_game_over_events)
            return current_game_state, events
        
        if mistakes >= settings.MAX_MISTAKES:
            logger.info(f"G:{game_id} R:{round_num} P:{acting_player_id} - Reached max mistakes ({mistakes}) from timeouts. Ending round.")
            current_game_state, round_game_over_events = _handle_round_or_game_end(current_game_state, acting_player_id, RoundEndReason.TIMEOUT_MAX_MISTAKES, db) # Changed reason
            events.extend(round_game_over_events)
        else:
            next_player_id = _determine_next_player(acting_player_id, p1_id, p2_id)
            current_game_state.current_player_id = next_player_id
            logger.info(f"G:{game_id} R:{round_num} - Turn ended for P:{acting_player_id} due to timeout. New turn for P:{next_player_id}.")
            timeout_turn_change_payload = {
                "player_id": str(acting_player_id), "current_player_id": str(next_player_id),
                "game_id": current_game_state.game_id, "game_active": True,
                "last_action_timestamp": current_game_state.last_action_timestamp
            }
            events.append(GameEvent(event_type="timeout", payload=timeout_turn_change_payload, broadcast=True))
        return current_game_state, events

    elif action_type == "send_emoji":
        emoji = action_payload.get("emoji")
        if emoji:
            emoji_payload = {"emoji": emoji, "sender_id": str(acting_player_id)}
            next_player_id = _determine_next_player(acting_player_id, p1_id, p2_id)
            events.append(GameEvent(event_type="emoji_broadcast", payload=emoji_payload, broadcast=False, target_player_id=next_player_id)) # Keep exclude for sender
            logger.debug(f"G:{game_id} P:{acting_player_id} - Sent emoji '{emoji}'. Broadcasting.")
            if db_game_id_for_logging:
                crud_game_log.increment_emojis_sent(db, game_db_id=db_game_id_for_logging, user_id=acting_player_id)
        else:
            logger.warning(f"G:{game_id} P:{acting_player_id} - Sent empty emoji. Ignoring.")
        return current_game_state, events

    else:
        events.append(GameEvent(event_type="error_message_to_player", payload={"message": f"Unknown action type: {action_type}"}, target_player_id=acting_player_id))
        logger.error(f"G:{game_id} P:{acting_player_id} - Sent unknown action type '{action_type}'.")
        return current_game_state, events

def _handle_round_or_game_end(
    current_game_state: GameState, 
    round_loser_id: int|None, 
    reason: RoundEndReason, 
    db: Session
) -> Tuple[GameState, List[GameEvent]]:
    """
    Helper to manage round/game end logic. Modifies current_game_state.
    Returns updated state and list of events.
    """
    events = []

    events = []
    game_id = current_game_state.game_id
    round_num = current_game_state.current_round

    logger.info(f"G:{game_id} R:{round_num} - Round ending. Loser ID: {round_loser_id}, Reason: {reason.value}")
    logger.debug(f"G:{game_id} - State before _handle_round_or_game_end:\n{current_game_state.model_dump_json(indent=2)}")

    current_game_state.consecutive_timeouts = 0
    p1_id = current_game_state.matchmaking_player_order[0]
    p2_id = current_game_state.matchmaking_player_order[1]


    round_winner_id: int | None = None
    if round_loser_id is not None:
        round_winner_id = p1_id if round_loser_id == p2_id else p2_id

    if round_winner_id and round_loser_id:
        current_game_state.players[round_winner_id].score += 1
        crud_user.add_experience_to_user(db, user_id=round_winner_id, exp_to_add=settings.XP_FOR_ROUND_WIN)
        crud_user.add_experience_to_user(db, user_id=round_loser_id, exp_to_add=settings.XP_FOR_ROUND_LOSS)
    else: # Draw
        crud_user.add_experience_to_user(db, user_id=p1_id, exp_to_add=settings.XP_FOR_ROUND_DRAW)
        crud_user.add_experience_to_user(db, user_id=p2_id, exp_to_add=settings.XP_FOR_ROUND_DRAW)
        
      

    # current_game_state.last_action_timestamp is usually set by the calling function before this

    p1_score = current_game_state.players[p1_id].score
    p2_score = current_game_state.players[p2_id].score
    db_game_id_for_logging = current_game_state.db_game_id
    if db_game_id_for_logging:
        try:
            # Update both players' scores after a round win
            crud_game_log.update_game_player_score(db, game_db_id=db_game_id_for_logging, user_id=p1_id, new_score=p1_score)
            crud_game_log.update_game_player_score(db, game_db_id=db_game_id_for_logging, user_id=p2_id, new_score=p2_score)
        except Exception as log_e:
            logger.exception(f"G:{game_id} - Error updating player scores in DB: {log_e}")

    logger.info(f"G:{game_id} R:{round_num} - Round ended. Loser:{round_loser_id} by {reason.value}. Winner:{round_winner_id}. Score P1({p1_id}):{p1_score}, P2({p2_id}):{p2_score}")

    max_rounds_val = current_game_state.max_rounds
    rounds_needed_to_win = (max_rounds_val // 2) + 1
    game_is_over = p1_score >= rounds_needed_to_win or p2_score >= rounds_needed_to_win or current_game_state.current_round >= max_rounds_val

    if game_is_over:
        final_winner_id = p1_id if p1_score > p2_score else (p2_id if p2_score > p1_score else None)
        
        logger.info(f"G:{game_id} - Game is over. Final Winner ID: {final_winner_id}. Score: {p1_score}-{p2_score}")

        final_loser_id = None
        if final_winner_id:
            final_loser_id = p2_id if final_winner_id == p1_id else p1_id
            crud_user.add_experience_to_user(db, user_id=final_winner_id, exp_to_add=settings.XP_FOR_GAME_WIN)
            if final_loser_id:
                 crud_user.add_experience_to_user(db, user_id=final_loser_id, exp_to_add=settings.XP_FOR_GAME_LOSS)
        else:
            crud_user.add_experience_to_user(db, user_id=p2_id, exp_to_add=settings.XP_FOR_GAME_DRAW)
            crud_user.add_experience_to_user(db, user_id=p1_id, exp_to_add=settings.XP_FOR_GAME_DRAW)

        old_status = current_game_state.status
        current_game_state.status = "finished"
        current_game_state.winner_user_id = final_winner_id
        logger.info(f"G:{game_id} - STATE CHANGE: '{old_status}' -> '{current_game_state.status}'.")

        if db_game_id_for_logging:
            try: 
                crud_game_log.finalize_game_record(
                    db, 
                    game_db_id=db_game_id_for_logging, 
                    winner_user_id=final_winner_id, 
                    status="finished",
                    reason=reason.value,
                )
            except Exception as log_e: 
                logger.exception(f"G:{game_id} - Error finalizing game DB record: {log_e}")

        game_over_reason = reason
        if reason not in [RoundEndReason.DOUBLE_TIMEOUT, RoundEndReason.OPPONENT_DISCONNECTED] and game_is_over:
            game_over_reason = RoundEndReason.MAX_ROUNDS_REACHED_OR_SCORE_LIMIT


        game_over_payload = {
            "game_winner_id": str(final_winner_id) if final_winner_id else None,
            "player1_server_id": str(p1_id), "player2_server_id": str(p2_id),
            "player1_final_score": p1_score, "player2_final_score": p2_score,
            "reason": game_over_reason.value
        }
        events.append(GameEvent(event_type="game_over", payload=game_over_payload, broadcast=True))
        logger.debug(f"G:{game_id} - Final state at game over:\n{current_game_state.model_dump_json(indent=2)}")
    else:
        current_game_state, next_round_events = _prepare_next_round(current_game_state, db, round_winner_id, reason)
        events.extend(next_round_events)
        
    return current_game_state, events


def handle_player_disconnect(
    current_game_state: GameState, 
    disconnected_player_id: int, 
    db: Session # For potential new round if game continues by forfeit
) -> Tuple[GameState, List[GameEvent]]:
    """
    Handles a player disconnect during an active game.
    - If game was 'in_progress', the other player might win by forfeit.
    - Returns updated game state and events.
    """
    events: List[GameEvent] = []
    game_id = current_game_state.game_id

    if not current_game_state or current_game_state.status not in ["in_progress", "waiting_for_ready"]:
        logger.info(f"G:{game_id} P:{disconnected_player_id} - Disconnected, but game status is '{current_game_state.status}'. No disconnect logic run.")
        return current_game_state, events
   

    logger.info(f"G:{game_id} P:{disconnected_player_id} - Disconnected during active game (status: '{current_game_state.status}').")
    
    db_game_id_for_logging = current_game_state.db_game_id
    forfeit_winner_id = None
    
    p1_id = current_game_state.matchmaking_player_order[0]
    p2_id = current_game_state.matchmaking_player_order[1]
    forfeit_winner_id = p1_id if disconnected_player_id == p2_id else p2_id

    crud_user.add_experience_to_user(
        db, user_id=forfeit_winner_id, exp_to_add=settings.XP_FOR_GAME_WIN_BY_FORFEIT
    )
    
    old_status = current_game_state.status
    current_game_state.status = "abandoned_by_player"
    current_game_state.winner_user_id = forfeit_winner_id
    logger.info(f"G:{game_id} - STATE CHANGE: '{old_status}' -> '{current_game_state.status}'. Winner by forfeit: P:{forfeit_winner_id}")


    if db_game_id_for_logging:
        try:
            crud_game_log.finalize_game_record(
                db, 
                game_db_id=db_game_id_for_logging, 
                winner_user_id=forfeit_winner_id, 
                status="abandoned_by_player",
                reason=RoundEndReason.OPPONENT_DISCONNECTED.value)
            logger.info(f"G:{game_id} (DB ID: {db_game_id_for_logging}) - Marked as 'abandoned' in database.")
        except Exception as log_e: 
            logger.exception(f"G:{game_id} - Error marking game abandoned in DB: {log_e}")
    
    # Inform other player(s)
    disconnect_inform_payload = {
        "player_id": str(disconnected_player_id),
        "message": "Opponent disconnected. You win by forfeit.", # More explicit message
        "game_winner_id": str(forfeit_winner_id) if forfeit_winner_id else None
    }
    # Send only to the remaining player if game is abandoned
    remaining_player_id = forfeit_winner_id 
    if remaining_player_id and remaining_player_id != disconnected_player_id:
         events.append(GameEvent(event_type="player_disconnected_inform", payload=disconnect_inform_payload, target_player_id=remaining_player_id))
    
    p1_final_score = current_game_state.players[p1_id].score
    p2_final_score = current_game_state.players[p2_id].score
    
    game_over_by_disconnect_payload = {
        "game_winner_id": str(forfeit_winner_id) if forfeit_winner_id else None,
        "player1_server_id": str(p1_id),
        "player2_server_id": str(p2_id),
        "player1_final_score": p1_final_score, # Use current scores
        "player2_final_score": p2_final_score,
        "reason": RoundEndReason.OPPONENT_DISCONNECTED.value
    }
    # This event should go to the remaining player to inform them the game is fully over.
    if remaining_player_id and remaining_player_id != disconnected_player_id:
        events.append(GameEvent(event_type="game_over", payload=game_over_by_disconnect_payload, target_player_id=remaining_player_id))

    logger.debug(f"G:{game_id} - Final state after disconnect:\n{current_game_state.model_dump_json(indent=2)}")

    return current_game_state, events