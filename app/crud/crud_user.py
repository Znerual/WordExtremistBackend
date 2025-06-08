# app/crud/crud_user.py
import logging
from typing import Any, Dict
import uuid
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.schemas.system import DailyActiveUser
from app.schemas.user import User
from app.models.user import UserCreateFromGoogle, UserCreateFromPGS, GetOrCreateUserRequest  # Use this Pydantic model
from app.core.config import settings
from datetime import date

logger = logging.getLogger("app.crud.user")  # Logger for this module

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

    logger.info(f"Created new user with client_provided_id: {client_id}, username: {username}")

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

    logger.info(f"Created new user with client_provided_id: {user_in.client_provided_id}, username: {default_username}")

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

    logger.info(f"Created new user with Play Games Player ID: {user_in.play_games_player_id}, username: {user_in.username}")

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
            logger.exception(f"Error committing new user to DB: {e}")
            db.rollback() # Rollback on error during commit
            raise e
    else:
        db.flush()
        db.refresh(db_user)

    logger.info(f"Created new user with Google ID: {user_in.google_id}, username: {user_in.username}")

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

    logger.info(f"Updated login info for user: {user.username} (ID: {user.id})")

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
    user_data.setdefault('words_count', int(user_data.get('words_count', 0)))

    # Convert empty strings for optional fields to None
    fields_to_clean = [
        'email', 'profile_pic_url', 'username', 
        'client_provided_id', 'play_games_player_id', 'google_id',
        'country', 'mother_tongue', 'preferred_language', 'gender', 'language_level',
        'birthday'
    ]
    for key in fields_to_clean:
        if key in user_data and user_data[key] == '':
            user_data[key] = None

    # Ensure numeric fields are integers
    for num_field in ['level', 'experience', 'words_count']:
        if num_field in user_data and isinstance(user_data[num_field], str):
            try: user_data[num_field] = int(user_data[num_field])
            except ValueError: user_data[num_field] = 0 # Or default specific to field

    # Filter out keys not in User model to prevent errors, or ensure user_data only contains valid keys
    # For simplicity, assuming user_data keys match User model attributes
    db_user = User(**user_data)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)

    logger.info(f"Admin created new user: {db_user.username} (ID: {db_user.id}) with data: {user_data}")

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
                string_fields_to_nullify = [
                    'email', 'profile_pic_url', 'username', 
                    'client_provided_id', 'play_games_player_id', 'google_id',
                    'country', 'mother_tongue', 'preferred_language', 'gender', 'language_level'
                ]

                if value == '' and key in string_fields_to_nullify:
                    setattr(db_user, key, None)
                elif value == '' and key == 'birthday': # Special case for date field
                    setattr(db_user, key, None)
                elif key in ['level', 'experience', 'words_count']:
                    try:
                        setattr(db_user, key, int(value) if value is not None else (1 if key == 'level' else 0) )
                    except (ValueError, TypeError):
                        # Keep existing value or set default if conversion fails
                        setattr(db_user, key, getattr(db_user, key) or (1 if key == 'level' else 0))
                else:
                    setattr(db_user, key, value)
        db.commit()
        db.refresh(db_user)

        logger.info(f"Admin updated user {db_user.username} (ID: {db_user.id}) with data: {user_update_data}")

        return db_user
    
    logger.error(f"Admin tried to update non-existent user with ID: {user_id}")
    return None

def delete_user_admin(db: Session, user_id: int) -> bool:
    """Deletes a user by their ID."""
    db_user = get_user(db, user_id=user_id)
    if db_user:
        db.delete(db_user)
        db.commit()
        logger.info(f"Admin deleted user with ID: {user_id} (Username: {db_user.username})")

        return True
    
    logger.error(f"Admin tried to delete non-existent user with ID: {user_id}")
    return False

def get_all_users_paginated(db: Session, skip: int = 0, limit: int = 100) -> list[User]:
    return db.query(User).order_by(User.id.desc()).offset(skip).limit(limit).all()

def add_experience_to_user(db: Session, user_id: int, exp_to_add: int) -> User | None:
    """Adds experience to a user and handles leveling up. Returns the updated User object."""
    user = get_user(db, user_id=user_id)
    if user:
        user.experience += exp_to_add
        logger.info(f"User {user_id} ({user.username}) gained {exp_to_add} XP. Total XP: {user.experience}, Level: {user.level}")

        # Leveling up logic
        # Example: XP needed for next level = current_level * XP_PER_LEVEL_BASE
        xp_needed_for_next_level = user.level * settings.XP_PER_LEVEL_BASE * settings.XP_PER_LEVEL_MULTIPLIER ** (user.level - 1)  # Assuming XP_PER_LEVEL_BASE is defined in settings
        
        while user.experience >= xp_needed_for_next_level:
            user.level += 1
            xp_needed_for_next_level = user.level * settings.XP_PER_LEVEL_BASE * settings.XP_PER_LEVEL_MULTIPLIER ** (user.level - 1)
            logger.info(f"User {user_id} ({user.username}) leveled up to Level {user.level}! XP remaining: {user.experience}")
           
        
        db.commit()
        db.refresh(user)
        return user
    return None

def increment_user_words_count(db: Session, user_id: int, count: int = 1) -> User | None:
    """Increments the words_count for a user."""
    user = get_user(db, user_id=user_id)
    if user:
        user.words_count = (user.words_count or 0) + count # Handle if words_count was somehow None
        db.commit()
        db.refresh(user)
        logger.debug(f"User {user_id} ({user.username}) words_count incremented to {user.words_count}")
        return user
    return None

def log_daily_active_user(db: Session, user_id: int):
    """
    Logs a user's activity for the current day.
    Uses a raw SQL INSERT with ON CONFLICT DO NOTHING for high performance
    and to avoid race conditions or duplicate entries.
    """
    today = date.today()
    try:
        # This is more efficient than a SELECT then INSERT
        stmt = text("""
            INSERT INTO dailyactiveusers (user_id, activity_date)
            VALUES (:user_id, :activity_date)
            ON CONFLICT (user_id, activity_date) DO NOTHING;
        """)
        db.execute(stmt, {"user_id": user_id, "activity_date": today})
        db.commit()
    except Exception as e:
        logger.error(f"Failed to log daily active user {user_id}: {e}")
        db.rollback()

def get_or_create_bot_user(db: Session) -> User:
    """
    Finds or creates a single, persistent user account for the bot.
    The bot's display name will be randomized per-game, but the underlying user record is static.
    """
    # Use a consistent, unique identifier for the bot user record
    bot_email = "bot@wordextremist.game"
    bot_user = get_user_by_email(db, email=bot_email)
    
    if not bot_user:
        logger.info(f"Bot user record not found, creating a new one.")
        bot_user = User(
            email=bot_email,
            username="WordExtremist Bot", # Default name, will be overridden in-game
            is_bot=True,
            is_active=True,
            # A bot doesn't need a real PGS ID, but the field should be unique if not nullable.
            play_games_player_id=f"bot_user_id_{uuid.uuid4().hex[:12]}"
        )
        db.add(bot_user)
        db.commit()
        db.refresh(bot_user)
        logger.info(f"Bot user record created with ID: {bot_user.id}")

    return bot_user