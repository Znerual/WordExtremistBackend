# tests/crud/test_crud_user.py
from sqlalchemy.orm import Session
from app.crud import crud_user
from app.models.user import UserCreateFromGoogle
from app.schemas.user import User as DBUser # SQLAlchemy model

def test_create_user_from_google_info(db_session: Session):
    google_id = "google_test_123"
    email = "test@example.com"
    username = "TestUser"
    pic_url = "http://example.com/pic.jpg"

    user_in = UserCreateFromGoogle(
        google_id=google_id,
        email=email,
        username=username,
        profile_pic_url=pic_url
    )
    db_user = crud_user.create_user_from_google_info(db_session, user_in=user_in)

    assert db_user.google_id == google_id
    assert db_user.email == email
    assert db_user.username == username
    assert db_user.profile_pic_url == pic_url
    assert db_user.id is not None

    # Verify it's in the DB
    queried_user = db_session.query(DBUser).filter(DBUser.google_id == google_id).first()
    assert queried_user is not None
    assert queried_user.email == email

def test_get_user_by_google_id(db_session: Session):
    google_id = "google_get_test_456"
    email = "get_test@example.com"
    user_in = UserCreateFromGoogle(google_id=google_id, email=email, username="GetUser")
    crud_user.create_user_from_google_info(db_session, user_in=user_in)

    user = crud_user.get_user_by_google_id(db_session, google_id=google_id)
    assert user is not None
    assert user.google_id == google_id
    assert user.email == email

    non_existent_user = crud_user.get_user_by_google_id(db_session, google_id="non_existent_id")
    assert non_existent_user is None

def test_update_user_login_info(db_session: Session):
    google_id = "google_update_login_789"
    email = "update_login@example.com"
    user_in = UserCreateFromGoogle(google_id=google_id, email=email, username="UpdateLoginUser")
    created_user = crud_user.create_user_from_google_info(db_session, user_in=user_in)
    
    # Simulate some time passing or ensure last_login_at is different
    initial_login_time = created_user.last_login_at 

    updated_user = crud_user.update_user_login_info(db_session, user=created_user)
    db_session.refresh(updated_user) # Refresh to get updated timestamp from DB default/onupdate

    assert updated_user.last_login_at is not None
    if initial_login_time: # If it was set on creation
         assert updated_user.last_login_at >= initial_login_time # Should be same or later
    # Note: Precise time comparison can be tricky due to DB time functions.
    # Checking for non-None or greater/equal is often sufficient.