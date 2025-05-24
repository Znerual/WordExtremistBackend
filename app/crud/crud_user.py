# app/crud/crud_user.py
from sqlalchemy.orm import Session
from app.schemas.user import User
from app.models.user import UserCreateFromGoogle, UserCreateFromPGS # Use this Pydantic model

def get_user(db: Session, user_id: int) -> User | None:
    return db.query(User).filter(User.id == user_id).first()

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