# tests/api/test_auth_api.py
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock # For mocking async functions
from app.core.config import settings
from app.schemas.user import User as DBUser # SQLAlchemy model for DB checks
from fastapi import HTTPException

# Mock payload for a new user from Google
NEW_USER_GOOGLE_PAYLOAD = {
    "sub": "new_google_user_id_123",
    "play_games_player_id": "new_google_user_id_123",
    "email": "new.user@example.com",
    "email_verified": True,
    "name": "New Google User",
    "picture": "http://example.com/newpic.jpg",
    "iss": "accounts.google.com",
    "aud": settings.GOOGLE_CLIENT_ID,
    "exp": 9999999999
}

# Mock payload for an existing user from Google
EXISTING_USER_GOOGLE_ID = "existing_google_user_id_456"
EXISTING_USER_EMAIL = "existing.user@example.com"
EXISTING_USER_GOOGLE_PAYLOAD = {
    "sub": EXISTING_USER_GOOGLE_ID,
    "play_games_player_id": "new_google_user_id_1234",
    "email": EXISTING_USER_EMAIL,
    "email_verified": True,
    "name": "Existing Google User",
    "picture": "http://example.com/existingpic.jpg",
    "iss": "accounts.google.com",
    "aud": settings.GOOGLE_CLIENT_ID,
    "exp": 9999999999
}


def test_link_device_new_user(client: TestClient, mocker, db_session): # db_session to check DB state
    # Mock the external Google token verification
    # Mock app.core.security.verify_google_id_token AS IT IS USED by the auth endpoint
    # The endpoint itself calls await verify_google_id_token(...)
    mock_verify = mocker.patch(
        "app.api.auth.verify_google_id_token", # Path to where it's *called* or imported
        # If app.api.auth.py has "from app.core.security import verify_google_id_token",
        # then "app.api.auth.verify_google_id_token" is the correct path to mock.
        # If it has "import app.core.security" and calls "app.core.security.verify_google_id_token",
        # then that's the path. Let's assume the former for now.
        new_callable=AsyncMock, # It's an async function
        return_value=NEW_USER_GOOGLE_PAYLOAD
    )


    response = client.post(
        f"{settings.API_V1_STR}/auth/google/link-device",
        json={"google_id_token": "fake_new_user_google_token"}
    )
    assert response.status_code == 200 # Or 201 if you prefer for creation
    data = response.json()
    assert data["email"] == NEW_USER_GOOGLE_PAYLOAD["email"]
    assert data["google_id"] == NEW_USER_GOOGLE_PAYLOAD["sub"]
    assert data["username"] == NEW_USER_GOOGLE_PAYLOAD["name"]

    # Verify user was created in DB
    user_in_db = db_session.query(DBUser).filter(DBUser.google_id == NEW_USER_GOOGLE_PAYLOAD["sub"]).first()
    assert user_in_db is not None
    assert user_in_db.email == NEW_USER_GOOGLE_PAYLOAD["email"]


def test_link_device_existing_user(client: TestClient, mocker, db_session):
    # 1. Pre-populate the database with an existing user
    from app.crud.crud_user import create_user_from_google_info
    from app.models.user import UserCreateFromGoogle

    existing_user_create = UserCreateFromGoogle(
        google_id=EXISTING_USER_GOOGLE_ID,
        email=EXISTING_USER_EMAIL,
        username="PreExisting User", # Initial username
        profile_pic_url="http://example.com/initial.jpg"
    )
    create_user_from_google_info(db_session, user_in=existing_user_create, commit_db=False)

    # 2. Mock Google verification to return this existing user's details
    mocker.patch(
        "app.api.auth.verify_google_id_token",
        AsyncMock(return_value=EXISTING_USER_GOOGLE_PAYLOAD) # Google might return updated name/pic
    )

    response = client.post(
        f"{settings.API_V1_STR}/auth/google/link-device",
        json={"google_id_token": "fake_existing_user_google_token"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == EXISTING_USER_GOOGLE_PAYLOAD["email"]
    assert data["google_id"] == EXISTING_USER_GOOGLE_PAYLOAD["sub"]
    # Username might have been updated if logic for that exists in endpoint
    # For now, it would be the one from EXISTING_USER_GOOGLE_PAYLOAD if create_user_from_google_info updates it,
    # or the one from update_user_login_info
    assert "last_login_at" in data # Check if last_login_at was updated

    user_in_db = db_session.query(DBUser).filter(DBUser.google_id == EXISTING_USER_GOOGLE_ID).first()
    assert user_in_db is not None
    assert user_in_db.last_login_at is not None


def test_link_device_invalid_google_token(client: TestClient, mocker):
    mocker.patch(
        "app.api.auth.verify_google_id_token",
        AsyncMock(side_effect=HTTPException(status_code=401, detail="Invalid Google Token Mocked"))
    )
    response = client.post(
        f"{settings.API_V1_STR}/auth/google/link-device",
        json={"google_id_token": "invalid_google_token"}
    )
    assert response.status_code == 401


def test_read_users_me_authenticated(client: TestClient, mocker, db_session):
    # 1. Ensure a user exists and "login" them by mocking verify_google_id_token
    #    for the get_current_active_user dependency.
    from app.crud.crud_user import create_user_from_google_info
    from app.models.user import UserCreateFromGoogle
    
    print(f"\n[BEFORE CREATE] Users with google_id {NEW_USER_GOOGLE_PAYLOAD['sub']}:")
    users_before = db_session.query(DBUser).filter(DBUser.google_id == NEW_USER_GOOGLE_PAYLOAD['sub']).all()
    for u in users_before:
        print(f" - User ID: {u.id}, Email: {u.email}")
    if not users_before:
        print(" - None found.")
    
    # Map 'sub' from Google payload to 'google_id' for your Pydantic model
    user_create_args = {
        "google_id": NEW_USER_GOOGLE_PAYLOAD["sub"],
        "email": NEW_USER_GOOGLE_PAYLOAD["email"],
        "username": NEW_USER_GOOGLE_PAYLOAD["name"],
        "profile_pic_url": NEW_USER_GOOGLE_PAYLOAD["picture"]
    }
    user_create_data = UserCreateFromGoogle(**user_create_args)
    create_user_from_google_info(db_session, user_in=user_create_data, commit_db=False)

   
    # Mock where get_current_active_user (which calls get_current_user_from_google_token)
    # would call verify_google_id_token.
    # So we mock the internal google library call that verify_google_id_token makes.
    mock_google_lib_verify = mocker.patch(
        "google.oauth2.id_token.verify_oauth2_token", # Path to the actual library function
        return_value=NEW_USER_GOOGLE_PAYLOAD # It directly returns the payload
    )

    # The client needs to send a token. Even though it's mocked, the oauth2_scheme expects it.
    # The actual value of the token string doesn't matter here as verify_google_id_token is mocked.
    headers = {"Authorization": "Bearer fake_google_id_token_for_me_endpoint"}
    response = client.get(f"{settings.API_V1_STR}/auth/users/me", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == NEW_USER_GOOGLE_PAYLOAD["email"]
    assert data["google_id"] == NEW_USER_GOOGLE_PAYLOAD["sub"]

    # Verify that the mock was called correctly by your app.core.security.verify_google_id_token
    mock_google_lib_verify.assert_called_once_with(
        "fake_google_id_token_for_me_endpoint", # The token string
        mocker.ANY, # The GOOGLE_REQUEST_SESSION object
        settings.GOOGLE_CLIENT_ID
    )

def test_read_users_me_unauthenticated(client: TestClient):
    response = client.get(f"{settings.API_V1_STR}/auth/users/me")
    assert response.status_code == 401 # Depends on how OAuth2PasswordBearer handles missing token
                                      # It might be 403 if it's caught by FastAPI as missing auth
                                      # TestClient usually makes it 401 if scheme is enforced