# app/api/deps.py
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer # We still use this for the "Bearer" scheme
from sqlalchemy.orm import Session

from app.core import security
from app.db.session import SessionLocal
from app.core.security import verify_google_id_token # Use this
from app.models.user import UserPublic
from app.crud import crud_user, crud_user as user_crud # Alias for clarity
from app.core.config import settings

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
    # ... (as before) ...

# The tokenUrl is nominal, as auth happens via Google ID Token
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/google/login") # Dummy URL

async def get_current_user_from_google_token(
    token: str = Depends(oauth2_scheme), # Client sends Google ID Token as Bearer token
    db: Session = Depends(get_db)
) -> UserPublic:
    try:
        google_payload = await verify_google_id_token(token)
        google_user_id = google_payload.get("sub")

        if google_user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Google token: Subject (sub) missing.",
            )

        user = user_crud.get_user_by_google_id(db, google_id=google_user_id)
        if user is None:
            # User doesn't exist in our DB yet, but Google token is valid.
            # We will create them in the /auth/google/callback endpoint (or similar)
            # For protected routes, if user not found after token check, it's an issue.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, # Or 401
                detail="User not registered in our system. Please complete sign-in.",
            )

        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user")

        # Optionally update user's last login time or other info from token here
        # user = user_crud.update_user_login_info(db, user=user)

        return UserPublic.model_validate(user)

    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Error in get_current_user_from_google_token: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials during Google token user lookup",
        )

async def get_current_user_from_backend_jwt( # Renamed for clarity
    token: str = Depends(oauth2_scheme), # Expects your backend-issued JWT
    db: Session = Depends(get_db)
) -> UserPublic:
    payload = await security.verify_backend_token(token) # Verify your backend's JWT
    pgs_player_id: str = payload.get("sub") # Assuming you store pgs_player_id as 'sub' in your JWT
    if pgs_player_id is None:
        raise HTTPException(status_code=401, detail="Invalid token: subject missing")

    user = crud_user.get_user_by_play_games_player_id(db, play_games_player_id=pgs_player_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return UserPublic.model_validate(user)

get_current_active_user = get_current_user_from_backend_jwt