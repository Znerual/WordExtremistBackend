# app/core/security.py
import logging
from datetime import datetime, timedelta, timezone
from typing import Tuple
from jose import jwt, JWTError
from fastapi import HTTPException, status
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
import bcrypt
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest
from google_auth_oauthlib.flow import Flow # For exchanging auth code

from app.core.config import settings

# A simple cache for Google's public keys to avoid fetching them on every request
# google-auth's id_token.verify_oauth2_token handles caching internally if you provide a requests.Request() object.
# For direct use with id_token.verify_token, you might manage a simpler cache or rely on library's internal.
# However, id_token.verify_oauth2_token is generally preferred as it handles more.
logger = logging.getLogger("app.core.security")  # Logger for this module
GOOGLE_REQUEST_SESSION = google_requests.Request() # Re-use a session for requests

def get_password_hash(password: str) -> str:
    """Hashes a password using bcrypt."""
    # Generate a salt and hash the password
    # bcrypt.gensalt() creates a new salt for each password, which is good practice.
    # The salt is embedded within the resulting hash string.
    password_bytes = password.encode('utf-8') # bcrypt works with bytes
    hashed_bytes = bcrypt.hashpw(password_bytes, bcrypt.gensalt())
    return hashed_bytes.decode('utf-8') # Store the hash as a string

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain password against a stored bcrypt hash."""
    plain_password_bytes = plain_password.encode('utf-8')
    hashed_password_bytes = hashed_password.encode('utf-8')
   
    return bcrypt.checkpw(plain_password_bytes, hashed_password_bytes)
   

async def verify_google_id_token(token: str) -> dict:
    """
    Verifies a Google ID token.
    Returns the token payload if valid, otherwise raises HTTPException.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate Google credentials",
        headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
    )
    try:
        # Verify the ID token while checking if the token was issued to your app's client ID.
        # This also checks expiration, signature, etc.
        idinfo = id_token.verify_oauth2_token(
            token, GOOGLE_REQUEST_SESSION, settings.GOOGLE_CLIENT_ID
        )

        # idinfo contains the decoded token claims:
        # idinfo['iss'] (issuer)
        # idinfo['sub'] (subject - Google User ID, this is what you store)
        # idinfo['aud'] (audience - should match your GOOGLE_CLIENT_ID)
        # idinfo['email']
        # idinfo['email_verified']
        # idinfo['name'] (display name)
        # idinfo['picture'] (profile picture URL)
        # idinfo['exp'] (expiration time)

        if idinfo['iss'] not in ['accounts.google.com', 'https://accounts.google.com']:
            logger.error(f"Invalid issuer in Google ID token: {idinfo['iss']}")
            raise ValueError('Wrong issuer.')

        # You can add more checks here if needed (e.g., specific hd domain for G Suite)

        return idinfo # This is the dictionary of claims

    except ValueError as e:
        # This error occurs if the token is invalid (e.g., wrong issuer, expired, bad signature, wrong audience)
        logger.exception(f"Google ID Token ValueError: {e}")
        raise credentials_exception
    except Exception as e:
        logger.exception(f"Unexpected error verifying Google ID Token: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error during Google token verification"
        )

async def exchange_google_auth_code(auth_code: str) -> Tuple[str | None, str | None, str | None, str | None]:
    """
    Exchanges a Google server auth code for tokens and gets the Play Games Player ID.
    Returns: (play_games_player_id, email, refresh_token, access_token_for_pgs)
    """
    try:
        flow = Flow.from_client_secrets_info(
            client_config={
                "web": {
                    "client_id": settings.GOOGLE_WEB_CLIENT_ID,
                    "client_secret": settings.GOOGLE_WEB_CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [settings.REDIRECT_URI], # e.g., "postmessage" or your configured URI
                    "javascript_origins": [] # Or your app's origins
                }
            },
            scopes=['https://www.googleapis.com/auth/games_lite'], # Scope for Play Games Lite (Player ID)
            redirect_uri=settings.REDIRECT_URI
        )

        # Exchange the auth code for tokens
        flow.fetch_token(code=auth_code)
        credentials = flow.credentials # google.oauth2.credentials.Credentials

        if not credentials or not credentials.valid:
            logger.error("Failed to fetch valid credentials from Google auth code.")
            raise HTTPException(status_code=400, detail="Invalid auth code or failed to fetch tokens.")

        # Securely get Play Games Player ID using the obtained credentials
        # This requires calling the Play Games Services API.
        # For v2, it's usually part of the sign-in identity.
        # The 'sub' claim in an ID token obtained via these credentials would be the Google Account ID.
        # To get the specific Play Games Player ID, you might need to make an API call.

        # Let's assume for now the Player ID can be derived or is the 'sub' from an ID token if one were present.
        # For Play Games v2, the primary identifier IS the Player ID obtained after auth.
        # If credentials.id_token is available (it might be after fetch_token):
        pgs_player_id = None
        email = None
        id_token_claims = getattr(credentials, 'id_token_jwt', None) # Raw ID token if available
        if credentials.id_token: # Parsed ID token dictionary
            pgs_player_id = credentials.id_token.get("sub") # Often Google Account sub, need to confirm for PGS
            email = credentials.id_token.get("email")
        
        # A more direct way to get the Play Games Player ID would be to use the access token
        # to call the Play Games Services API's "players/me" endpoint.
        # For simplicity, let's simulate this or assume 'sub' from id_token is usable as a unique Google ID.
        # The most reliable way for PGS Player ID is via an API call to https://www.googleapis.com/games/v1/players/me
        # using the access token.

        # Placeholder - In a real scenario, you'd make an API call to Google Play Games API
        # using credentials.token (the access token) to get the verified player ID.
        # For example:
        # from googleapiclient.discovery import build
        # games_service = build('games', 'v1', credentials=credentials)
        # player_info = games_service.players().get(playerId='me').execute()
        # pgs_player_id = player_info.get('playerId')
        # display_name = player_info.get('displayName')

        # For this example, let's assume the 'sub' from a potential ID token is what we use for linking.
        # Or, if `credentials.refresh_token` is present, you can store it.
        # The actual Play Games Player ID might need a direct API call with the access token.
        # The 'sub' in the ID token is the Google Account's unique ID.
        # For Play Games Services v2, this 'sub' when obtained through PGS flow *is* the Player ID.

        if not pgs_player_id: # Fallback or if ID token not directly available from `flow.credentials`
             # This part is crucial and needs to be accurate for PGS v2
             # Typically, after exchanging the server_auth_code, Google provides an ID token.
             # The 'sub' claim of *that specific ID token* (scoped for your game) is the PGS Player ID.
             # If flow.credentials.id_token is not populated, you might need to parse it from the token response.
             # Or, better, use the access token to call Google's userinfo endpoint or PGS players/me.
             # Let's assume for now the `flow.credentials.id_token` is populated as expected
             # by `google-auth-oauthlib` when using the games_lite scope.

             # If the ID token is not directly in `flow.credentials.id_token` as a dict,
             # you may need to decode `credentials.id_token_jwt` if available.
             # Or, more robustly, call the userinfo endpoint with the access token:
             import httpx
             async with httpx.AsyncClient() as client:
                 userinfo_resp = await client.get(
                     "https://www.googleapis.com/oauth2/v3/userinfo",
                     headers={"Authorization": f"Bearer {credentials.token}"}
                 )
                 if userinfo_resp.status_code == 200:
                     userinfo = userinfo_resp.json()
                     pgs_player_id = userinfo.get("sub") # Google account sub
                     email = userinfo.get("email")
                     # Note: This 'sub' is the general Google Account ID.
                     # For the *specific Play Games Player ID*, an API call to Play Games API is best.
                     # For Play Games v2, this 'sub' obtained through server auth flow for PGS scope *is* the Player ID.


        logger.debug(f"DEBUG: Exchanged auth code. PGS Player ID (sub): {pgs_player_id}, Email: {email}")
        return pgs_player_id, email, credentials.refresh_token, credentials.token

    except Exception as e:
        logger.exception(f"Error exchanging Google auth code: {e}")
        # Log the error details
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid Google auth code or server error: {str(e)}")


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    # Add 'iss' (issuer) and 'aud' (audience) for better token validation if desired
    # to_encode.update({"iss": "your_backend_name", "aud": "your_client_audience"})
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM) # Use your actual key/algo from settings
    return encoded_jwt

async def verify_backend_token(token: str) -> dict:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate backend token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        # Use the jwt object to decode
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            # options={"verify_aud": False, "verify_iss": False} # If you don't set/check audience/issuer
        )
        # Example: Check for expiration, which decode() handles by default
        # You could add more checks here like 'iss' or 'aud' if you set them during creation.
        # if payload.get("iss") != "your_project_name_or_url":
        #     raise JWTError("Invalid issuer")
            
        return payload
    except JWTError as e: # Catches errors from jwt.decode (e.g., signature expired, invalid signature)
        logger.exception(f"JWTError during backend token verification: {e}")
        raise credentials_exception
    except Exception as e: # Catch any other unexpected errors
        logger.exception(f"Unexpected error verifying backend token: {e}")
        raise credentials_exception