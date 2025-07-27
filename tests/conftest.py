# tests/conftest.py
import pytest
import logging
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.db.base import Base
from app.api.deps import get_db

SQLALCHEMY_DATABASE_URL_TEST = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL_TEST,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    """Create tables once for the entire test session."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

def override_get_db():
    """Dependency override for test database sessions."""
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

@pytest.fixture(scope="function")
def db_session():
    """
    Provides a clean, isolated database session for each test function
    by using transactions and rollbacks.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()

@pytest.fixture(scope="module")
def client() -> TestClient:
    """Provides a TestClient for making API requests."""
    return TestClient(app)

@pytest.fixture(autouse=True)
def reset_in_memory_state():
    """Clears in-memory state before each test."""
    from app.services import matchmaking_service
    from app.api.websockets import game_manager
    from app.api.matchmaking import player_match_status

    matchmaking_service.waiting_players_by_lang.clear()
    matchmaking_service.active_games.clear()
    game_manager.active_connections.clear()
    player_match_status.clear()
    yield

def pytest_configure(config):
    """
    Hook to configure logging levels before tests are run.
    This silences noisy third-party libraries.
    """
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)