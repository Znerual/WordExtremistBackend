# app/api/auth.py
from fastapi import APIRouter, Depends, HTTPException, status, Body
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.api import deps
from app.core import security
from app.models.user import BackendToken, GetOrCreateUserRequest, ServerAuthCodeRequest, UserCreateFromPGS, UserPublic, UserCreateFromGoogle
from app.crud import crud_user
from app.core.security import verify_google_id_token
from app.core.config import settings
from datetime import datetime, timedelta, timezone
# from app.core.security import create_backend_access_token # If issuing own tokens
# from app.models.user import BackendToken # If issuing own tokens

router = APIRouter()

class GoogleIdTokenRequest(BaseModel): # Renamed for clarity
    google_id_token: str


@router.post("/user/get-or-create", response_model=UserPublic)
async def get_or_create_user_by_client_id(
    request_data: GetOrCreateUserRequest,
    db: Session = Depends(deps.get_db)
):
    """
    Retrieves a user by their client_provided_id.
    If the user doesn't exist, a new one is created with this ID.
    Returns the full UserPublic object, including the database ID.
    """
    if not request_data.client_provided_id:
        raise HTTPException(status_code=400, detail="client_provided_id is required.")

    user = crud_user.get_user_by_client_provided_id(db, client_id=request_data.client_provided_id)

    if not user:
        print(f"User with client_provided_id '{request_data.client_provided_id}' not found. Creating new user.")
        user = crud_user.create_user_with_client_provided_id(db, user_in=request_data)
        print(f"Created new user: {user.username} (DB ID: {user.id}, Client ID: {user.client_provided_id})")
    else:
        # User exists, update last_login_at or other details if needed
        user.last_login_at = datetime.now(timezone.utc)
        if request_data.username and user.username != request_data.username:
            # Optionally update username if client provides a new one and you allow it
            # user.username = request_data.username 
            pass
        db.commit()
        db.refresh(user)
        print(f"Found existing user: {user.username} (DB ID: {user.id}, Client ID: {user.client_provided_id})")

    return UserPublic.model_validate(user)

@router.post("/pgs-login", response_model=BackendToken) # Client sends Server Auth Code
async def login_with_play_games_server_auth_code(
    request_data: ServerAuthCodeRequest,
    db: Session = Depends(deps.get_db)
):
    auth_code = request_data.server_auth_code
    if not auth_code:
        raise HTTPException(status_code=400, detail="Server auth code is required.")

    try:
        pgs_player_id, email, refresh_token, _ = await security.exchange_google_auth_code(auth_code)
        # Note: You might want to get username/pic via another PGS API call here using the access_token

        if not pgs_player_id:
            raise HTTPException(status_code=400, detail="Could not retrieve Play Games Player ID from auth code.")

        user = crud_user.get_user_by_play_games_player_id(db, play_games_player_id=pgs_player_id)
        if not user:
            # Potentially fetch display name/avatar from PGS API here
            # For now, using email as username if available
            username_from_pgs = email.split('@')[0] if email else f"Player_{pgs_player_id[:6]}"

            user_in_create = UserCreateFromPGS(
                play_games_player_id=pgs_player_id,
                email=email, # May be None
                username=username_from_pgs,
                # profile_pic_url= fetched_pic_url # Fetch if needed
            )
            user = crud_user.create_user_from_pgs_info(db, user_in=user_in_create)
            print(f"New user created via PGS: {user.username} (PGS ID: {pgs_player_id})")
        else:
            user.last_login_at = datetime.now(timezone.utc)
            # if refresh_token and user.google_refresh_token != refresh_token:
            #     user.google_refresh_token = refresh_token # Securely store if needed
            db.commit()
            db.refresh(user)
            print(f"User logged in via PGS: {user.username} (PGS ID: {pgs_player_id})")

        # Create your backend's JWT
        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        # Store the verified PGS Player ID in your JWT's subject
        backend_access_token = security.create_access_token(
            data={"sub": user.play_games_player_id, "user_db_id": user.id}, # Include your internal DB ID too
            expires_delta=access_token_expires
        )
        return BackendToken(access_token=backend_access_token)

    except HTTPException as e: # Re-raise FastAPI HTTPExceptions
        raise e
    except Exception as e:
        print(f"Error during PGS login: {e}")
        # Log the full error for debugging
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"An error occurred during Play Games sign-in: {str(e)}")


@router.post("/google/link-device", response_model=UserPublic) # Renamed endpoint
async def link_device_with_google_account(
    token_request: GoogleIdTokenRequest, # Client sends Google ID Token
    db: Session = Depends(deps.get_db)
):
    """
    Client sends Google ID Token obtained from (silent) Google Sign-In on the device.
    Backend verifies it, then finds or creates a user in the local PostgreSQL DB.
    This effectively "logs in" the user or links their device's current Google identity.
    """
    try:
        google_payload = await verify_google_id_token(token_request.google_id_token)

        google_id = google_payload.get("sub")
        email = google_payload.get("email")
        # Ensure email is present and email_verified is true if needed for your app's policy
        if not google_payload.get("email_verified", False) and email: # Optional check
             print(f"Warning: Email {email} for Google ID {google_id} is not verified.")
             # Decide if you want to proceed or require verified emails.

        username = google_payload.get("name")
        picture_url = google_payload.get("picture")

        if not google_id or not email: # Email is usually a good secondary identifier/check
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Google token payload: missing sub or email."
            )

        user = crud_user.get_user_by_google_id(db, google_id=google_id)
        if not user:
            # New user based on this Google ID
            user_create_data = UserCreateFromGoogle(
                google_id=google_id,
                email=email,
                username=username,
                profile_pic_url=picture_url
            )
            user = crud_user.create_user_from_google_info(db, user_in=user_create_data)
            print(f"New user linked via Google Sign-In: {user.username} (Google ID: {google_id})")
        else:
            # Existing user, update last login or other details if necessary
            user.last_login_at = datetime.now(timezone.utc) # Update last login
            # Optionally update username/picture if they changed in Google profile
            # user.username = username
            # user.profile_pic_url = picture_url
            db.commit()
            db.refresh(user)
            print(f"User logged in via Google: {user.username} (Google ID: {google_id})")

        return UserPublic.model_validate(user)

    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Error during Google device link: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred during Google sign-in processing."
        )

@router.get("/users/me", response_model=UserPublic)
async def read_users_me(
    current_user: UserPublic = Depends(deps.get_current_active_user) # Uses Google ID Token
):
    """Get current authenticated user's profile."""
    return current_user