# tests/services/test_game_service.py
import pytest
from app.services import game_service
from app.models.game import GameState, PlayerAction, GameStatePlayer, SentencePromptPublic
from app.models.enums import RoundEndReason
from unittest.mock import MagicMock

# --- Corrected Helper to create a default state ---
def create_test_game_state(p1_id=1, p2_id=2, current_player=1) -> GameState:
     p1 = GameStatePlayer(id=p1_id, name="Player 1", level=5) # FIX: Added level
     p2 = GameStatePlayer(id=p2_id, name="Player 2", level=5) # FIX: Added level

     return GameState(
        game_id="test_game_1",
        db_game_id=101,
        players={ p1_id: p1, p2_id: p2 },
        matchmaking_player_order=[p1_id, p2_id],
        current_player_id=current_player,
        sentence_prompt=SentencePromptPublic(
             id=1, language="en", difficulty=1,
             sentence_text="Today I am tired.",
             target_word="tired",
             prompt_text="BE MORE EXTREME"
         )
     )

def test_process_action_not_players_turn(db_session):
    state = create_test_game_state(current_player=1)
    # Player 2 tries to play when it's Player 1's turn
    new_state, events = game_service.process_player_game_action(state, 2, "submit_word", {"word": "exhausted"}, db_session)
    assert new_state == state # State should not change
    assert len(events) == 1
    assert events[0].type == "error_message_to_player"
    assert "Not your turn" in events[0].payload["message"]

def test_process_action_submit_valid_word(mocker, db_session):
    mocker.patch("app.services.game_service.validate_word_against_prompt", return_value=(MagicMock(is_valid=True, creativity_score=3), 100))
    mocker.patch("app.crud.crud_user.increment_user_words_count") # Mock db side-effects
    state = create_test_game_state(current_player=1)
    
    new_state, events = game_service.process_player_game_action(state, 1, "submit_word", {"word": "exhausted"}, db_session)
    
    assert len(events) == 2
    assert events[0].type == "validation_result"
    assert events[0].payload["is_valid"] is True
    assert events[1].type == "opponent_turn_ended"
    assert new_state.current_player_id == 2 # Turn switches to P2
    assert "exhausted" in new_state.words_played_this_round_all[0]

def test_process_action_third_mistake(mocker, db_session):
    mocker.patch("app.services.game_service.validate_word_against_prompt", return_value=(MagicMock(is_valid=False, error_message="mock invalid"), 100))
    mocker.patch("app.services.game_service._prepare_next_round", return_value=(GameState.model_validate(create_test_game_state()), [])) # Mock next round logic
    mocker.patch("app.crud.crud_user.add_experience_to_user")
    
    state = create_test_game_state(current_player=1)
    state.players[1].mistakes_in_current_round = 2 # Setup state with 2 mistakes
    
    new_state, events = game_service.process_player_game_action(state, 1, "submit_word", {"word": "badword"}, db_session)

    assert new_state.players[1].mistakes_in_current_round == 3
    # The last event should be for the round ending
    assert any(e.type == "new_round_started" for e in events) or any(e.type == "game_over" for e in events)