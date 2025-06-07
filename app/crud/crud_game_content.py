# app/crud/crud_game_content.py
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import func # For random
from app.schemas.game_content import SentencePrompt

def get_random_sentence_prompt(db: Session, language: str | None = "en") -> SentencePrompt | None:
    query = db.query(SentencePrompt)
    if language:
        query = query.filter(SentencePrompt.language == language)
    return query.order_by(func.random()).first()

