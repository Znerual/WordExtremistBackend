# tests/conftest.py
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool # For SQLite in-memory with multiple test functions

from app.main import app # Your FastAPI app instance
from app.db.base import Base # Your SQLAlchemy Base
from app.api.deps import get_db # The dependency we need to override
from app.core.config import settings # To potentially modify settings for tests


# Use a different database URL for testing (SQLite in-memory)
SQLALCHEMY_DATABASE_URL_TEST = "sqlite:///:memory:"
SQLALCHEMY_DATABASE_URL_TEST_IN_MEMORY = "sqlite:///:memory:"
# SQLALCHEMY_DATABASE_URL_TEST = "sqlite:///./test.db" # Or a file-based SQLite

engine = create_engine(
    SQLALCHEMY_DATABASE_URL_TEST,
    # connect_args are specific to SQLite
    connect_args={"check_same_thread": False}, # Needed for SQLite
    poolclass=StaticPool # Ensures the same in-memory DB is used across a test session for SQLite
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create tables for the test database before tests run
Base.metadata.create_all(bind=engine)

# Override the get_db dependency for testing
def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

@pytest.fixture(scope="session", autouse=True)
def create_test_tables():
    """
    Create database tables before any tests run in the session.
    This fixture will run automatically for the whole test session.
    """
    Base.metadata.create_all(bind=engine)
    yield
    # Optional: Clean up if using a file-based test.db
    # Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function", autouse=False) # function scope for a fresh DB per test if needed
def db_session():
    """
    Provides a database session for a test function.
    Rolls back changes after the test to ensure isolation.
    """
    # Create tables fresh for each test function that uses this fixture.
    # This ensures a clean slate if previous TestClient calls committed data.
    Base.metadata.drop_all(bind=engine) # Drop all tables
    Base.metadata.create_all(bind=engine) # Recreate all tables

    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    yield session # provide the session to the test

    session.close()
    transaction.rollback() # Rollback to ensure test isolation
    connection.close()


@pytest.fixture(scope="module")
def client() -> TestClient:
    """
    Provides a TestClient instance for making API requests.
    This client will use the overridden get_db dependency.
    """
    return TestClient(app)

@pytest.fixture(autouse=True)
def reset_in_memory_state():
    """
    Fixture to automatically clear in-memory singleton state
    before each test function runs.
    """
    try:
      from app.services import matchmaking_service
      from app.api.websockets import game_manager # Access the global manager

      # Clear state before running the test
      matchmaking_service.waiting_players.clear()
      matchmaking_service.active_games.clear()
      game_manager.active_connections.clear()
     # print("\n---CLEARED IN-MEMORY STATE---")
    except ImportError:
       # Module might not exist yet, or this test run doesn't involve it.
       pass
       
    yield # Run the test

    # --- Cleanup after test is not strictly needed if we clear BEFORE ---
    # --- but can be helpful if a test fails mid-way ---
    try:
      from app.services import matchmaking_service
      from app.api.websockets import game_manager
      matchmaking_service.waiting_players.clear()
      matchmaking_service.active_games.clear()
      game_manager.active_connections.clear()
    except ImportError:
      pass