# app/api/game_data.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api import deps
from app.models.game import SentencePromptPublic
from app.crud import crud_game_content

router = APIRouter()

@router.get("/sentence-prompt/random", response_model=SentencePromptPublic)
def get_random_sentence_prompt_api(
    db: Session = Depends(deps.get_db),
    # current_user: UserPublic = Depends(deps.get_current_user_firebase) # Optional: protect this endpoint
):
    """Get a random sentence and prompt for a game round."""
    item = crud_game_content.get_random_sentence_prompt(db)
    if not item:
        raise HTTPException(status_code=404, detail="No sentence prompts found in database.")
    return SentencePromptPublic.model_validate(item)

# You might also add an endpoint to create sentence prompts (admin only)