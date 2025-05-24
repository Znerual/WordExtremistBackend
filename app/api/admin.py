# app/api/admin.py
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import List, Optional

from app.api import deps
from app.schemas.game_content import SentencePrompt as SentencePromptSchema # SQLAlchemy model
from app.models.game import SentencePromptPublic # Pydantic model for display
from app.crud import crud_game_content # Your existing CRUD functions

router = APIRouter()

# Configure templates
# Assuming your 'templates' directory is at 'app/templates'
# Adjust the path if your project structure is different relative to where uvicorn runs
templates = Jinja2Templates(directory="app/templates")

@router.get("/add-sentence-prompt", response_class=HTMLResponse, tags=["Admin"])
async def show_add_sentence_prompt_form(
    request: Request,
    db: Session = Depends(deps.get_db),
    message: Optional[str] = None,
    success: Optional[bool] = None
):
    """Displays the form to add a new sentence prompt and lists existing ones."""
    # Fetch some existing prompts to display
    # Modify crud_game_content or add a new function to get latest N prompts
    # For now, let's assume a function get_latest_sentence_prompts exists or adapt
    try:
        # Crude way to get last 5 for now, ideally add a proper CRUD function
        db_prompts = db.query(SentencePromptSchema).order_by(SentencePromptSchema.id.desc()).limit(5).all()
        prompts_public = [SentencePromptPublic.model_validate(p) for p in db_prompts]
    except Exception as e:
        print(f"Error fetching existing prompts: {e}")
        prompts_public = []
        # If this happens on form load, it's not critical for the form itself
        # but indicates a DB issue if it persists.

    return templates.TemplateResponse("admin_forms.html", {
        "request": request,
        "prompts": prompts_public,
        "message": message,
        "success": success
    })

@router.post("/add-sentence-prompt", tags=["Admin"])
async def handle_add_sentence_prompt(
    request: Request, # Keep request for potential future use
    db: Session = Depends(deps.get_db),
    sentence_text: str = Form(...),
    target_word: str = Form(...),
    prompt_text: str = Form(...),
    difficulty: Optional[int] = Form(1) # Default to 1 if not provided
):
    """Handles the form submission for adding a new sentence prompt."""
    try:
        # Basic validation (more can be added)
        if not sentence_text or not target_word or not prompt_text:
            raise ValueError("All fields (sentence, target word, prompt) are required.")
        if target_word.lower() not in sentence_text.lower():
            # Redirect back with an error message
            error_msg = f"Error: Target word '{target_word}' not found in sentence '{sentence_text}'."
            # Use query parameters for redirect message
            return RedirectResponse(
                url=f"/admin/add-sentence-prompt?message={error_msg}&success=false",
                status_code=303 # See Other
            )

        created_prompt = crud_game_content.create_sentence_prompt(
            db=db,
            sentence_text=sentence_text,
            target_word=target_word,
            prompt_text=prompt_text,
            # difficulty=difficulty # Ensure your create_sentence_prompt handles difficulty
        )
        # Update crud_game_content.create_sentence_prompt to accept difficulty
        # For now, let's assume it doesn't and handle it separately if needed, or ignore.
        # If your create_sentence_prompt was updated to take difficulty:
        # created_prompt.difficulty = difficulty
        # db.commit()
        # db.refresh(created_prompt)

        success_msg = f"Successfully added prompt: '{created_prompt.sentence_text[:30]}...'"
        # Redirect back to the form page with a success message
        return RedirectResponse(
            url=f"/admin/add-sentence-prompt?message={success_msg}&success=true",
            status_code=303 # See Other, common for POST-redirect-GET
        )

    except ValueError as ve: # Specific validation error
         error_msg_val = f"Validation Error: {ve}"
         return RedirectResponse(
            url=f"/admin/add-sentence-prompt?message={error_msg_val}&success=false",
            status_code=303
        )
    except Exception as e:
        print(f"Error adding sentence prompt: {e}")
        # In a real app, log this error properly
        error_msg_exc = f"An unexpected error occurred: {str(e)}"
        return RedirectResponse(
            url=f"/admin/add-sentence-prompt?message={error_msg_exc}&success=false",
            status_code=303
        )

# You might need to update your crud_game_content.py to handle difficulty
# if you want to store it.
# Example update for app/crud/crud_game_content.py:
# def create_sentence_prompt(db: Session, sentence_text: str, target_word: str, prompt_text: str, difficulty: int = 1):
#     db_item = SentencePrompt( # Make sure SentencePrompt SQLAlchemy model has a difficulty column
#         sentence_text=sentence_text,
#         target_word=target_word,
#         prompt_text=prompt_text,
#         difficulty=difficulty
#     )
#     db.add(db_item)
#     db.commit()
#     db.refresh(db_item)
#     return db_item