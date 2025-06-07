# app/schemas/system.py
from sqlalchemy import Column, String, Integer, DateTime, Float, Text, Date, UniqueConstraint, ForeignKey
from sqlalchemy.sql import func
from app.db.base_class import Base

class MonitoringSnapshot(Base):
    """Stores a snapshot of system metrics at a specific time."""
    __tablename__ = "monitoringsnapshots"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    
    # Live Metrics
    players_in_matchmaking = Column(Integer, default=0)
    active_players_in_game = Column(Integer, default=0)
    active_games = Column(Integer, default=0)
    concurrent_websockets = Column(Integer, default=0)
    
    # Engagement Metrics
    new_users_today = Column(Integer, default=0) # <-- ADDED
    dau = Column(Integer, default=0) # Daily Active Users <-- ADDED
    mau = Column(Integer, default=0) # Monthly Active Users <-- ADDED
    
    # Game Health Metrics
    total_games_finished = Column(Integer, default=0)
    total_games_abandoned = Column(Integer, default=0)
    avg_game_duration_seconds = Column(Float, default=0)
    p1_win_rate = Column(Float, default=0.5) # <-- ADDED
    
    # System Performance Metrics
    api_error_rate_5xx_percent = Column(Float, default=0) # <-- ADDED
    gemini_avg_latency_ms = Column(Float, default=0) # <-- ADDED
    gemini_cache_hit_rate_percent = Column(Float, default=0) # <-- ADDED

class SystemAlert(Base):
    """Stores critical errors or warnings for admin review."""
    __tablename__ = "systemalerts"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    level = Column(String, default="ERROR") # e.g., 'ERROR', 'WARNING'
    message = Column(String, nullable=False)
    details = Column(Text, nullable=True) # For stack traces or extra info

class DailyActiveUser(Base):
    """Tracks unique user activity per day."""
    __tablename__ = "dailyactiveusers"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    activity_date = Column(Date, nullable=False, index=True)
    
    __table_args__ = (UniqueConstraint('user_id', 'activity_date', name='_user_activity_date_uc'),)