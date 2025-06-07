# app/crud/crud_system.py
import logging
from sqlalchemy.orm import Session
from app.schemas.system import MonitoringSnapshot, SystemAlert
from typing import Dict, Any, List

logger = logging.getLogger("app.crud.system")

def create_monitoring_snapshot(db: Session, metrics: Dict[str, Any]) -> MonitoringSnapshot:
    """Creates a new monitoring snapshot record in the database."""
    snapshot = MonitoringSnapshot(**metrics)
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    logger.info(f"Created monitoring snapshot at {snapshot.timestamp}")
    return snapshot

def create_alert(db: Session, level: str, message: str, details: str = None) -> SystemAlert:
    """Creates a new system alert record."""
    try:
        alert = SystemAlert(level=level, message=message, details=details)
        db.add(alert)
        db.commit()
        db.refresh(alert)
        logger.info(f"Logged new system alert: [{level}] {message}")
        return alert
    except Exception as e:
        # If logging to DB fails, we must fall back to standard logging
        logger.critical(f"CRITICAL: FAILED TO LOG ALERT TO DATABASE: {e}")
        logger.critical(f"Original Alert: [{level}] {message} | Details: {details}")
        db.rollback()

def get_latest_snapshots(db: Session, limit: int = 100) -> List[MonitoringSnapshot]:
    """Retrieves the most recent monitoring snapshots."""
    return db.query(MonitoringSnapshot).order_by(MonitoringSnapshot.timestamp.desc()).limit(limit).all()

def get_latest_alerts(db: Session, limit: int = 50) -> List[SystemAlert]:
    """Retrieves the most recent system alerts."""
    return db.query(SystemAlert).order_by(SystemAlert.timestamp.desc()).limit(limit).all()