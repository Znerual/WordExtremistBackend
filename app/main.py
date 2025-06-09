# app/main.py
# Start the Postgres server using docker run -d --name word_extremist_db -e POSTGRES_USER=backend -e POSTGRES_PASSWORD=password -e POSTGRES_DB=word_extremist_db -p 5432:5432 -v pgdata:/var/lib/postgresql/data postgres:15
# Start backend using uvicorn app.main:app --reload --host 0.0.0.0
import asyncio
from datetime import date, timedelta
import logging
import logging.config
import json
import pathlib
from contextlib import asynccontextmanager
import time
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.routing import APIRoute, APIWebSocketRoute, Mount
from fastapi.staticfiles import StaticFiles
from sqlalchemy import and_, extract, func
from app.core.config import settings
from app.api import admin as admin_router
from app.api import auth as auth_router
from app.api import game_data as game_data_router
from app.api import websockets as websocket_router
from app.api import matchmaking as matchmaking_router
from app.api import monitoring as monitoring_router
from app.crud import crud_system
from app.db.base import Base # For initial table creation if not using Alembic
from app.db.session import SessionLocal, engine
from app.schemas.game_log import Game, GamePlayer, WordSubmission
from app.schemas.system import DailyActiveUser
from app.schemas.user import User
from app.services import matchmaking_service, word_validator

_queue_handler_instance: Optional[logging.handlers.QueueHandler] = None # Module-level variable
_monitoring_task: Optional[asyncio.Task] = None # For the background task
_matchmaking_bot_task: Optional[asyncio.Task] = None
_api_stats = {"total_requests": 0, "errors_5xx": 0}

async def metrics_middleware(request: Request, call_next):
    global _api_stats
    _api_stats["total_requests"] += 1
    start_time = time.time()
    try:
        response = await call_next(request)
        if response.status_code >= 500:
            _api_stats["errors_5xx"] += 1
        return response
    except Exception as e:
        _api_stats["errors_5xx"] += 1
        # Log this severe error to the DB
        try:
            db = SessionLocal()
            crud_system.create_alert(db, "CRITICAL", "Unhandled Exception in Middleware", str(e))
            db.close()
        except Exception as alert_e:
            logger.critical(f"FATAL: Could not log unhandled exception to DB: {alert_e}")
        raise e
    
async def matchmaking_bot_check_task(interval_seconds: int = 5):
    """A background task that periodically matches lonely players with bots."""
    while True:
        await asyncio.sleep(interval_seconds) # Sleep first to avoid running on immediate startup
        try:
            db = SessionLocal()
            # Iterate over a copy of the items to allow modification
            for lang, pool in list(matchmaking_service.waiting_players_by_lang.items()):
                if len(pool) == 1:
                    player, joined_time = pool[0]
                    if (time.time() - joined_time) > settings.MATCHMAKING_BOT_THRESHOLD_SECONDS:
                        logger.info(f"Player {player.username} waiting > {settings.MATCHMAKING_BOT_THRESHOLD_SECONDS}s. Finding a bot.")
                        
                        # Remove player from pool first to prevent double matching
                        matchmaking_service.remove_player_from_matchmaking_pool(player.id)
                        
                        # Create the match in the service. This populates active_games
                        game_id, bot_user = matchmaking_service.create_bot_match(player, lang, db)
                        
                        # Update the polling status for the human player
                        # This requires importing from the matchmaking API file
                        from app.api.matchmaking import player_match_status, MatchmakingStatusResponse
                        game_state = matchmaking_service.get_game_info(game_id)
                        
                        
                        player_match_status[player.id] = MatchmakingStatusResponse(
                            status="matched", 
                            game_id=game_id, 
                            language=lang, 
                            opponent_name=bot_user.username,
                            opponent_profile_pic_url=str(bot_user.profile_pic_url) if bot_user.profile_pic_url else None,
                            player1_id=game_state.matchmaking_player_order[0],
                            player2_id=game_state.matchmaking_player_order[1],
                            your_player_id_in_game=player.id,
                            opponent_level=bot_user.level
                        )

                        
        except Exception as e:
            logger.error(f"Error in matchmaking bot check task: {e}", exc_info=True)
            try:
                # Log this failure to the alerts DB
                db_alert = SessionLocal()
                crud_system.create_alert(db_alert, "ERROR", "Matchmaking bot task failed", str(e))
                db_alert.close()
            except Exception as alert_e:
                logger.critical(f"FATAL: Could not log matchmaking bot task failure to DB: {alert_e}")
        finally:
            if 'db' in locals() and db.is_active:
                db.close()
    

async def capture_monitoring_snapshot_task(interval_seconds: int = 3600):
    """A background task that periodically captures system metrics."""
    while True:
        try:
            logger.info("Running periodic monitoring snapshot task...")
            db = SessionLocal()
            today = date.today()
            thirty_days_ago = today - timedelta(days=30)

            # --- CALCULATE ALL METRICS ---
            # Engagement
            new_users_q = db.query(User).filter(User.created_at >= today, User.is_bot == False).count()
            dau_q = (
                db.query(func.count(func.distinct(DailyActiveUser.user_id))) # Using distinct is more robust
                .join(User, User.id == DailyActiveUser.user_id) # Explicitly join User table
                .filter(
                    DailyActiveUser.activity_date == today,
                    User.is_bot == False
                )
                .scalar()
            )
            mau_q = (
                db.query(func.count(func.distinct(DailyActiveUser.user_id)))
                .join(User, User.id == DailyActiveUser.user_id) # Explicitly join User table
                .filter(
                    DailyActiveUser.activity_date >= thirty_days_ago,
                    User.is_bot == False
                )
                .scalar()
            )
            
            # Game Health
            games_finished_q = db.query(Game).filter(Game.status == 'finished').count()
            games_abandoned_q = db.query(Game).filter(Game.status.like('%abandoned%')).count()
            
            avg_duration_q = db.query(func.avg(extract('epoch', Game.end_time) - extract('epoch', Game.start_time))).filter(Game.status == 'finished', Game.end_time.isnot(None)).scalar() or 0

            # P1 Win Rate
            p1_wins = db.query(Game).join(GamePlayer, and_(Game.id == GamePlayer.game_id, Game.winner_user_id == GamePlayer.user_id)).filter(GamePlayer.player_order == 1, Game.status == 'finished').count()
            total_decided_games = db.query(Game).filter(Game.status == 'finished', Game.winner_user_id.isnot(None)).count()
            p1_win_rate = (p1_wins / total_decided_games) if total_decided_games > 0 else 0.5
            
            # System Performance
            api_error_rate = (_api_stats["errors_5xx"] / _api_stats["total_requests"] * 100) if _api_stats["total_requests"] > 0 else 0
            gemini_avg_latency = db.query(func.avg(WordSubmission.validation_latency_ms)).filter(WordSubmission.validation_latency_ms.isnot(None)).scalar() or 0
            cache_hit_rate = (word_validator.validation_stats["cache_hits"] / word_validator.validation_stats["total_calls"] * 100) if word_validator.validation_stats["total_calls"] > 0 else 0
            
            # Live Metrics
            live_players_in_matchmaking = sum(len(pool) for pool in matchmaking_service.waiting_players_by_lang.values())
            live_active_games = sum(1 for gs in matchmaking_service.active_games.values() if gs.status == 'in_progress')
            live_active_players_in_game = sum(len(gs.players) for gs in matchmaking_service.active_games.values() if gs.status == 'in_progress')
            live_concurrent_ws = sum(len(conns) for conns in websocket_router.game_manager.active_connections.values())

            metrics = {
                "players_in_matchmaking": live_players_in_matchmaking,
                "active_players_in_game": live_active_players_in_game,
                "active_games": live_active_games,
                "concurrent_websockets": live_concurrent_ws,
                "new_users_today": new_users_q,
                "dau": dau_q,
                "mau": mau_q,
                "total_games_finished": games_finished_q,
                "total_games_abandoned": games_abandoned_q,
                "avg_game_duration_seconds": float(avg_duration_q),
                "p1_win_rate": p1_win_rate,
                "api_error_rate_5xx_percent": api_error_rate,
                "gemini_avg_latency_ms": float(gemini_avg_latency),
                "gemini_cache_hit_rate_percent": cache_hit_rate,
            }
            
            crud_system.create_monitoring_snapshot(db, metrics=metrics)
            
        except Exception as e:
            logger.error(f"Error in monitoring snapshot task: {e}", exc_info=True)
            try:
                # Try to log the failure to the alerts DB
                db_alert = SessionLocal()
                crud_system.create_alert(
                    db_alert,
                    level="ERROR",
                    message="Monitoring snapshot task failed",
                    details=str(e)
                )
                db_alert.close()
            except Exception as alert_e:
                logger.critical(f"Failed to log monitoring task failure to DB: {alert_e}")
        finally:
            if 'db' in locals() and db.is_active:
                db.close()

        await asyncio.sleep(interval_seconds)

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
            # Use a temporary logger name to avoid conflict if "app.main" isn't configured yet
            temp_logger = logging.getLogger("app.main.logging_setup_check")
            temp_logger.error(
                "QueueHandler not found in root logger. Off-thread logging will not work as intended."
            )
    except FileNotFoundError:
        print(f"ERROR: Logging configuration file not found at {config_file}. Falling back to basic stdout logging.")
        logging.basicConfig(level=logging.INFO, format='%(levelname)-8s [%(name)s] %(message)s')
        logging.getLogger("app.main.logging_setup_fallback").error("Logging configuration file missing.", exc_info=True)
    except json.JSONDecodeError as e:
        print(f"ERROR: Failed to parse logging configuration file {config_file}: {e}. Falling back to basic stdout logging.")
        logging.basicConfig(level=logging.INFO, format='%(levelname)-8s [%(name)s] %(message)s')
        logging.getLogger("app.main.logging_setup_fallback").error("Logging configuration JSON error.", exc_info=True)
    except Exception as e:
        # Fallback to basic config if file loading or dictConfig fails for other reasons
        print(f"ERROR: Failed to configure logging from file: {e}. Falling back to basic stdout logging.")
        logging.basicConfig(level=logging.INFO, format='%(levelname)-8s [%(name)s] %(message)s')
        logging.getLogger("app.main.logging_setup_fallback").error("General logging configuration failed.", exc_info=True)


# Configure logging when the module is loaded. Listener is started/stopped by lifespan.
configure_logging_from_file()
logger = logging.getLogger("app.main") # Logger for this module

# If using Alembic, you don't need this here.
# For simple setup, you can create tables like this (run once):
def create_tables():
    Base.metadata.create_all(bind=engine)
create_tables()



@asynccontextmanager
async def lifespan(app: FastAPI):
    global _monitoring_task
    global _matchmaking_bot_task
    
    # Code to execute during application startup
    logger.info("Application startup sequence initiated...")
    if _queue_handler_instance and hasattr(_queue_handler_instance, 'listener'):
        try:
            _queue_handler_instance.listener.start()
            logger.info("Logging QueueListener started successfully via lifespan.")
        except Exception as e:
            logger.error(f"Failed to start QueueListener in lifespan: {e}", exc_info=True)
    else:
        logger.warning("QueueHandler or its listener not found during startup; off-thread logging might not be active.")

    # --- Your other startup logic ---
    # Example: Create tables if not using Alembic migrations and want it on app start
    # create_tables()
    # logger.info("Database tables checked/created (if applicable).")
    # ---

    logger.info("Starting monitoring background task...")
    # Run every hour (3600s). For testing, you can set it to a lower value like 60.
    _monitoring_task = asyncio.create_task(capture_monitoring_snapshot_task(settings.MONITORING_SNAPSHOT_INTERVAL_SECONDS))
    _matchmaking_bot_task = asyncio.create_task(matchmaking_bot_check_task(interval_seconds=15)) # Check every 15s
    yield  # This is where the application will run

    if _monitoring_task:
        logger.info("Cancelling monitoring background task...")
        _monitoring_task.cancel()
        try:
            await _monitoring_task
        except asyncio.CancelledError:
            logger.info("Monitoring background task successfully cancelled.")

    # Code to execute during application shutdown
    logger.info("Application shutdown sequence initiated...")
    if _queue_handler_instance and hasattr(_queue_handler_instance, 'listener'):
        try:
            logger.info("Attempting to stop Logging QueueListener...")
            _queue_handler_instance.listener.stop()
            logger.info("Logging QueueListener stopped successfully via lifespan.")
        except Exception as e:
            logger.error(f"Failed to stop QueueListener gracefully in lifespan: {e}", exc_info=True)

    if _matchmaking_bot_task:
        logger.info("Cancelling matchmaking bot check background task...")
        _matchmaking_bot_task.cancel()
        try:
            await _matchmaking_bot_task
        except asyncio.CancelledError:
            logger.info("Matchmaking bot check task successfully cancelled.")

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)
app.middleware("http")(metrics_middleware)  # Add the metrics middleware
app.mount("/static", StaticFiles(directory=settings.UPLOADS_DIR.parent), name="static")

# Include Routers
app.include_router(auth_router.router, prefix=settings.API_V1_STR + "/auth", tags=["Auth"])
app.include_router(matchmaking_router.router, prefix=settings.API_V1_STR + "/matchmaking", tags=["Matchmaking"])
app.include_router(game_data_router.router, prefix=settings.API_V1_STR + "/game-content", tags=["Game Content"])
app.include_router(monitoring_router.router, prefix=settings.API_V1_STR + "/monitoring", tags=["Monitoring"])
app.include_router(websocket_router.router, tags=["Game Sockets"]) # WebSockets usually don't have API prefix
app.include_router(admin_router.router, prefix="/admin", include_in_schema=False)  # Admin routes are not in OpenAPI schema
#app.include_router(admin_router.protected_router, prefix="/admin", include_in_schema=False)  # Admin routes are not in OpenAPI schema

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


@app.get(settings.API_V1_STR + "/health", tags=["Health Check"])
async def health_check():
    return {"status": "healthy", "project": settings.PROJECT_NAME}

# For development with uvicorn: uvicorn app.main:app --reload