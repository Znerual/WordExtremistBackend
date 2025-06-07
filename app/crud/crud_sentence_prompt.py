# app/crud/crud_sentence_prompt.py
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import func
from app.schemas.game_content import SentencePrompt
from typing import Dict, Any

def get_sentence_prompt(db: Session, prompt_id: int) -> SentencePrompt | None:
    """Gets a single prompt by its ID."""
    return db.query(SentencePrompt).filter(SentencePrompt.id == prompt_id).first()

def get_random_sentence_prompt(db: Session, language: str | None = "en") -> SentencePrompt | None:
    """Gets a random prompt, optionally filtered by language."""
    query = db.query(SentencePrompt)
    if language:
        query = query.filter(SentencePrompt.language == language)
    return query.order_by(func.random()).first()

def create_sentence_prompt(db: Session, sentence_text: str, target_word: str, prompt_text: str, difficulty: int = 1, language: str = "en") -> SentencePrompt:
    """Creates a new sentence prompt."""
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
    
def update_sentence_prompt(db: Session, prompt_id: int, update_data: Dict[str, Any]) -> SentencePrompt | None:
    """Updates an existing sentence prompt."""
    db_prompt = get_sentence_prompt(db, prompt_id)
    if db_prompt:
        for key, value in update_data.items():
            setattr(db_prompt, key, value)
        db.commit()
        db.refresh(db_prompt)
        return db_prompt
    return None

def delete_sentence_prompt(db: Session, prompt_id: int) -> bool:
    """Deletes a sentence prompt."""
    db_prompt = get_sentence_prompt(db, prompt_id)
    if db_prompt:
        db.delete(db_prompt)
        db.commit()
        return True
    return False

# You can now remove create_sentence_prompt and get_sentence_prompt_by_content
# from app/crud/crud_game_content.py to avoid duplication.