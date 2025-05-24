# app/models/user.py
from pydantic import BaseModel, EmailStr, HttpUrl
from datetime import datetime

class UserBase(BaseModel):
    email: EmailStr | None = None
    username: str | None = None # Display name
    profile_pic_url: HttpUrl | None = None

class UserCreateFromPGS(UserBase):
    play_games_player_id: str

class UserCreateFromGoogle(UserBase): # Data extracted from Google ID Token
    google_id: str
    # email, username, profile_pic_url already in UserBase

class UserUpdate(BaseModel): # For user to update their editable profile info
    username: str | None = None
    # other updatable fields

class UserInDBBase(UserBase):
    id: int # Your internal ID
    play_games_player_id: str | None = None # << Add/Ensure this exists and is optional
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None

    class Config:
        from_attributes = True

class UserPublic(UserInDBBase):
    pass

# Token model for your *own* backend-issued JWT (if you choose to issue one after Google validation)
class BackendToken(BaseModel):
    access_token: str
    token_type: str = "bearer"

class ServerAuthCodeRequest(BaseModel):
    server_auth_code: str
# If you are NOT issuing your own token and relying on Google ID tokens, you don't need BackendToken