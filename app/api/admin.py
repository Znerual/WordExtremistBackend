# app/api/admin.py
import logging
from fastapi import APIRouter, Query, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_
from typing import List, Optional
import datetime
import math

from app.api import deps
from app.core import security
from app.models.game_log_display import GamePublic, WordSubmissionPublic
from app.models.user import UserPublic
from app.schemas.game_content import SentencePrompt as SentencePromptModel # SQLAlchemy model
from app.schemas.game_log import Game, WordSubmission, GamePlayer # SQLAlchemy model
from app.models.game import SentencePromptPublic # Pydantic model for display
from app.crud import crud_game_content, crud_game_log, crud_user, crud_sentence_prompt 
from app.schemas.user import User # Your existing CRUD functions
from app.core.config import settings
from fastapi import HTTPException # Added HTTPException

logger = logging.getLogger(__name__)

router = APIRouter()
protected_router = APIRouter(
    dependencies=[Depends(deps.get_current_admin_user)],
    tags=["Admin"]
)

# Configure templates
# Assuming your 'templates' directory is at 'app/templates'
# Adjust the path if your project structure is different relative to where uvicorn runs
templates = Jinja2Templates(directory="app/templates")

ITEMS_PER_PAGE = 15
ADMIN_USERS_PER_PAGE = 20

# --- UNPROTECTED AUTH ROUTES ---

@router.get("/login", response_class=HTMLResponse, tags=["Admin Auth"])
async def admin_login_page(request: Request):
    """Serves the admin login page."""
    return templates.TemplateResponse("admin_login.html", {
        "request": request,
        "message": request.query_params.get("message")
    })

@router.post("/login", response_class=RedirectResponse, include_in_schema=False)
async def handle_admin_login(
    request: Request,
    db: Session = Depends(deps.get_db),
    username: str = Form(...),
    password: str = Form(...)
):
    """Handles admin login form submission, sets cookie, and redirects."""
    user = crud_user.get_user_by_email(db, email=username)
    if not user or not user.is_superuser or not user.hashed_password or not security.verify_password(password, user.hashed_password):
        # IMPORTANT: Use a generic error message to prevent leaking info
        # about whether an email exists or not.
        error_msg = "Incorrect email or password."
        return RedirectResponse(url=f"/admin/login?message={error_msg}", status_code=303)

    # Create token and set it in a secure, HTTP-only cookie
    access_token_expires = datetime.timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = security.create_access_token(
        data={"sub": str(user.id)}, expires_delta=access_token_expires
    )
    
    redirect_url = request.query_params.get("next", "/admin/")
    response = RedirectResponse(url=redirect_url, status_code=303)
    response.set_cookie(
        key=deps.ACCESS_TOKEN_COOKIE_NAME,
        value=access_token,
        httponly=True,
        samesite="lax",
        # secure=True, # In production with HTTPS, set this to True
    )
    return response

@router.get("/logout", response_class=RedirectResponse, tags=["Admin Auth"])
async def handle_admin_logout():
    """Logs the admin out by clearing the auth cookie."""
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(key=deps.ACCESS_TOKEN_COOKIE_NAME)
    return response


# --- PROTECTED ADMIN ROUTES ---


@protected_router.get("/", response_class=HTMLResponse, tags=["Admin"])
async def admin_dashboard(request: Request, current_user: UserPublic = Depends(deps.get_current_admin_user)):
    """
    Serves the main admin dashboard page with links to various admin sections.
    """
    return templates.TemplateResponse("admin_index.html", {"request": request, "user": current_user})

@protected_router.get("/users", response_class=HTMLResponse, tags=["Admin User Management"])
async def list_users_admin(
    request: Request,
    db: Session = Depends(deps.get_db),
    page: int = Query(1, ge=1),
    current_user: UserPublic = Depends(deps.get_current_admin_user)
):
    offset = (page - 1) * ADMIN_USERS_PER_PAGE
    total_users_count = db.query(User).count() # User is your SQLAlchemy model
    db_users = db.query(User).order_by(User.id.desc()).offset(offset).limit(ADMIN_USERS_PER_PAGE).all()
    
    users_public = [UserPublic.model_validate(u) for u in db_users]

    return templates.TemplateResponse("admin_users_list.html", {
        "request": request,
        "users": users_public,
        "total_users": total_users_count,
        "page": page,
        "total_pages": math.ceil(total_users_count / ADMIN_USERS_PER_PAGE),
        "message": request.query_params.get("message"),
        "success": request.query_params.get("success") == "true",
        "user": current_user
    })

@protected_router.get("/user/add", response_class=HTMLResponse, tags=["Admin User Management"])
async def show_add_user_form_admin(request: Request, current_user: UserPublic = Depends(deps.get_current_admin_user)):
    return templates.TemplateResponse("admin_user_form.html", {"request": request, "user_form": None, "user": current_user})

@protected_router.post("/user/add", response_class=RedirectResponse, tags=["Admin User Management"])
async def handle_add_user_admin(
    db: Session = Depends(deps.get_db),
    username: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    client_provided_id: Optional[str] = Form(None),
    play_games_player_id: Optional[str] = Form(None),
    google_id: Optional[str] = Form(None),
    profile_pic_url: Optional[str] = Form(None),
    is_active: bool = Form(True), # Default to True
    level: Optional[int] = Form(1),
    experience: Optional[int] = Form(0),
    country: Optional[str] = Form(None),
    mother_tongue: Optional[str] = Form(None),
    preferred_language: Optional[str] = Form(None),
    birthday: Optional[datetime.date] = Form(None),
    gender: Optional[str] = Form(None),
    language_level: Optional[str] = Form(None)
    # password: Optional[str] = Form(None) # If you were to implement password auth
):
    # Basic validation: at least one identifier should be present for a new user usually
    if not any([username, email, client_provided_id, play_games_player_id, google_id]):
         # Redirect back to form with error
        return RedirectResponse(url="/admin/user/add?message=Error: At least one identifying field (username, email, or an ID) is required.&success=false", status_code=303)

    # Check for uniqueness if necessary (e.g., email, client_provided_id, pgs_id, google_id)
    # This logic can be complex depending on your rules. Example for email:
    if email and crud_user.get_user_by_email(db, email=email):
        return RedirectResponse(url=f"/admin/user/add?message=Error: Email '{email}' already exists.&success=false", status_code=303)
    # Add similar checks for other unique fields (client_provided_id, pgs_id, google_id) if they are provided

    try:
        # Use a new generic CRUD function to create user
        # This is a simplified UserCreate model, create a proper Pydantic one if needed
        user_data = {
            "username": username, "email": email,
            "client_provided_id": client_provided_id,
            "play_games_player_id": play_games_player_id,
            "google_id": google_id,
            "profile_pic_url": profile_pic_url,
            "is_active": is_active,
            "level": level,
            "experience": experience,
            "country": country,
            "mother_tongue": mother_tongue,
            "preferred_language": preferred_language,
            "birthday": birthday,
            "gender": gender,
            "language_level": language_level,
        }
        # Remove None values so SQLAlchemy defaults can apply if defined in model
        user_data_cleaned = {k: v for k, v in user_data.items() if v is not None}

        created_user = crud_user.create_user_admin(db, user_data=user_data_cleaned) # New CRUD function
        message = f"User '{created_user.username or created_user.id}' created successfully."
        return RedirectResponse(url=f"/admin/users?message={message}&success=true", status_code=303)
    except Exception as e:
        message = f"Error creating user: {e}"
        return RedirectResponse(url=f"/admin/user/add?message={message}&success=false", status_code=303)


@protected_router.get("/user/{user_id}/edit", response_class=HTMLResponse, tags=["Admin User Management"])
async def show_edit_user_form_admin(request: Request, user_id: int, db: Session = Depends(deps.get_db), current_user: UserPublic = Depends(deps.get_current_admin_user)):
    db_user = crud_user.get_user(db, user_id=user_id)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    user_public = UserPublic.model_validate(db_user)
    return templates.TemplateResponse("admin_user_form.html", {
        "request": request, 
        "user": user_public, 
        "user_id": user_id,
        "message": request.query_params.get("message"),
        "success": request.query_params.get("success") == "true",
        "user": current_user,
    })

@protected_router.post("/user/{user_id}/edit", response_class=RedirectResponse, tags=["Admin User Management"])
async def handle_edit_user_admin(
    user_id: int,
    db: Session = Depends(deps.get_db),
    username: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    client_provided_id: Optional[str] = Form(None),
    play_games_player_id: Optional[str] = Form(None),
    google_id: Optional[str] = Form(None),
    profile_pic_url: Optional[str] = Form(None),
    is_active_form: Optional[str] = Form(None), # Checkboxes send value "true" or "on" if checked, or not at all
    level: Optional[int] = Form(None), # Allow None to keep existing if not submitted
    experience: Optional[int] = Form(None), # Allow None to keep existing if not
    country: Optional[str] = Form(None),
    mother_tongue: Optional[str] = Form(None),
    preferred_language: Optional[str] = Form(None),
    birthday: Optional[datetime.date] = Form(None),
    gender: Optional[str] = Form(None),
    language_level: Optional[str] = Form(None)
):
    db_user = crud_user.get_user(db, user_id=user_id)
    if not db_user:
        # Should not happen if coming from valid link, but good check
        return RedirectResponse(url="/admin/users?message=Error: User not found for editing.&success=false", status_code=303)

    # Handle checkbox for is_active
    is_active = True if is_active_form else False

    update_data = {
        "username": username, "email": email,
        "client_provided_id": client_provided_id,
        "play_games_player_id": play_games_player_id,
        "google_id": google_id,
        "profile_pic_url": profile_pic_url,
        "is_active": is_active,
        "country": country,
        "mother_tongue": mother_tongue,
        "preferred_language": preferred_language,
        "birthday": birthday,
        "gender": gender,
        "language_level": language_level,
    }

    if level is not None:
        update_data["level"] = level
    if experience is not None:
        update_data["experience"] = experience
        
    # Filter out fields that were not submitted or are empty strings to avoid overwriting with None unintentionally
    # unless you specifically want to allow setting fields to NULL via empty form submissions.
    # For this example, we'll update with provided values, allowing empty strings to clear fields if model allows nullable.
    
    try:
        updated_user = crud_user.update_user_admin(db, user_id=user_id, user_update_data=update_data) # New CRUD
        message = f"User '{updated_user.username or updated_user.id}' updated successfully."
        return RedirectResponse(url=f"/admin/user/{user_id}/edit?message={message}&success=true", status_code=303)
    except Exception as e:
        # Catch specific exceptions like IntegrityError for duplicate unique fields
        message = f"Error updating user: {e}"
        return RedirectResponse(url=f"/admin/user/{user_id}/edit?message={message}&success=false", status_code=303)


@protected_router.post("/user/{user_id}/delete", response_class=RedirectResponse, tags=["Admin User Management"])
async def handle_delete_user_admin(user_id: int, db: Session = Depends(deps.get_db)):
    db_user = crud_user.get_user(db, user_id=user_id)
    if not db_user:
        message = "Error: User not found for deletion."
        return RedirectResponse(url=f"/admin/users?message={message}&success=false", status_code=303)
    try:
        username_deleted = db_user.username or f"ID {db_user.id}"
        crud_user.delete_user_admin(db, user_id=user_id) # New CRUD function
        message = f"User '{username_deleted}' deleted successfully."
        return RedirectResponse(url=f"/admin/users?message={message}&success=true", status_code=303)
    except Exception as e:
        # Handle cases where deletion might fail due to foreign key constraints if user is linked elsewhere
        message = f"Error deleting user: {e}. Check for related records (games, submissions)."
        return RedirectResponse(url=f"/admin/users?message={message}&success=false", status_code=303)

# This route will now be for the full management page
@protected_router.get("/sentence-prompts", response_class=HTMLResponse)
async def manage_sentence_prompts(
    request: Request,
    db: Session = Depends(deps.get_db),
    page: int = Query(1, ge=1),
    search: Optional[str] = Query(None),
    message: Optional[str] = None,
    success: Optional[bool] = None,
    current_user: UserPublic = Depends(deps.get_current_admin_user)
):
    """
    Displays a paginated and searchable list of all sentence prompts,
    and a form to add a new one.
    """
    offset = (page - 1) * ITEMS_PER_PAGE
    
    query = db.query(SentencePromptModel)
    
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                SentencePromptModel.sentence_text.ilike(search_term),
                SentencePromptModel.target_word.ilike(search_term),
                SentencePromptModel.prompt_text.ilike(search_term)
            )
        )

    total_prompts = query.count()
    db_prompts = query.order_by(SentencePromptModel.id.desc()).offset(offset).limit(ITEMS_PER_PAGE).all()
    
    prompts_public = [SentencePromptPublic.model_validate(p) for p in db_prompts]

    return templates.TemplateResponse("admin_sentence_prompts.html", {
        "request": request,
        "prompts": prompts_public,
        "total_prompts": total_prompts,
        "page": page,
        "total_pages": math.ceil(total_prompts / ITEMS_PER_PAGE),
        "search": search or "",
        "message": request.query_params.get("message"),
        "success": request.query_params.get("success") == "true",
        "user": current_user
    })


# This route handles the form submission for ADDING a prompt
@protected_router.post("/sentence-prompts/add", tags=["Admin"])
async def handle_add_sentence_prompt(
    db: Session = Depends(deps.get_db),
    sentence_text: str = Form(...),
    target_word: str = Form(...),
    prompt_text: str = Form(...),
    difficulty: Optional[int] = Form(1),
    language: Optional[str] = Form("en")
):
    """Handles the form submission for adding a new sentence prompt."""
    try:
        # ... (validation logic is the same)
        # On success or failure, redirect back to the main management page
        created_prompt = crud_sentence_prompt.create_sentence_prompt(
            db=db,
            sentence_text=sentence_text,
            target_word=target_word,
            prompt_text=prompt_text,
            difficulty=difficulty,
            language=language
        )
        success_msg = f"Successfully added prompt ID {created_prompt.id}."
        return RedirectResponse(
            url=f"/admin/sentence-prompts?message={success_msg}&success=true",
            status_code=303
        )
    except Exception as e:
        error_msg = f"Error adding prompt: {e}"
        return RedirectResponse(
            url=f"/admin/sentence-prompts?message={error_msg}&success=false",
            status_code=303
        )

# --- NEW ROUTES FOR EDITING ---

@protected_router.get("/sentence-prompts/{prompt_id}/edit", response_class=HTMLResponse)
async def show_edit_sentence_prompt_form(
    prompt_id: int,
    request: Request,
    db: Session = Depends(deps.get_db),
    current_user: UserPublic = Depends(deps.get_current_admin_user)
):
    """Displays the form to edit an existing sentence prompt."""
    prompt = crud_sentence_prompt.get_sentence_prompt(db, prompt_id=prompt_id)
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")
    
    return templates.TemplateResponse("admin_edit_prompt.html", {
        "request": request,
        "prompt": SentencePromptPublic.model_validate(prompt),
        "user": current_user
    })

@protected_router.post("/sentence-prompts/{prompt_id}/edit", tags=["Admin"])
async def handle_edit_sentence_prompt(
    prompt_id: int,
    db: Session = Depends(deps.get_db),
    sentence_text: str = Form(...),
    target_word: str = Form(...),
    prompt_text: str = Form(...),
    difficulty: int = Form(...),
    language: str = Form(...)
):
    """Handles the form submission for editing a sentence prompt."""
    update_data = {
        "sentence_text": sentence_text,
        "target_word": target_word,
        "prompt_text": prompt_text,
        "difficulty": difficulty,
        "language": language,
    }
    try:
        updated_prompt = crud_sentence_prompt.update_sentence_prompt(db, prompt_id=prompt_id, update_data=update_data)
        if not updated_prompt:
             raise HTTPException(status_code=404, detail="Prompt not found")
        success_msg = f"Successfully updated prompt ID {prompt_id}."
        return RedirectResponse(url=f"/admin/sentence-prompts?message={success_msg}&success=true", status_code=303)
    except Exception as e:
        error_msg = f"Error updating prompt: {e}"
        return RedirectResponse(url=f"/admin/sentence-prompts?message={error_msg}&success=false", status_code=303)

# --- NEW ROUTE FOR DELETING ---
@protected_router.post("/sentence-prompts/{prompt_id}/delete")
async def handle_delete_sentence_prompt(prompt_id: int, db: Session = Depends(deps.get_db)):
    try:
        deleted = crud_sentence_prompt.delete_sentence_prompt(db, prompt_id=prompt_id)
        if not deleted:
             raise HTTPException(status_code=404, detail="Prompt not found")
        success_msg = f"Successfully deleted prompt ID {prompt_id}."
        return RedirectResponse(url=f"/admin/sentence-prompts?message={success_msg}&success=true", status_code=303)
    except Exception as e:
        # This will catch errors if a prompt is in use by a word_submission (foreign key constraint)
        error_msg = f"Error deleting prompt {prompt_id}: It may be in use in game logs. Details: {e}"
        return RedirectResponse(url=f"/admin/sentence-prompts?message={error_msg}&success=false", status_code=303)

@protected_router.get("/game-logs", response_class=HTMLResponse, tags=["Admin Game Logs"])
async def show_game_logs(
    request: Request,
    db: Session = Depends(deps.get_db),
    games_page: int = Query(1, ge=1),
    submissions_page: int = Query(1, ge=1),
    game_id_filter: Optional[int] = Query(None, description="Filter submissions by Game DB ID"), # For linking
    current_user: UserPublic = Depends(deps.get_current_admin_user),
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
        "user": current_user,
    })

@protected_router.get("/game/{game_db_id}/submissions", response_class=HTMLResponse, tags=["Admin Game Logs"])
async def show_submissions_for_game(request: Request, game_db_id: int, db: Session = Depends(deps.get_db)):
    """Redirects to game-logs page, filtering submissions for the given game."""
    # This just makes a cleaner URL that redirects to the main logs page with a filter
    return RedirectResponse(url=f"/admin/game-logs?game_id_filter={game_db_id}", status_code=302)


@protected_router.get("/game/{game_db_id}/edit", response_class=HTMLResponse, tags=["Admin Game Logs"])
async def show_edit_game_form(
    request: Request,
    game_db_id: int,
    db: Session = Depends(deps.get_db),
    current_user: UserPublic = Depends(deps.get_current_admin_user)
):
    game = db.query(Game).options(joinedload(Game.players_association)).filter(Game.id == game_db_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    return templates.TemplateResponse("admin_edit_game.html", {
        "request": request,
        "game": GamePublic.model_validate(game), # Pass Pydantic model
        "message": request.query_params.get("message"),
        "success": request.query_params.get("success") == "true",
        "user": current_user,
    })

@protected_router.post("/game/{game_db_id}/edit", tags=["Admin Game Logs"])
async def handle_edit_game(
    request: Request, # To get all form data dynamically for scores
    game_db_id: int,
    db: Session = Depends(deps.get_db),
    matchmaking_game_id: str = Form(...),
    status: str = Form(...),
    language: Optional[str] = Form(None), # Optional language field
    winner_user_id: Optional[int] = Form(None)
):
    form_data = await request.form()
    try:
        updated_game = crud_game_log.update_game_details(
            db=db,
            game_db_id=game_db_id,
            matchmaking_game_id=matchmaking_game_id,
            status=status,
            language=language, # Allow admin to edit language
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
                    logger.exception(f"Skipping invalid score update for key {key}")
                except Exception as e_score:
                    logger.exception(f"Error updating score for {key}: {e_score}")


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

@protected_router.get("/submission/{submission_id}/edit", response_class=HTMLResponse, tags=["Admin Game Logs"])
async def show_edit_submission_form(
    request: Request,
    submission_id: int,
    db: Session = Depends(deps.get_db),
    current_user: UserPublic = Depends(deps.get_current_admin_user)
):
    submission = crud_game_log.get_word_submission_by_id(db, submission_id=submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Word Submission not found")
    
    return templates.TemplateResponse("admin_edit_submission.html", {
        "request": request,
        "submission": WordSubmissionPublic.model_validate(submission), # Pass Pydantic model
        "message": request.query_params.get("message"),
        "success": request.query_params.get("success") == "true",
        "user": current_user
    })

@protected_router.post("/submission/{submission_id}/edit", tags=["Admin Game Logs"])
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
    
# Route to serve the new monitoring page
@protected_router.get("/monitoring", response_class=HTMLResponse)
async def admin_monitoring_page(request: Request, current_user: UserPublic = Depends(deps.get_current_admin_user)):
    """Serves the main admin monitoring page."""
    return templates.TemplateResponse("admin_monitoring.html", {"request": request, "user": current_user})

# Include the protected router under the main admin router.
# All its routes will inherit the /admin prefix.
router.include_router(protected_router)