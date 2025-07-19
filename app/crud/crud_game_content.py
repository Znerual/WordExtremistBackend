# app/crud/crud_game_content.py
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import func # For random
from app.schemas.game_content import SentencePrompt

def get_random_sentence_prompt(db: Session, language: str | None = "en") -> SentencePrompt | None:
    query = db.query(SentencePrompt)
    if language:
        query = query.filter(SentencePrompt.language == language)
    return query.order_by(func.random()).first()

def create_sentence_prompt(db: Session, sentence_text: str, target_word: str, prompt_text: str, difficulty: int = 1, language: str = "en") -> SentencePrompt:
    db_item = SentencePrompt(
        sentence_text=sentence_text,
        target_word=target_word,
        prompt_text=prompt_text,
        difficulty=difficulty,
        language=language
    )
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return db_item

def get_sentence_prompt_by_content(db: Session, sentence_text: str, target_word: str, prompt_text: str, language: str = "en") -> SentencePrompt | None:
    """
    Retrieves a sentence prompt from the database based on its exact content.
    """
    return db.query(SentencePrompt).filter(
        SentencePrompt.sentence_text == sentence_text,
        SentencePrompt.target_word == target_word,
        SentencePrompt.prompt_text == prompt_text,
        SentencePrompt.language == language
    ).first()