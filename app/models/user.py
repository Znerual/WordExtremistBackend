# app/models/user.py
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, HttpUrl
from datetime import date, datetime

class UserBase(BaseModel):
    email: EmailStr | None = None
    username: str | None = None # Display name
    profile_pic_url: HttpUrl | None = None
    level: int = Field(default=1)
    experience: int = Field(default=0)
    words_count: int = Field(default=0)
    country: Optional[str] = Field(None, max_length=2, description="ISO 3166-1 alpha-2 country code")
    mother_tongue: Optional[str] = Field(None, max_length=10, description="BCP-47 language code (e.g., 'en', 'es-MX')")
    preferred_language: Optional[str] = Field(None, max_length=10, description="BCP-47 language code (e.g., 'en', 'es-MX')")
    birthday: Optional[date] = None
    gender: Optional[str] = Field(None, max_length=50)
    language_level: Optional[str] = Field(None, max_length=50, description="e.g., A1, B2, native")

class UserCreateFromPGS(UserBase):
    play_games_player_id: str

class UserCreateFromGoogle(UserBase): # Data extracted from Google ID Token
    google_id: str
    # email, username, profile_pic_url already in UserBase

class UserUpdate(BaseModel): # For user to update their editable profile info
    username: str | None = None
    profile_pic_url: HttpUrl | None = None
    # other updatable fields

class UserOptionalInfoUpdate(BaseModel):
    country: Optional[str] = Field(None, max_length=2)
    mother_tongue: Optional[str] = Field(None, max_length=10)
    preferred_language: Optional[str] = Field(None, max_length=10)
    birthday: Optional[date] = None
    gender: Optional[str] = Field(None, max_length=50)
    language_level: Optional[str] = Field(None, max_length=50)

class UserInDBBase(UserBase):
    id: int # Your internal ID
    play_games_player_id: str | None = None # << Add/Ensure this exists and is optional
    client_provided_id: Optional[str] = None # Add if missing, make Optional if not always present
    is_active: bool
    is_superuser: bool = False # Default to False, set to True for admin users
    is_bot: bool = False # Default to False, set to True for bot accounts
    created_at: datetime
    last_login_at: datetime | None = None

    class Config:
        from_attributes = True

class UserPublic(UserInDBBase):
    pass

class DeviceLoginRequest(BaseModel):
    client_provided_id: str
    client_generated_password: str

# Token model for your *own* backend-issued JWT (if you choose to issue one after Google validation)
class BackendToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic | None = None  # Optional: Include user info if needed
    expires_in: int
    
class ServerAuthCodeRequest(BaseModel):
    server_auth_code: str

class GetOrCreateUserRequest(BaseModel):
    client_provided_id: str # This could be a device ID, a temporary user-chosen ID, etc.
    username: Optional[str] = None # Optional: Client can suggest a username
# If you are NOT issuing your own token and relying on Google ID tokens, you don't need BackendToken