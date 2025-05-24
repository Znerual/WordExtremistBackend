# app/api/matchmaking.py
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel, EmailStr, HttpUrl
import asyncio

from app.services import matchmaking_service
from app.models.user import UserPublic
from datetime import datetime
router = APIRouter()

# Simple model for the frontend polling response
class MatchmakingStatusResponse(BaseModel):
    status: str # "waiting", "matched", "error"
    game_id: str | None = None
    opponent_name: str | None = None # Simplified opponent info

# In-memory store for polling requests (simplistic)
# Maps a temporary player identifier (e.g., username) to their matchmaking status
player_match_status: dict[int, MatchmakingStatusResponse] = {}

@router.get("/find", response_model=MatchmakingStatusResponse)
async def find_match(
    # Simulate client sending its ID and username - replace with auth in real app
    user_id: int = Query(..., description="Temporary user ID for player seeking match"),
    username: str = Query("Player", description="Temporary username for player seeking match") # Default username
):
    """
    Client polls this endpoint to find a match.
    Adds player to pool if not already waiting/matched.
    Returns current status.
    """
    global player_match_status

    # Check if already matched
    if user_id in player_match_status and player_match_status[user_id].status == "matched":
        return player_match_status[user_id]

    # --- Simulate having a UserPublic object without auth/DB ---
    # Create a dummy UserPublic object for interacting with the service
    # Note: In a real app, this would come from get_current_user dependency
    dummy_player = UserPublic(
        id=user_id,
        username=username,
        google_id=f"dummy_google_{user_id}", # Dummy value
        is_active=True,
        created_at=datetime.utcnow(), # Dummy value
        last_login_at=datetime.utcnow(), # Dummy value
        email=f"player{user_id}@example.com", # Dummy value
        profile_pic_url=None,
        play_games_player_id=f"dummy_pgs_{user_id}" # Add this field
    )
    # --- End Simulation ---

    is_waiting = matchmaking_service.is_player_waiting(user_id)

    if not is_waiting and user_id not in player_match_status:
        print(f"Adding player '{username}' (ID: {user_id}) to matchmaking pool.")
        matchmaking_service.add_player_to_matchmaking_pool(dummy_player) # Add the dummy object
        player_match_status[user_id] = MatchmakingStatusResponse(status="waiting") # Initial status

    # Try to create matches
    match_result = matchmaking_service.try_match_players()
    if match_result:
        game_id, p1, p2 = match_result # p1, p2 are UserPublic objects
        print(f"Match found via polling: {game_id} for {p1.username} vs {p2.username}")
        # Update status for both players using their IDs
        player_match_status[p1.id] = MatchmakingStatusResponse(
            status="matched", game_id=game_id, opponent_name=p2.username # Provide opponent's name
        )
        player_match_status[p2.id] = MatchmakingStatusResponse(
            status="matched", game_id=game_id, opponent_name=p1.username # Provide opponent's name
        )
        # Clean up status dict for players who are now matched
        # (Optional, keeps dict smaller, but status check handles it)
        # del player_match_status[p1.id]
        # del player_match_status[p2.id]


    # Return the current status for the requesting player
    return player_match_status.get(
        user_id,
        MatchmakingStatusResponse(status="waiting")
    )

@router.post("/cancel")
async def cancel_matchmaking(
     user_id: int = Query(..., description="User ID to remove from matchmaking")
):
    """ Allows a player to cancel their matchmaking request """
    global player_match_status
    matchmaking_service.remove_player_from_matchmaking_pool(user_id)
    if user_id in player_match_status:
        del player_match_status[user_id]
    print(f"Player ID '{user_id}' cancelled matchmaking.")
    return {"message": "Matchmaking cancelled"}