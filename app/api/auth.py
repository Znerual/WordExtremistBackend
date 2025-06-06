# app/api/auth.py
import logging
import pathlib
import shutil
from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status, Body
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.api import deps
from app.core import security
from app.models.user import DeviceLoginRequest, BackendToken, GetOrCreateUserRequest, ServerAuthCodeRequest, UserCreateFromPGS, UserPublic, UserCreateFromGoogle
from app.models.game_log_display import UserWordVaultEntry
from app.crud import crud_user, crud_game_log
from app.core.security import get_password_hash, verify_password, create_access_token
from app.core.security import verify_google_id_token
from app.core.config import settings
from datetime import datetime, timedelta, timezone
# from app.core.security import create_backend_access_token # If issuing own tokens
# from app.models.user import BackendToken # If issuing own tokens

logger = logging.getLogger("app.api.auth")  # Logger for this module
router = APIRouter()

class GoogleIdTokenRequest(BaseModel): # Renamed for clarity
    google_id_token: str


@router.post("/device-login", response_model=BackendToken)
async def login_with_device_credentials(
    request_data: DeviceLoginRequest,
    db: Session = Depends(deps.get_db)
):
    if not request_data.client_provided_id or not request_data.client_generated_password:
        raise HTTPException(status_code=400, detail="Client ID and password are required.")

    user = crud_user.get_user_by_client_provided_id(db, client_id=request_data.client_provided_id)

    if not user:
        # --- User Registration Case ---
        logger.info(f"Device ID {request_data.client_provided_id} not found. Registering new user.")
        hashed_password = get_password_hash(request_data.client_generated_password)
        
        # We need a username. Client doesn't send it in this request.
        # Generate a default one. The client could update it later if you build that feature.
        default_username = f"User_{request_data.client_provided_id[:8]}"
        
        try:
            user = crud_user.create_user_for_device_login(db, 
                client_id=request_data.client_provided_id,
                hashed_password_val=hashed_password,
                username=default_username
            )
        except Exception as e: # Catch potential IntegrityError if client_provided_id somehow got duplicated
            db.rollback()
            raise HTTPException(status_code=400, detail=f"Could not register user. Possible duplicate ID. Error: {e}")
    
    elif not user.hashed_password or not verify_password(request_data.client_generated_password, user.hashed_password):
        # --- Login Failed Case ---
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect client ID or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # --- Login Successful or Registration Successful ---
    user.last_login_at = datetime.now(timezone.utc) # Update last login
    db.commit()
    db.refresh(user)

    access_token_expires_minutes = settings.ACCESS_TOKEN_EXPIRE_MINUTES
    access_token_expires_delta = timedelta(minutes=access_token_expires_minutes)
    # The 'sub' of your JWT will be the client_provided_id for this scheme.
    # Or you could use user.id (database primary key) as 'sub' for consistency if other auth methods use it.
    # Let's use user.id (database PK) as 'sub' to be consistent with potential future auth methods.
    # Include client_provided_id in the token payload if client needs it.
    jwt_payload_data = {"sub": str(user.id), "cpid": user.client_provided_id}
    
    access_token = create_access_token(
        data=jwt_payload_data, expires_delta=access_token_expires_delta
    )
    return BackendToken(access_token=access_token, token_type="bearer", user=user, expires_in=int(access_token_expires_delta.total_seconds()))

# --- REMOVE or DEACTIVATE the old /user/get-or-create endpoint ---
# It's now superseded by /device-login
# @router.post("/user/get-or-create", response_model=UserPublic) ...


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
            logger.info(f"New user created via PGS: {user.username} (PGS ID: {pgs_player_id})")
        else:
            user.last_login_at = datetime.now(timezone.utc)
            # if refresh_token and user.google_refresh_token != refresh_token:
            #     user.google_refresh_token = refresh_token # Securely store if needed
            db.commit()
            db.refresh(user)
            logger.info(f"User logged in via PGS: {user.username} (PGS ID: {pgs_player_id})")

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
        logger.exception(f"Error during PGS login: {e}")
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
            logger.warning(f"Warning: Email {email} for Google ID {google_id} is not verified.")
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
            logger.info(f"New user linked via Google Sign-In: {user.username} (Google ID: {google_id})")
        else:
            # Existing user, update last login or other details if necessary
            user.last_login_at = datetime.now(timezone.utc) # Update last login
            # Optionally update username/picture if they changed in Google profile
            # user.username = username
            # user.profile_pic_url = picture_url
            db.commit()
            db.refresh(user)
            logger.debug(f"User logged in via Google: {user.username} (Google ID: {google_id})")

        return UserPublic.model_validate(user)

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.exception(f"Error during Google device link: {e}")
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

@router.get("/users/me/words", response_model=List[UserWordVaultEntry])
async def get_my_words(
    current_user: UserPublic = Depends(deps.get_current_active_user),
    db: Session = Depends(deps.get_db)
):
    """Get all valid words ever submitted by the current user for the Word Vault."""
    user_id = current_user.id
    # Call the new CRUD function
    word_entries_raw = crud_game_log.get_all_word_vault_entries_for_user(db, user_id=user_id)
    
    # Map the raw tuple results to our Pydantic response model
    response_data = [
        UserWordVaultEntry(
            submitted_word=word,
            creativity_score=score,
            sentence_text=sentence,
            prompt_text=prompt
        )
        for word, score, sentence, prompt in word_entries_raw
    ]
    
    return response_data

@router.patch("/users/me", response_model=UserPublic)
async def update_current_user_profile(
    current_user_from_token: UserPublic = Depends(deps.get_current_active_user),
    db: Session = Depends(deps.get_db),
    username: Optional[str] = Form(None),
    profile_picture: Optional[UploadFile] = File(None)
):
    """
    Update the current authenticated user's profile.
    Can update username, profile picture, or both.
    """
    user_id = current_user_from_token.id
    db_user = crud_user.get_user(db, user_id=user_id)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    update_data = {}

    if username is not None and username.strip() != db_user.username:
        update_data["username"] = username.strip()

    if profile_picture is not None:
        if profile_picture.content_type not in ["image/jpeg", "image/png", "image/webp"]:
            raise HTTPException(status_code=400, detail="Invalid file type. Please upload a JPG or PNG.")

        file_extension = pathlib.Path(profile_picture.filename).suffix
        unique_filename = f"{uuid.uuid4()}{file_extension}"
        save_path = settings.UPLOADS_DIR / unique_filename

        try:
            with save_path.open("wb") as buffer:
                shutil.copyfileobj(profile_picture.file, buffer)
            
            file_url = f"{settings.STATIC_FILES_BASE_URL}/static/uploads/{unique_filename}"
            update_data["profile_pic_url"] = file_url
            logger.info(f"User {user_id} uploaded new profile picture. Saved to {save_path}, URL: {file_url}")

        except Exception as e:
            logger.exception(f"Failed to save uploaded file for user {user_id}: {e}")
            raise HTTPException(status_code=500, detail="Could not save file.")
        finally:
            profile_picture.file.close()

    if not update_data:
        return db_user

    updated_user = crud_user.update_user_admin(db, user_id=user_id, user_update_data=update_data)
    if not updated_user:
        raise HTTPException(status_code=500, detail="Failed to update user in database.")

    return updated_user