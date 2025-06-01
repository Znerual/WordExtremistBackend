# app/main.py
# Start the Postgres server using docker run -d --name word_extremist_db -e POSTGRES_USER=backend -e POSTGRES_PASSWORD=password -e POSTGRES_DB=word_extremist_db -p 5432:5432 -v pgdata:/var/lib/postgresql/data postgres:15
# Start backend using uvicorn app.main:app --reload --host 0.0.0.0
import logging
import logging.config
import json
import pathlib
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.routing import APIRoute, APIWebSocketRoute, Mount
from app.core.config import settings
from app.api import admin as admin_router
from app.api import auth as auth_router
from app.api import game_data as game_data_router
from app.api import websockets as websocket_router
from app.api import matchmaking as matchmaking_router
from app.db.base import Base # For initial table creation if not using Alembic
from app.db.session import engine

_queue_handler_instance: Optional[logging.handlers.QueueHandler] = None # Module-level variable

def configure_logging_from_file():
    """Loads logging configuration from the JSON file and identifies the QueueHandler."""
    global _queue_handler_instance
    config_file = pathlib.Path(__file__).parent / "logging_config.json"
    try:
        with open(config_file) as f_in:
            config = json.load(f_in)
        
        log_dir = pathlib.Path("logs")
        log_dir.mkdir(exist_ok=True)

        logging.config.dictConfig(config)
        
        # Find the QueueHandler instance to start/stop its listener later
        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            if isinstance(handler, logging.handlers.QueueHandler):
                _queue_handler_instance = handler
                break
        
        if not _queue_handler_instance:
            # This log might go to a default handler if root has no handlers yet,
            # or if dictConfig itself fails before setting up handlers.
            # A print might be more reliable here if dictConfig itself could fail.
            # However, if dictConfig succeeds but no QueueHandler is found, this will use the configured logging.
            logging.getLogger("app.main.logging_setup").error(
                "QueueHandler not found in root logger. Off-thread logging will not work as intended."
            )
    except Exception as e:
        # Fallback to basic config if file loading or dictConfig fails
        print(f"ERROR: Failed to configure logging from file: {e}. Falling back to basic stdout logging.")
        logging.basicConfig(level=logging.INFO, format='%(levelname)-8s [%(name)s] %(message)s')
        logging.getLogger("app.main.logging_setup").error("Logging configuration failed.", exc_info=True)


# Configure logging when the module is loaded. Listener is started/stopped by lifespan.
configure_logging_from_file()
logger = logging.getLogger("app.main") # Logger for this module

# If using Alembic, you don't need this here.
# For simple setup, you can create tables like this (run once):
def create_tables():
    Base.metadata.create_all(bind=engine)
create_tables()


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

# Include Routers
app.include_router(auth_router.router, prefix=settings.API_V1_STR + "/auth", tags=["Auth"])
app.include_router(admin_router.router, prefix="/admin", include_in_schema=False)  # Admin routes are not in OpenAPI schema
app.include_router(matchmaking_router.router, prefix=settings.API_V1_STR + "/matchmaking", tags=["Matchmaking"])
app.include_router(game_data_router.router, prefix=settings.API_V1_STR + "/game-content", tags=["Game Content"])
app.include_router(websocket_router.router, tags=["Game Sockets"]) # WebSockets usually don't have API prefix

# --- Add this block for debugging ---
logger.info("--- FastAPI Registered Routes ---")
for route in app.routes:
    if isinstance(route, APIRoute):
        logger.info(f"Path: {route.path}, Methods: {route.methods}, Name: {route.name}")
    elif isinstance(route, Mount):
        logger.info(f"Mount Path: {route.path}, App: {route.app.__class__.__name__}")
    elif isinstance(route, APIWebSocketRoute):
        logger.info(f"WebSocket Path: {route.path}, Name: {route.name}")
    else:
        logger.info(f"Other Route Type: {type(route)}")
logger.info("--- End Registered Routes ---\n")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Code to execute during application startup
    logger.info("Application startup sequence initiated...")
    if _queue_handler_instance and hasattr(_queue_handler_instance, 'listener'):
        try:
            _queue_handler_instance.listener.start()
            logger.info("Logging QueueListener started successfully.")
        except Exception as e:
            logger.error(f"Failed to start QueueListener: {e}", exc_info=True)
    else:
        logger.warning("QueueHandler or its listener not found; off-thread logging might not be active.")

    # --- Your other startup logic (e.g., DB checks, initial data loading) ---
    # For example, if you're not using Alembic and want to ensure tables:
    # create_tables()
    # logger.info("Database tables checked/created (if not using Alembic).")
    # ---

    yield  # This is where the application will run

    # Code to execute during application shutdown
    logger.info("Application shutdown sequence initiated...")
    if _queue_handler_instance and hasattr(_queue_handler_instance, 'listener'):
        try:
            logger.info("Attempting to stop Logging QueueListener...")
            _queue_handler_instance.listener.stop()
            logger.info("Logging QueueListener stopped successfully.")
        except Exception as e:
            logger.error(f"Failed to stop QueueListener gracefully: {e}", exc_info=True)
    # --- Your other shutdown logic (e.g., closing DB connections if not handled by SessionLocal) ---


@app.get(settings.API_V1_STR + "/health", tags=["Health Check"])
async def health_check():
    return {"status": "healthy", "project": settings.PROJECT_NAME}

# For development with uvicorn: uvicorn app.main:app --reload