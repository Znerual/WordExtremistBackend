# app/models/validation.py
from typing import Optional
from pydantic import BaseModel

class WordValidationResult(BaseModel):
    is_valid: bool
    creativity_score: Optional[int] = None
    from_cache: bool = False  # To indicate if the result was from a previous submission
    error_message: Optional[str] = None # To pass error messages if any
