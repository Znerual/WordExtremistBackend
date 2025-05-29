# app/api/matchmaking.py
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel, EmailStr, HttpUrl
import asyncio

from sqlalchemy.orm import Session
from app.services import matchmaking_service
from app.models.user import UserPublic
from datetime import datetime
from app.api import deps # For get_db if needed by service, but not for user auth here
from app.crud import crud_user # To fetch user details by ID

router = APIRouter()

# Simple model for the frontend polling response
class MatchmakingStatusResponse(BaseModel):
    status: str
    game_id: str | None = None
    game_language: str | None = None
    opponent_name: str | None = None
    player1_id: int | None = None
    player2_id: int | None = None
    your_player_id_in_game: int | None = None

# In-memory store for polling requests (simplistic)
# Maps a temporary player identifier (e.g., username) to their matchmaking status
player_match_status: dict[int, MatchmakingStatusResponse] = {}

@router.get("/find", response_model=MatchmakingStatusResponse)
async def find_match(
    # Simulate client sending its ID and username - replace with auth in real app
    current_user: UserPublic = Depends(deps.get_current_active_user), # USE AUTHENTICATED USER
    requested_language: str | None = Query(None, description="Preferred BCP-47 language code for the game (e.g., 'en', 'es'). Defaults to server default if not specified."),
    db: Session = Depends(deps.get_db)
):
    """
    Client polls this endpoint to find a match.
    Adds player to pool if not already waiting/matched.
    Returns current status.
    """
    global player_match_status

    # Fetch user details from DB using the provided user_id to ensure it's valid
    # and to get their actual username, etc.
    user_id = current_user.id # Get the ID from the authenticated user object
    current_user_from_db = crud_user.get_user(db, user_id=user_id)
    if not current_user_from_db:
        raise HTTPException(status_code=404, detail=f"User with ID {user_id} not found. Please register first via /user/get-or-create.")
    
    # Convert SQLAlchemy model to Pydantic model for the service
    current_user = UserPublic.model_validate(current_user_from_db)


    if user_id in player_match_status and player_match_status[user_id].status == "matched":
        current_status = player_match_status[user_id]
        # Populate IDs if not already (should be done when match is made)
        if current_status.game_id and matchmaking_service.get_game_info(current_status.game_id):
            return current_status
        else:
            del player_match_status[user_id]  # Remove stale match status


    is_waiting = matchmaking_service.is_player_waiting(user_id)
    if not is_waiting and user_id not in player_match_status:
        print(f"Adding player '{current_user.username}' (DB ID: {user_id}) to matchmaking pool for lang '{requested_language or matchmaking_service.DEFAULT_GAME_LANGUAGE}.")
        matchmaking_service.add_player_to_matchmaking_pool(current_user, requested_language=requested_language)
        player_match_status[user_id] = MatchmakingStatusResponse(status="waiting", your_player_id_in_game=user_id)

    match_result = matchmaking_service.try_match_players()
    if match_result:
        game_id, p1, p2, game_lang  = match_result # p1, p2 are UserPublic objects
        print(f"Match found via /find: {game_id} (Lang: {game_lang}) for {p1.username} vs {p2.username}")
        
        player_match_status[p1.id] = MatchmakingStatusResponse(
            status="matched", game_id=game_id, game_language=game_lang, opponent_name=p2.username,
            player1_id=p1.id, player2_id=p2.id, your_player_id_in_game=p1.id
        )
        player_match_status[p2.id] = MatchmakingStatusResponse(
            status="matched", game_id=game_id, game_language=game_lang, opponent_name=p1.username,
            player1_id=p1.id, player2_id=p2.id, your_player_id_in_game=p2.id
        )

    # If user is still waiting (either wasn't matched, or the match involved other players)
    if matchmaking_service.is_player_waiting(user_id):
         waiting_response = MatchmakingStatusResponse(status="waiting", your_player_id_in_game=user_id, game_language=(requested_language or matchmaking_service.DEFAULT_GAME_LANGUAGE))
         player_match_status[user_id] = waiting_response # Cache waiting status
         return waiting_response
    
    # If user is not waiting and not matched (e.g. cancelled, or error state)
    # This path should ideally not be hit if `is_player_waiting` is accurate or they are matched.
    # However, as a fallback:
    if user_id in player_match_status: # They were matched, but the current request didn't re-trigger that
        return player_match_status[user_id]

    # Default fallback if something unexpected happened
    return MatchmakingStatusResponse(status="error", message="Could not determine matchmaking status.")

    # return player_match_status.get(
    #     user_id,
    #     MatchmakingStatusResponse(status="waiting", your_player_id_in_game=user_id)
    # )

@router.post("/cancel")
async def cancel_matchmaking(
    current_user: UserPublic = Depends(deps.get_current_active_user), # USE AUTHENTICATED USER
):
    """ Allows a player to cancel their matchmaking request """
    global player_match_status
    user_id = current_user.id
    matchmaking_service.remove_player_from_matchmaking_pool(user_id)
    if user_id in player_match_status:
        del player_match_status[user_id]
    print(f"Player ID '{user_id}' cancelled matchmaking.")
    return {"message": "Matchmaking cancelled"}