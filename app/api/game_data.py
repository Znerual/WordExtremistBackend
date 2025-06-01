# app/api/game_data.py
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.api import deps
from app.models.game import SentencePromptPublic
from app.crud import crud_game_content

logger = logging.getLogger("app.api.game_data")  # Logger for this module
router = APIRouter()

@router.get("/sentence-prompt/random", response_model=SentencePromptPublic)
def get_random_sentence_prompt_api(
    db: Session = Depends(deps.get_db),
    language: str | None = Query("en", description="BCP-47 language code for the desired prompt, e.g., 'en', 'es'."),  # Optional: filter by language
    # current_user: UserPublic = Depends(deps.get_current_user_firebase) # Optional: protect this endpoint
):
    """Get a random sentence and prompt for a game round."""
    item = crud_game_content.get_random_sentence_prompt(db, language=language)
    if not item:
        raise HTTPException(status_code=404, detail="No sentence prompts found in database.")
    return SentencePromptPublic.model_validate(item)

@router.post("/sentence-prompts/", response_model=SentencePromptPublic, status_code=201) # Added status_code
async def create_sentence_prompt_via_api( # Renamed to avoid conflict if admin.py also has one
    sentence_prompt_data: SentencePromptPublic, # Input model includes language
    db: Session = Depends(deps.get_db)
    # current_user: UserPublic = Depends(deps.get_current_active_user) # If auth is needed
) -> SentencePromptPublic: # Return SQLAlchemy model, FastAPI handles conversion
    """
    Creates a new sentence prompt via API.
    The input `sentence_prompt_data` should include `language`.
    """
    if sentence_prompt_data.target_word.lower() not in sentence_prompt_data.sentence_text.lower():
        logger.error(
            f"Target word '{sentence_prompt_data.target_word}' not found in sentence '{sentence_prompt_data.sentence_text}'."
        )
        raise HTTPException(
            status_code=400,
            detail=f"Target word '{sentence_prompt_data.target_word}' not found in sentence '{sentence_prompt_data.sentence_text}'."
        )
    
    existing = crud_game_content.get_sentence_prompt_by_content(
        db,
        sentence_text=sentence_prompt_data.sentence_text,
        target_word=sentence_prompt_data.target_word,
        prompt_text=sentence_prompt_data.prompt_text,
        language=sentence_prompt_data.language
    )
    if existing:
        logger.warning(
            f"Attempt to create duplicate sentence prompt for language '{sentence_prompt_data.language}': {sentence_prompt_data.sentence_text}"
        )
        raise HTTPException(
            status_code=409, # Conflict
            detail="This sentence prompt already exists for the given language."
        )

    # The ID in sentence_prompt_data will be ignored by create_sentence_prompt if using **data.model_dump(exclude_unset=True)
    # or if your Pydantic model for create doesn't have ID.
    # Since SentencePromptPublic has ID, it's better if CRUD function can take the Pydantic model directly
    # or if you use a dedicated Create Pydantic model.
    # For now, relying on the CRUD function's parameters:
    created_prompt_db = crud_game_content.create_sentence_prompt(
        db=db,
        sentence_text=sentence_prompt_data.sentence_text,
        target_word=sentence_prompt_data.target_word,
        prompt_text=sentence_prompt_data.prompt_text,
        difficulty=sentence_prompt_data.difficulty,
        language=sentence_prompt_data.language
    )
    return created_prompt_db