from pydantic import BaseModel, Field
from typing import Any, List, Dict, Optional
from datetime import datetime

from app.schemas.system import SystemAlert

class LiveStats(BaseModel):
    players_in_matchmaking: int
    active_players_in_game: int
    active_games: int

class HistoricalStatPoint(BaseModel):
    timestamp: datetime
    value: float

class SystemAlertPublic(BaseModel):
    id: int
    timestamp: datetime
    level: str
    message: str
    details: Optional[str] = None

    class Config:
        from_attributes = True

class KpiStats(BaseModel):
    dau: int
    mau: int
    abandonment_rate_percent: float
    api_error_rate_percent: float
    gemini_avg_latency_ms: float
    gemini_cache_hit_rate_percent: float

class LevelDistribution(BaseModel):
    level: int
    count: int

class FrequentError(BaseModel):
    message: str
    count: int

class MonitoringDataResponse(BaseModel):
    kpi_stats: KpiStats
    historical_stats: Dict[str, List[Dict[str, Any]]]
    alerts: List[SystemAlertPublic]
    level_distribution: List[LevelDistribution]
    frequent_errors: List[FrequentError]