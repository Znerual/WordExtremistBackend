# app/api/deps.py
import logging
from typing import Optional
from fastapi import Cookie, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.core import security
from app.db.session import SessionLocal
from app.core.security import verify_google_id_token
from app.models.user import UserPublic
from app.crud import crud_user, crud_user as user_crud
from app.core.config import settings

logger = logging.getLogger("app.api.deps") 

ACCESS_TOKEN_COOKIE_NAME = "word_extremist_admin_token"

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
            logger.error("Google token validation failed: 'sub' field missing.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Google token: Subject (sub) missing.",
            )

        user = user_crud.get_user_by_google_id(db, google_id=google_user_id)
        if user is None:
            # User doesn't exist in our DB yet, but Google token is valid.
            # We will create them in the /auth/google/callback endpoint (or similar)
            # For protected routes, if user not found after token check, it's an issue.
            logger.error(f"User with Google ID {google_user_id} not found in our system.")
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
        logger.exception(f"Error in get_current_user_from_google_token: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials during Google token user lookup",
        )

async def get_current_user_from_backend_jwt( # Renamed for clarity
    token: str = Depends(oauth2_scheme), # Expects your backend-issued JWT
    db: Session = Depends(get_db)
) -> UserPublic:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = await security.verify_backend_token(token)
        user_id_str: str | None = payload.get("sub") # Expect user.id as string

        if user_id_str is None:
            logger.error("Backend JWT validation failed: 'sub' field missing.")
            raise credentials_exception
        
        try:
            user_db_id = int(user_id_str)
        except ValueError:
            logger.exception(f"Invalid user ID format in token 'sub': {user_id_str}")
            raise credentials_exception

        user = crud_user.get_user(db, user_id=user_db_id)
        if user is None:
            # This implies a valid token was issued for a user that no longer exists,
            # or a token from another environment. This is an anomaly.
            logger.error(f"User with DB ID {user_db_id} from valid token not found in database.")
            raise HTTPException(status_code=404, detail="User from token not found")

        if not user.is_active:
            raise HTTPException(status_code=400, detail="Inactive user")
        

        return UserPublic.model_validate(user)
    except HTTPException as e:
        logger.exception(f"HTTPException in get_current_user_from_backend_jwt: {e.detail}")
        raise e
    except Exception as e:
        logger.exception(f"Unexpected error in get_current_user_from_backend_jwt: {e}")
        raise credentials_exception


get_current_active_user = get_current_user_from_backend_jwt

async def get_current_admin_user(
    request: Request,
    token: Optional[str] = Cookie(None, alias=ACCESS_TOKEN_COOKIE_NAME),
    db: Session = Depends(get_db)
) -> UserPublic:
    """
    Dependency to get the current admin user from a cookie.
    Raises HTTPException if the user is not an authenticated admin.
    """
    login_url_with_next = f"/admin/login?next={request.url.path}"
    # Exception for browser clients to trigger a redirect
    redirect_exception = HTTPException(
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        headers={"Location": login_url_with_next},
    )
    # Exception for API clients (e.g., JavaScript fetch)
    api_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated or not an admin.",
    )


    # Determine which exception to use based on the 'Accept' header
    accept_header = request.headers.get("accept", "")
    is_browser_request = "text/html" in accept_header

    credentials_exception = redirect_exception if is_browser_request else api_exception

    if token is None:
        raise credentials_exception

    try:
        payload = await security.verify_backend_token(token)
        user_id_str: str | None = payload.get("sub")
        if user_id_str is None:
            raise credentials_exception

        user_db_id = int(user_id_str)
        user = user_crud.get_user(db, user_id=user_db_id)
        
        if user is None or not user.is_active or not user.is_superuser:
            raise credentials_exception

        return UserPublic.model_validate(user)
    except (JWTError, ValueError):
        # If token is invalid, also redirect to login
        raise credentials_exception