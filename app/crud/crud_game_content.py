# app/crud/crud_game_content.py
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import func # For random
from app.schemas.game_content import SentencePrompt

def get_random_sentence_prompt(db: Session) -> SentencePrompt | None:
    return db.query(SentencePrompt).order_by(func.random()).first()

def create_sentence_prompt(db: Session, sentence_text: str, target_word: str, prompt_text: str):
    db_item = SentencePrompt(
        sentence_text=sentence_text,
        target_word=target_word,
        prompt_text=prompt_text
    )
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return db_item