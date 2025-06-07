# app/db/base.py
# Import all the models, so that Base has them before being
# imported by Alembic
from app.db.base_class import Base
from app.schemas.user import User
from app.schemas.game_content import SentencePrompt
from app.schemas.game_log import Game, GamePlayer, WordSubmission
from app.schemas.system import MonitoringSnapshot, SystemAlert
from app.schemas.system import MonitoringSnapshot, SystemAlert, DailyActiveUser
# Import other models here