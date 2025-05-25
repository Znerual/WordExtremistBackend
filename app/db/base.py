# app/db/base.py
# Import all the models, so that Base has them before being
# imported by Alembic
from app.db.base_class import Base
from app.schemas.user import User
from app.schemas.game_content import SentencePrompt
from app.schemas.game_log import Game, GamePlayer, WordSubmission
# Import other models here