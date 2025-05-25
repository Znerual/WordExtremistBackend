# app/api/admin.py
from fastapi import APIRouter, Query, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional
from datetime import datetime
import math

from app.api import deps
from app.models.game_log_display import GamePublic, WordSubmissionPublic
from app.schemas.game_content import SentencePrompt as SentencePromptSchema # SQLAlchemy model
from app.schemas.game_log import Game, WordSubmission, GamePlayer # SQLAlchemy model
from app.models.game import SentencePromptPublic # Pydantic model for display
from app.crud import crud_game_content, crud_game_log # Your existing CRUD functions

router = APIRouter()

# Configure templates
# Assuming your 'templates' directory is at 'app/templates'
# Adjust the path if your project structure is different relative to where uvicorn runs
templates = Jinja2Templates(directory="app/templates")

ITEMS_PER_PAGE = 15

@router.get("/", response_class=HTMLResponse, tags=["Admin"])
async def admin_dashboard(request: Request):
    """
    Serves the main admin dashboard page with links to various admin sections.
    """
    return templates.TemplateResponse("admin_index.html", {"request": request})

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
    
@router.get("/game-logs", response_class=HTMLResponse, tags=["Admin Game Logs"])
async def show_game_logs(
    request: Request,
    db: Session = Depends(deps.get_db),
    games_page: int = Query(1, ge=1),
    submissions_page: int = Query(1, ge=1),
    game_id_filter: Optional[int] = Query(None, description="Filter submissions by Game DB ID") # For linking
):
    # Games Pagination
    games_offset = (games_page - 1) * ITEMS_PER_PAGE
    total_games_count = db.query(Game).count()
    db_games = (
        db.query(Game)
        .options(joinedload(Game.players_association), joinedload(Game.word_submissions)) # Eager load
        .order_by(Game.id.desc())
        .offset(games_offset)
        .limit(ITEMS_PER_PAGE)
        .all()
    )
    games_public = [GamePublic.model_validate(g) for g in db_games] # Use Pydantic model for template
    
    # Word Submissions Pagination
    submissions_offset = (submissions_page - 1) * ITEMS_PER_PAGE
    submissions_query = db.query(WordSubmission)
    if game_id_filter:
        submissions_query = submissions_query.filter(WordSubmission.game_id == game_id_filter)
    
    total_submissions_count = submissions_query.count()
    db_submissions = (
        submissions_query
        .order_by(WordSubmission.id.desc())
        .offset(submissions_offset)
        .limit(ITEMS_PER_PAGE)
        .all()
    )
    submissions_public = [WordSubmissionPublic.model_validate(s) for s in db_submissions]

    return templates.TemplateResponse("admin_game_logs.html", {
        "request": request,
        "games": games_public,
        "games_total": total_games_count,
        "games_page": games_page,
        "games_total_pages": math.ceil(total_games_count / ITEMS_PER_PAGE),
        "submissions": submissions_public,
        "submissions_total": total_submissions_count,
        "submissions_page": submissions_page,
        "submissions_total_pages": math.ceil(total_submissions_count / ITEMS_PER_PAGE),
        "selected_game_id_for_submissions": game_id_filter,
        "message": request.query_params.get("message"),
        "success": request.query_params.get("success") == "true",
    })

@router.get("/game/{game_db_id}/submissions", response_class=HTMLResponse, tags=["Admin Game Logs"])
async def show_submissions_for_game(request: Request, game_db_id: int, db: Session = Depends(deps.get_db)):
    """Redirects to game-logs page, filtering submissions for the given game."""
    # This just makes a cleaner URL that redirects to the main logs page with a filter
    return RedirectResponse(url=f"/admin/game-logs?game_id_filter={game_db_id}", status_code=302)


@router.get("/game/{game_db_id}/edit", response_class=HTMLResponse, tags=["Admin Game Logs"])
async def show_edit_game_form(
    request: Request,
    game_db_id: int,
    db: Session = Depends(deps.get_db)
):
    game = db.query(Game).options(joinedload(Game.players_association)).filter(Game.id == game_db_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    return templates.TemplateResponse("admin_edit_game.html", {
        "request": request,
        "game": GamePublic.model_validate(game), # Pass Pydantic model
        "message": request.query_params.get("message"),
        "success": request.query_params.get("success") == "true",
    })

@router.post("/game/{game_db_id}/edit", tags=["Admin Game Logs"])
async def handle_edit_game(
    request: Request, # To get all form data dynamically for scores
    game_db_id: int,
    db: Session = Depends(deps.get_db),
    matchmaking_game_id: str = Form(...),
    status: str = Form(...),
    winner_user_id: Optional[int] = Form(None)
):
    form_data = await request.form()
    try:
        updated_game = crud_game_log.update_game_details(
            db=db,
            game_db_id=game_db_id,
            matchmaking_game_id=matchmaking_game_id,
            status=status,
            winner_user_id=winner_user_id if winner_user_id is not None else None # Handle empty string from form
        )
        if not updated_game:
            raise HTTPException(status_code=404, detail="Game not found for update")

        # Update player scores
        for key, value in form_data.items():
            if key.startswith("player_score_"):
                try:
                    player_id_to_update = int(key.split("player_score_")[1])
                    new_score = int(value)
                    crud_game_log.update_game_player_score_admin( # New CRUD function for this
                        db=db, game_db_id=game_db_id, user_id=player_id_to_update, new_score=new_score
                    )
                except ValueError:
                    print(f"Skipping invalid score update for key {key}")
                except Exception as e_score:
                    print(f"Error updating score for {key}: {e_score}")


        success_msg = f"Game {game_db_id} updated successfully."
        return RedirectResponse(
            url=f"/admin/game/{game_db_id}/edit?message={success_msg}&success=true",
            status_code=303
        )
    except Exception as e:
        error_msg = f"Error updating game: {e}"
        return RedirectResponse(
            url=f"/admin/game/{game_db_id}/edit?message={error_msg}&success=false",
            status_code=303
        )

@router.get("/submission/{submission_id}/edit", response_class=HTMLResponse, tags=["Admin Game Logs"])
async def show_edit_submission_form(
    request: Request,
    submission_id: int,
    db: Session = Depends(deps.get_db)
):
    submission = crud_game_log.get_word_submission_by_id(db, submission_id=submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Word Submission not found")
    
    return templates.TemplateResponse("admin_edit_submission.html", {
        "request": request,
        "submission": WordSubmissionPublic.model_validate(submission), # Pass Pydantic model
        "message": request.query_params.get("message"),
        "success": request.query_params.get("success") == "true",
    })

@router.post("/submission/{submission_id}/edit", tags=["Admin Game Logs"])
async def handle_edit_submission(
    submission_id: int,
    db: Session = Depends(deps.get_db),
    submitted_word: str = Form(...),
    time_taken_ms: Optional[int] = Form(None),
    is_valid: Optional[bool] = Form(False), # Checkbox handling
    is_valid_hidden_presence: Optional[str] = Form(None) # To detect if checkbox was part of the form
):
    # Handle checkbox: if 'is_valid' (name of checkbox) is not in form, it means it was unchecked (value is False)
    # If it is in form, its value will be "on" or the value attribute, which Form(bool) might handle.
    # A common robust way for checkboxes:
    actual_is_valid = True if is_valid else False # If 'is_valid' key is present and truthy, consider it True

    try:
        updated_submission = crud_game_log.update_word_submission_details(
            db=db,
            submission_id=submission_id,
            submitted_word=submitted_word,
            time_taken_ms=time_taken_ms, # Pass None if not provided
            is_valid=actual_is_valid
        )
        if not updated_submission:
            raise HTTPException(status_code=404, detail="Word Submission not found for update")

        success_msg = f"Word Submission {submission_id} updated successfully."
        # Redirect back to the edit form for this submission
        return RedirectResponse(
            url=f"/admin/submission/{submission_id}/edit?message={success_msg}&success=true",
            status_code=303
        )
    except Exception as e:
        error_msg = f"Error updating word submission: {e}"
        # Redirect back to the edit form for this submission
        return RedirectResponse(
            url=f"/admin/submission/{submission_id}/edit?message={error_msg}&success=false",
            status_code=303
        )