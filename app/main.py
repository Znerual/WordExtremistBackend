# app/main.py
from fastapi import FastAPI
from fastapi.routing import APIRoute, APIWebSocketRoute, Mount
from app.core.config import settings
from app.api import auth as auth_router
from app.api import game_data as game_data_router
from app.api import websockets as websocket_router
from app.api import matchmaking as matchmaking_router
# from app.db.base import Base # For initial table creation if not using Alembic
# from app.db.session import engine

# If using Alembic, you don't need this here.
# For simple setup, you can create tables like this (run once):
# def create_tables():
#    Base.metadata.create_all(bind=engine)
# create_tables()


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

# Include Routers
#app.include_router(auth_router.router, prefix=settings.API_V1_STR + "/auth", tags=["Auth"])
app.include_router(matchmaking_router.router, prefix=settings.API_V1_STR + "/matchmaking", tags=["Matchmaking"])
app.include_router(game_data_router.router, prefix=settings.API_V1_STR + "/game-content", tags=["Game Content"])
app.include_router(websocket_router.router, tags=["Game Sockets"]) # WebSockets usually don't have API prefix


# --- Add this block for debugging ---
print("\n--- FastAPI Registered Routes ---")
for route in app.routes:
    if isinstance(route, APIRoute):
        print(f"Path: {route.path}, Methods: {route.methods}, Name: {route.name}")
    elif isinstance(route, Mount):
         print(f"Mount Path: {route.path}, App: {route.app.__class__.__name__}")
    elif isinstance(route, APIWebSocketRoute):
         print(f"WebSocket Path: {route.path}, Name: {route.name}")
    else:
         print(f"Other Route Type: {type(route)}")
print("--- End Registered Routes ---\n")

@app.get(settings.API_V1_STR + "/health", tags=["Health Check"])
async def health_check():
    return {"status": "healthy", "project": settings.PROJECT_NAME}

# For development with uvicorn: uvicorn app.main:app --reload