# tests/api/test_websockets_api.py
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from sqlalchemy.orm import Session

from app.services import matchmaking_service
from app.models.user import UserPublic
from app.crud import crud_user
from app.models.user import UserCreateFromGoogle
from app.schemas.game_content import SentencePrompt as DBSentencePrompt
from app.core.config import settings

# --- Helper to create a user in the Test DB ---
def _create_db_user(db: Session, id:int, google_id: str, email:str, name:str) -> UserPublic:
     # Ensure it doesn't exist
    user = crud_user.get_user_by_google_id(db, google_id)
    if not user:
        user_in = UserCreateFromGoogle(
            google_id=google_id, email=email, username=name, profile_pic_url=None
            )
        user = crud_user.create_user_from_google_info(db, user_in)
    return UserPublic.model_validate(user)
# -----------------------------------------------


@pytest.mark.asyncio
async def test_ws_connect_auth_success_and_wait(client: TestClient, mocker, db_session: Session):
    """ Test a single player connecting successfully and waiting. """
    game_id = "ws_game_1"
    p1_google_id = "g_id_1"
    p1_token="p1_token_for_ws_success"

    # 1. Create user in DB that auth dependency will find
    p1_user = _create_db_user(db_session, id=1, google_id=p1_google_id, email="p1@test.com", name="P1WS")

   # Mock the actual Google library call
    mocked_google_verify  = mocker.patch(
        "google.oauth2.id_token.verify_oauth2_token",
        return_value={ # This is what your verify_google_id_token expects from the lib
            "sub": p1_user.google_id, "email": p1_user.email, "name": p1_user.username,
            "picture": p1_user.profile_pic_url, "email_verified": True,
            "iss": "accounts.google.com", "aud": settings.GOOGLE_CLIENT_ID, "exp": 9999999999
        }
    )
    
    # 3. Setup the game in the (mocked) matchmaking service state
    matchmaking_service.active_games[game_id] = {
         "players": [p1_user.id, "pending_p2_id"], # Use the ID checked by auth
         "game_id": game_id,
         "status": "starting"
         }

    try:
        with client.websocket_connect(f"/ws/game/{game_id}?token={p1_token}") as websocket1:
             data = websocket1.receive_json()
             assert data["type"] == "status"
             assert "Waiting for opponent" in data["message"]
    finally:
        if game_id in matchmaking_service.active_games:
            del matchmaking_service.active_games[game_id] # Cleanup

@pytest.mark.asyncio
async def test_ws_game_start_and_action(client: TestClient, mocker, db_session: Session):
    """
    Test two players connecting, game starting,
    and a player sending an action (with mocked service logic).
    """
    game_id = "ws_game_2"
    p1_token="p1_valid_token"
    p2_token="p2_valid_token"

    # 1. Create users in DB
    p1_user = _create_db_user(db_session, id=10, google_id="p10_gid", email="p10@test.com", name="PlayerTen")
    p2_user = _create_db_user(db_session, id=11, google_id="p11_gid", email="p11@test.com", name="PlayerEleven")

    
    # Prepare payloads that this mock will return
    p1_google_payload = {
        "sub": p1_user.google_id, "email": p1_user.email, "name": p1_user.username,
        "picture": p1_user.profile_pic_url, "email_verified": True,
        "iss": "accounts.google.com", "aud": settings.GOOGLE_CLIENT_ID, "exp": 9999999999
    }
    p2_google_payload = {
        "sub": p2_user.google_id, "email": p2_user.email, "name": p2_user.username,
        "picture": p2_user.profile_pic_url, "email_verified": True,
        "iss": "accounts.google.com", "aud": settings.GOOGLE_CLIENT_ID, "exp": 9999999999
    }

    mock_google_lib_verify = mocker.patch(
      "google.oauth2.id_token.verify_oauth2_token"
    )

    mock_google_lib_verify.side_effect = [p1_google_payload, p2_google_payload]

    # 2. Mock Auth dependency to return different users based on token
    #    We use side_effect to return different values on subsequent calls
    mock_auth_dep = mocker.patch(
        "app.api.websockets.deps.get_current_user_from_google_token",
    )
    mock_auth_dep.side_effect = [ p1_user, p2_user ] # P1 on first call, P2 on second

    # 3. Mock DB call for getting sentence prompt used inside the WS endpoint
    mock_sentence_prompt = DBSentencePrompt(
         id=5, sentence_text="Live Test", target_word="Live", prompt_text="TEST"
    )
    mocker.patch(
        "app.api.websockets.crud_game_content.get_random_sentence_prompt",
        return_value=mock_sentence_prompt
     )
     
    # 4. Mock the ACTUAL game service logic, so we just test that the
    #    websocket endpoint calls the service and broadcasts the result.
    #    Here we just simulate it echoing the action and swapping turns
    mock_process_action = mocker.patch("app.api.websockets.game_service.process_player_action")

    def dummy_processor(current_state, player_id, action):
        # Dummy logic: Accept word, add it, switch turn, return new state + event
        new_state = current_state.model_copy(deep=True)
        word = action.payload.get("word", "mocked_word")
        new_state.players[player_id].words_played.append(word)
        
        other_player_id = [pid for pid in new_state.players.keys() if pid != player_id][0]
        new_state.current_player_id = other_player_id # Switch turn
        
        events = [{"type": "word_accepted", "player_id": player_id, "word": word}]
        # IMPORTANT: The websocket code in the example DOES NOT use the return value
        # of process_player_action, it does its own simplified state update.
        # FOR THIS TEST TO WORK, the websockets.py code needs to be refactored
        # to actually USE game_service.process_player_action and broadcast the result.
        # Assuming that refactor happens, this mock approach is how you'd test it.
        
        # If testing the WEBSOCKET CODE AS CURRENTLY WRITTEN in the previous answer
        # (which has simplified, built-in logic and doesn't call game_service),
        # then DO NOT mock game_service.process_player_action here.
        print(f"ACTION PROCESSED BY DUMMY: Player {player_id} played {word}. Switching to {other_player_id}")
        return new_state, events
        
    # mock_process_action.side_effect = dummy_processor # UNCOMMENT if WS code calls service

    # 5. Setup Matchmaking
    matchmaking_service.active_games[game_id] = {
         "players": [p1_user.id, p2_user.id],
         "game_id": game_id,
         "status": "starting"
         }

    try:
      # --- Connect P1 and P2, check for game_start ---
      with client.websocket_connect(f"/ws/game/{game_id}?token={p1_token}") as websocket1:
        with client.websocket_connect(f"/ws/game/{game_id}?token={p2_token}") as websocket2:
            # Receive game_start messages
            p1_msg = websocket1.receive_json()
            p2_msg = websocket2.receive_json()
            
            # First message for P1 might be "waiting" if P2 hasn't connected,
            # but then P1 should get the broadcast "game_start" when P2 joins.
            if p1_msg["type"] == "status":
                 p1_msg = websocket1.receive_json() # Get the subsequent game_start

            assert p1_msg["type"] == "game_start"
            assert p2_msg["type"] == "game_start"
            assert p1_msg["state"]["current_player_id"] == p1_user.id # Check P1 (DB ID) starts

            # --- Simulate P1 Sending Action ---
            action_payload = {"action_type": "submit_word", "payload": {"word": "great"}}
            websocket1.send_json(action_payload)

            # --- Assert both P1 and P2 receive the update ---
            # Using the simplified websocket code from previous answer (NOT the mocked service)
            update_p1 = websocket1.receive_json()
            update_p2 = websocket2.receive_json()

            assert update_p1["type"] == "game_state_update"
            assert update_p2["type"] == "game_state_update"

            print(f"Update P1: {update_p1}")
            print(f"Update P2: {update_p2}")
            
            # Check that state reflects the (simplified) change made in the websocket endpoint
            assert "great" in update_p1["state"]["players"][str(p1_user.id)]["words_played"]
            assert update_p1["state"]["current_player_id"] == p2_user.id # Turn switched to P2
            assert update_p2["state"]["current_player_id"] == p2_user.id


    finally: # Cleanup
        if game_id in matchmaking_service.active_games:
              del matchmaking_service.active_games[game_id]