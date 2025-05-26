# app/schemas/game_content.py
from sqlalchemy import Column, String, Integer, Text
from app.db.base_class import Base
# Removed: from pydantic import BaseModel

class SentencePrompt(Base):
    id = Column(Integer, primary_key=True, index=True)
    sentence_text = Column(Text, nullable=False)
    target_word = Column(String, nullable=False) # The word to be replaced
    prompt_text = Column(String, nullable=False) # e.g., "BE MORE EXTREME"
    difficulty = Column(Integer, default=1) # Optional
    # category = Column(String) # Optional

# Removed SentencePromptCreate class