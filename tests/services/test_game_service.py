# tests/services/test_game_service.py
import pytest
from app.services import game_service
from app.models.game import GameState, PlayerAction, GameStatePlayer, SentencePromptPublic
from pydantic import ValidationError



# --- Helper to create a default state ---
def create_test_game_state(
    p1_id=1,
    p2_id=2,
    current_player=None
    ) -> GameState:

     p1 = GameStatePlayer(id=p1_id, name="Player 1")
     p2 = GameStatePlayer(id=p2_id, name="Player 2")

     return GameState(
        game_id="test_game_1",
        players={ p1_id: p1, p2_id: p2 },
        current_player_id= current_player or p1_id,
        sentence_prompt=SentencePromptPublic(
             id=1,
             sentence_text="Today I am tired.",
             target_word="tired",
             prompt_text="BE MORE EXTREME"
         )
     )
# ----------------------------------------

def test_process_action_not_players_turn():
    state = create_test_game_state(current_player=1)
    action = PlayerAction(action_type="submit_word", payload={"word": "exhausted"})

    # Player 2 tries to play
    new_state, events = game_service.process_player_action(state, 2, action)

    assert new_state == state # State should not have changed
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "Not your turn" in events[0]["message"]

def test_process_action_submit_valid_word(mocker):
     # Mock the underlying validator used by the service
    mock_validator = mocker.patch("app.services.game_service.validate_word_against_prompt")
    mock_validator.return_value = True # Simulate word is valid

    state = create_test_game_state(current_player=1)
    action = PlayerAction(action_type="submit_word", payload={"word": "exhausted"})

    new_state, events = game_service.process_player_action(state, 1, action)

    mock_validator.assert_called_once()
    assert len(events) == 1
    assert events[0]["type"] == "word_accepted"
    assert events[0]["word"] == "exhausted"

    # Check state changes
    assert new_state.current_player_id == 2 # Turn should switch
    assert "exhausted" in new_state.words_played_this_round_all
    assert "exhausted" in new_state.players[1].words_played
    assert new_state.players[1].mistakes_in_current_round == 0

def test_process_action_submit_invalid_word(mocker):
    mock_validator = mocker.patch("app.services.game_service.validate_word_against_prompt")
    mock_validator.return_value = False # Simulate word is INVALID

    state = create_test_game_state(current_player=1)
    action = PlayerAction(action_type="submit_word", payload={"word": "sleepy"})

    new_state, events = game_service.process_player_action(state, 1, action)

    mock_validator.assert_called_once()
    assert len(events) == 1
    assert events[0]["type"] == "mistake"
    assert "Invalid word" in events[0]["reason"]

    # Check state changes
    assert new_state.current_player_id == 1 # Turn should NOT switch
    assert new_state.players[1].mistakes_in_current_round == 1
    assert "sleepy" not in new_state.words_played_this_round_all

def test_process_action_submit_duplicate_word(mocker):
    mock_validator = mocker.patch("app.services.game_service.validate_word_against_prompt")
    # Validator should not even be called if word is duplicate

    state = create_test_game_state(current_player=1)
    state.words_played_this_round_all.append("exhausted") # Word already played

    action = PlayerAction(action_type="submit_word", payload={"word": "exhausted"})
    new_state, events = game_service.process_player_action(state, 1, action)

    mock_validator.assert_not_called() # IMPORTANT
    assert len(events) == 1
    assert events[0]["type"] == "mistake"
    assert "already played" in events[0]["reason"]

     # Check state changes
    assert new_state.current_player_id == 1 # Turn should NOT switch
    assert new_state.players[1].mistakes_in_current_round == 1

def test_process_action_third_mistake(mocker):
    mock_validator = mocker.patch("app.services.game_service.validate_word_against_prompt")
    mock_validator.return_value = False # Simulate word is INVALID

    state = create_test_game_state(current_player=1)
    state.players[1].mistakes_in_current_round = 2 # Start with 2 mistakes

    action = PlayerAction(action_type="submit_word", payload={"word": "badword"})
    new_state, events = game_service.process_player_action(state, 1, action)

    assert new_state.players[1].mistakes_in_current_round == 3
    assert events[-1]["type"] == "round_over_mistakes" # Should be last event emitted
    assert events[-1]["loser_id"] == 1
     # NOTE: Test does not assert the starting of a new round, only that the
     # round_over event was generated. Testing _start_new_round_or_end_game
     # would be a separate unit test.

def test_process_action_emoji():
    state = create_test_game_state(current_player=1)
    action = PlayerAction(action_type="send_emoji", payload={"emoji": "THUMBS_UP"})

    # Emoji can be sent anytime, even if not current player's turn (design decision)
    new_state, events = game_service.process_player_action(state, 2, action)

    assert state == new_state # State should not change for an emoji
    assert len(events) == 1
    assert events[0]["type"] == "emoji_received"
    assert events[0]["from_player_id"] == 2
    assert events[0]["emoji"] == "THUMBS_UP"