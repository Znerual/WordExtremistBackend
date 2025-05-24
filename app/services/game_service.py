# app/services/game_service.py
from app.models.game import GameState, PlayerAction, GameStatePlayer # Pydantic models
from app.services.word_validator import validate_word_against_prompt
import time

MAX_MISTAKES = 3


def process_player_action(
    current_state: GameState, player_id: int, action: PlayerAction
) -> tuple[GameState, list]: # Returns new state and list of events/messages
    
    new_state = current_state.model_copy(deep=True)
    events = [] # e.g., {"type": "word_accepted", "player_id": ..., "word": ...}

    # Handle emoji action first, as it might not depend on whose turn it is
    if action.action_type == "send_emoji":
        emoji = action.payload.get("emoji")
        events.append({"type": "emoji_received", "from_player_id": player_id, "emoji": emoji})
        # For emojis, we might not change game state, just emit an event
        # We return early here if an emoji is the only thing.
        # Or, if emojis are allowed alongside other turn-based actions, structure differently.
        # Assuming emoji is a standalone action for now:
        return new_state, events # Return current state (no change) and the emoji event

    if new_state.current_player_id != player_id:
        events.append({"type": "error", "player_id": player_id, "message": "Not your turn."})
        return new_state, events

    player_state = new_state.players[player_id]

    if action.action_type == "submit_word":
        word = action.payload.get("word", "").strip().lower()
        if not word:
            events.append({"type": "error", "player_id": player_id, "message": "Word cannot be empty."})
            return new_state, events # No state change, just an error event

        if word in new_state.words_played_this_round_all:
            # Player made a mistake (re-submitting already played word)
            player_state.mistakes_in_current_round += 1
            events.append({"type": "mistake", "player_id": player_id, "reason": "Word already played this round."})
        else:
            # --- THIS IS WHERE YOUR WORD VALIDATION LOGIC GOES ---
            is_valid_replacement = validate_word_against_prompt(
                word, new_state.sentence_prompt.target_word, new_state.sentence_prompt.prompt_text, new_state.sentence_prompt.sentence_text
            )
           
            if is_valid_replacement:
                player_state.words_played.append(word)
                new_state.words_played_this_round_all.append(word)
                events.append({"type": "word_accepted", "player_id": player_id, "word": word})
                # Switch turn
                all_player_uids = list(new_state.players.keys())
                current_idx = all_player_uids.index(player_id)
                next_idx = (current_idx + 1) % len(all_player_uids)
                new_state.current_player_id = all_player_uids[next_idx]
                new_state.is_waiting_for_opponent = True # Next player needs to act
            else:
                player_state.mistakes_in_current_round += 1
                events.append({"type": "mistake", "player_id": player_id, "reason": "Invalid word replacement."})
        
        # Check for round end due to mistakes
        if player_state.mistakes_in_current_round >= MAX_MISTAKES:
            # Handle round end logic (determine winner, update scores, prepare for next round or game end)
            # This would be more complex: update round scores, check game over, etc.
            events.append({"type": "round_over_mistakes", "loser_id": player_id})
            # For now, let's just say the other player wins the round conceptually
            # new_state = _start_new_round_or_end_game(new_state, events) # Helper function


    new_state.last_action_timestamp = time.time()
    return new_state, events