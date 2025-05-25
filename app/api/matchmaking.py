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
        if current_status.game_id and not current_status.player1_id:
             game_info_for_ws = matchmaking_service.get_game_info(current_status.game_id)
             if game_info_for_ws and "players" in game_info_for_ws:
                 p_ids = game_info_for_ws["players"]
                 current_status.player1_id = p_ids[0]
                 current_status.player2_id = p_ids[1]
                 current_status.your_player_id_in_game = user_id # Redundant but clear
        return current_status

    is_waiting = matchmaking_service.is_player_waiting(user_id)

    if not is_waiting and user_id not in player_match_status:
        print(f"Adding player '{current_user.username}' (DB ID: {user_id}) to matchmaking pool.")
        matchmaking_service.add_player_to_matchmaking_pool(current_user) # Pass UserPublic
        player_match_status[user_id] = MatchmakingStatusResponse(status="waiting", your_player_id_in_game=user_id)

    match_result = matchmaking_service.try_match_players()
    if match_result:
        game_id, p1, p2 = match_result # p1, p2 are UserPublic objects
        print(f"Match found: {game_id} for {p1.username} (ID: {p1.id}) vs {p2.username} (ID: {p2.id})")
        
        player_match_status[p1.id] = MatchmakingStatusResponse(
            status="matched", game_id=game_id, opponent_name=p2.username,
            player1_id=p1.id, player2_id=p2.id, your_player_id_in_game=p1.id
        )
        player_match_status[p2.id] = MatchmakingStatusResponse(
            status="matched", game_id=game_id, opponent_name=p1.username,
            player1_id=p1.id, player2_id=p2.id, your_player_id_in_game=p2.id
        )

    return player_match_status.get(
        user_id,
        MatchmakingStatusResponse(status="waiting", your_player_id_in_game=user_id)
    )

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