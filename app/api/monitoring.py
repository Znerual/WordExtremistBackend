# app/api/monitoring.py
import logging
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from datetime import datetime

from app.api import deps
from app.crud import crud_system
from app.models.monitoring import FrequentError, KpiStats, LevelDistribution, MonitoringDataResponse, SystemAlertPublic
from app.schemas.system import SystemAlert
from app.schemas.user import User


logger = logging.getLogger("app.api.monitoring")
router = APIRouter()

# --- Pydantic Models for the API Response ---



# --- API Endpoint ---

@router.get("/data", response_model=MonitoringDataResponse)
async def get_monitoring_data(
    current_admin: deps.UserPublic = Depends(deps.get_current_admin_user),
    db: Session = Depends(deps.get_db)
):
    """
    Provides live and historical data for the admin monitoring dashboard.
    Requires admin authentication.
    """
    # 1. Get latest snapshot for KPIs
    latest_snapshot = crud_system.get_latest_snapshots(db, limit=1)[0] if crud_system.get_latest_snapshots(db, limit=1) else None
    
    total_games = (latest_snapshot.total_games_finished + latest_snapshot.total_games_abandoned) if latest_snapshot else 0
    abandon_rate = (latest_snapshot.total_games_abandoned / total_games * 100) if total_games > 0 else 0
    
    kpi_stats = KpiStats(
        dau=latest_snapshot.dau if latest_snapshot else 0,
        mau=latest_snapshot.mau if latest_snapshot else 0,
        abandonment_rate_percent=round(abandon_rate, 2),
        api_error_rate_percent=round(latest_snapshot.api_error_rate_5xx_percent, 2) if latest_snapshot else 0,
        gemini_avg_latency_ms=round(latest_snapshot.gemini_avg_latency_ms, 2) if latest_snapshot else 0,
        gemini_cache_hit_rate_percent=round(latest_snapshot.gemini_cache_hit_rate_percent, 2) if latest_snapshot else 0,
    )

    # 2. Get Historical Stats (last 100 snapshots)
    snapshots = crud_system.get_latest_snapshots(db, limit=100)
    snapshots.reverse()
    
    historical_stats = {
        "player_activity": [], "game_health": [], "system_performance": []
    }
    for s in snapshots:
        historical_stats["player_activity"].append({"timestamp": s.timestamp, "matchmaking": s.players_in_matchmaking, "in_game": s.active_players_in_game})
        historical_stats["game_health"].append({"timestamp": s.timestamp, "avg_duration": s.avg_game_duration_seconds, "p1_win_rate": s.p1_win_rate * 100 if s.p1_win_rate else 0})
        historical_stats["system_performance"].append({"timestamp": s.timestamp, "gemini_latency": s.gemini_avg_latency_ms, "api_errors": s.api_error_rate_5xx_percent})

    # 3. Get Player Level Distribution
    level_dist_q = db.query(User.level, func.count(User.id)).group_by(User.level).order_by(User.level).all()
    level_distribution = [LevelDistribution(level=level, count=count) for level, count in level_dist_q]

    # 4. Get Frequent Errors
    errors_q = db.query(SystemAlert.message, func.count(SystemAlert.id).label("count")).group_by(SystemAlert.message).order_by(func.count(SystemAlert.id).desc()).limit(10).all()
    frequent_errors = [FrequentError(message=msg, count=count) for msg, count in errors_q]

    # 5. Get all recent alerts
    alerts = crud_system.get_latest_alerts(db, limit=50)

    return MonitoringDataResponse(
        kpi_stats=kpi_stats,
        historical_stats=historical_stats,
        alerts=alerts,
        level_distribution=level_distribution,
        frequent_errors=frequent_errors,
    )