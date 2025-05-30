# app/crud/crud_user.py
from typing import Any, Dict
from sqlalchemy.orm import Session
from app.schemas.user import User
from app.models.user import UserCreateFromGoogle, UserCreateFromPGS, GetOrCreateUserRequest  # Use this Pydantic model
from app.core.config import settings

def get_user(db: Session, user_id: int) -> User | None:
    return db.query(User).filter(User.id == user_id).first()

def get_user_by_client_provided_id(db: Session, client_id: str) -> User | None:
    return db.query(User).filter(User.client_provided_id == client_id).first()

def create_user_for_device_login(db: Session, client_id: str, hashed_password_val: str, username: str) -> User:
    db_user = User(
        client_provided_id=client_id,
        play_games_player_id=client_id,  # Assuming client_provided_id is used as play_games_player_id
        hashed_password=hashed_password_val,
        username=username,
        is_active=True
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def create_user_with_client_provided_id(db: Session, user_in: GetOrCreateUserRequest) -> User:
    default_username = user_in.username or f"Player_{user_in.client_provided_id[:8]}"
    
    # Check if username already exists, append random chars if it does to avoid non-unique username if you have constraint
    # existing_user_with_username = db.query(User).filter(User.username == default_username).first()
    # if existing_user_with_username:
    #     default_username = f"{default_username}_{uuid.uuid4().hex[:4]}"

    db_user = User(
        client_provided_id=user_in.client_provided_id,
        play_games_player_id=user_in.client_provided_id,
        username=default_username,
        # Other fields can be null or have defaults as per your User schema
        is_active=True
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def get_user_by_google_id(db: Session, google_id: str) -> User | None:
    return db.query(User).filter(User.google_id == google_id).first()

def get_user_by_email(db: Session, email: str) -> User | None: # Still useful
    return db.query(User).filter(User.email == email).first()

def get_user_by_play_games_player_id(db: Session, play_games_player_id: str) -> User | None:
    return db.query(User).filter(User.play_games_player_id == play_games_player_id).first()

def create_user_from_pgs_info(db: Session, user_in: UserCreateFromPGS) -> User:
    db_user = User(
        play_games_player_id=user_in.play_games_player_id,
        email=user_in.email, # Might be null
        username=user_in.username, # Might be null
        profile_pic_url=str(user_in.profile_pic_url) if user_in.profile_pic_url else None,
        # google_refresh_token=refresh_token # If you decide to store it
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def create_user_from_google_info(db: Session, user_in: UserCreateFromGoogle, commit_db: bool = True) -> User:
    db_user = User(
        google_id=user_in.google_id,
        email=user_in.email,
        username=user_in.username or user_in.email.split('@')[0], # Default username
        profile_pic_url=str(user_in.profile_pic_url) if user_in.profile_pic_url else None
    )
    db.add(db_user)
    if commit_db:
        try:
            db.commit()
            db.refresh(db_user)
        except Exception as e:
            db.rollback() # Rollback on error during commit
            raise e
    else:
        db.flush()
        db.refresh(db_user)
    return db_user

def update_user_login_info(db: Session, user: User) -> User:
    # Example: Update last_login_at, potentially refresh username/pic from Google
    from sqlalchemy.sql import func # For func.now()
    user.last_login_at = func.now()
    # If you want to update username/pic from Google token on each login:
    # user.username = new_username_from_token
    # user.profile_pic_url = new_pic_url_from_token
    db.commit()
    db.refresh(user)
    return user

def create_user_admin(db: Session, user_data: Dict[str, Any]) -> User:
    """
    Creates a user with arbitrary data provided by an admin.
    Handles potential None values for fields that are nullable in the DB.
    """
    # Ensure essential fields like is_active have defaults if not provided
    user_data.setdefault('is_active', True)
    user_data.setdefault('level', 1) # Default if not provided by admin
    user_data.setdefault('experience', 0) # Default if not provided by admin

    # Convert empty strings for optional fields to None
    for key in ['email', 'profile_pic_url', 'username', 
                'client_provided_id', 'play_games_player_id', 'google_id']: # Add all relevant optional string fields
        if key in user_data and user_data[key] == '':
            user_data[key] = None

    # Convert level and experience to int if they come as strings from form
    if 'level' in user_data and isinstance(user_data['level'], str):
        try:
            user_data['level'] = int(user_data['level'])
        except ValueError:
            user_data['level'] = 1 # Default if conversion fails
    if 'experience' in user_data and isinstance(user_data['experience'], str):
        try:
            user_data['experience'] = int(user_data['experience'])
        except ValueError:
            user_data['experience'] = 0 # Default if conversion fails

    # Filter out keys not in User model to prevent errors, or ensure user_data only contains valid keys
    # For simplicity, assuming user_data keys match User model attributes
    db_user = User(**user_data)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def update_user_admin(db: Session, user_id: int, user_update_data: Dict[str, Any]) -> User | None:
    """
    Updates a user's details by an admin.
    user_update_data should be a dict of fields to update.
    """
    db_user = get_user(db, user_id=user_id)
    if db_user:
        for key, value in user_update_data.items():
            # Only update if the field exists on the model and value is provided
            # (or if you want to allow setting to None explicitly)
            if hasattr(db_user, key):
                if value == '' and key in ['email', 'profile_pic_url', 'username', 
                                            'client_provided_id', 'play_games_player_id', 'google_id']:
                    setattr(db_user, key, None)

                elif key in ['level', 'experience']:
                    try:
                        setattr(db_user, key, int(value) if value is not None else (1 if key == 'level' else 0) )
                    except (ValueError, TypeError):
                        # Keep existing value or set default if conversion fails
                        setattr(db_user, key, getattr(db_user, key) or (1 if key == 'level' else 0))
                else:
                    setattr(db_user, key, value)
        db.commit()
        db.refresh(db_user)
        return db_user
    return None

def delete_user_admin(db: Session, user_id: int) -> bool:
    """Deletes a user by their ID."""
    db_user = get_user(db, user_id=user_id)
    if db_user:
        db.delete(db_user)
        db.commit()
        return True
    return False

def get_all_users_paginated(db: Session, skip: int = 0, limit: int = 100) -> list[User]:
    return db.query(User).order_by(User.id.desc()).offset(skip).limit(limit).all()

def add_experience_to_user(db: Session, user_id: int, exp_to_add: int) -> User | None:
    """Adds experience to a user and handles leveling up. Returns the updated User object."""
    user = get_user(db, user_id=user_id)
    if user:
        user.experience += exp_to_add
        print(f"User {user_id} ({user.username}) gained {exp_to_add} XP. Total XP: {user.experience}, Level: {user.level}")

        # Leveling up logic
        # Example: XP needed for next level = current_level * XP_PER_LEVEL_BASE
        xp_needed_for_next_level = user.level * settings.XP_PER_LEVEL_BASE * settings.XP_PER_LEVEL_MULTIPLIER ** (user.level - 1)  # Assuming XP_PER_LEVEL_BASE is defined in settings
        
        while user.experience >= xp_needed_for_next_level:
            user.level += 1
            print(f"User {user_id} ({user.username}) leveled up to Level {user.level}! XP remaining: {user.experience}")
           
        
        db.commit()
        db.refresh(user)
        return user
    return None