import logging
from typing import List, Optional
from sqlalchemy.orm import Session, joinedload
from app.schemas.game_log import Game, GamePlayer, WordSubmission
from app.schemas.user import User # To type hint
import datetime
from app.schemas.game_content import SentencePrompt

logger = logging.getLogger("app.crud.game_log")  # Logger for this module

def create_game_record(db: Session, matchmaking_game_id: str, player1_id: int, player2_id: int, language: str = "en") -> Game:
    """
    Creates a new game record and associated player records.
    Returns the persisted Game object.
    """
    db_game = Game(matchmaking_game_id=matchmaking_game_id, language=language, status="in_progress")
    db.add(db_game)
    # We need to flush to get db_game.id if it's auto-incremented by DB before creating GamePlayer
    db.flush() 

    db_player1 = GamePlayer(game_id=db_game.id, user_id=player1_id, score=0, player_order=1)
    db_player2 = GamePlayer(game_id=db_game.id, user_id=player2_id, score=0, player_order=2)
    
    db.add_all([db_player1, db_player2])
    db.commit()
    db.refresh(db_game) # Refresh to get relationships populated if accessed immediately
    return db_game

def log_word_submission(
    db: Session, 
    game_db_id: int, # The integer ID from our 'games' table
    round_number: int, 
    user_id: int, 
    sentence_prompt_id: int, 
    submitted_word: str, 
    time_taken_ms: int | None, 
    is_valid: bool,
    creativity_score: Optional[int] = None,
    validation_latency_ms: Optional[int] = None
) -> WordSubmission:
    db_submission = WordSubmission(
        game_id=game_db_id,
        round_number=round_number,
        user_id=user_id,
        sentence_prompt_id=sentence_prompt_id,
        submitted_word=submitted_word,
        time_taken_ms=time_taken_ms,
        is_valid=is_valid,
        submission_timestamp=datetime.datetime.now(datetime.timezone.utc),
        creativity_score=creativity_score,
        validation_latency_ms=validation_latency_ms 
    )
    db.add(db_submission)
    db.commit()
    db.refresh(db_submission)
    return db_submission

def get_all_word_vault_entries_for_user(db: Session, user_id: int) -> List[tuple]:
    """
    Retrieves all valid word submissions for a user, along with their context (sentence and prompt).
    The data is sorted by creativity score (best first), then by date.
    Returns a list of tuples: (submitted_word, creativity_score, sentence_text, prompt_text)
    """
    return (
        db.query(
            WordSubmission.submitted_word,
            WordSubmission.creativity_score,
            SentencePrompt.sentence_text,
            SentencePrompt.prompt_text
        )
        .join(SentencePrompt, WordSubmission.sentence_prompt_id == SentencePrompt.id)
        .filter(WordSubmission.user_id == user_id)
        .filter(WordSubmission.is_valid == True)  # Only show valid words in the vault
        .order_by(WordSubmission.creativity_score.desc().nullslast(), WordSubmission.submission_timestamp.desc())
        .all()
    )

def update_game_player_score(db: Session, game_db_id: int, user_id: int, new_score: int):
    db_game_player = db.query(GamePlayer).filter(
        GamePlayer.game_id == game_db_id,
        GamePlayer.user_id == user_id
    ).first()
    if db_game_player:
        db_game_player.score = new_score
        db.commit()
        db.refresh(db_game_player)
    else:
        logging.error(f"Warning: GamePlayer record not found for game_db_id {game_db_id}, user_id {user_id} to update score.")

def finalize_game_record(db: Session, game_db_id: int, winner_user_id: int | None, status: str = "finished", reason: str | None = None):
    db_game = db.query(Game).filter(Game.id == game_db_id).first()
    if db_game:
        db_game.end_time = datetime.datetime.now(datetime.timezone.utc)
        db_game.status = status
        db_game.end_reason = reason
        if winner_user_id:
            db_game.winner_user_id = winner_user_id
        db.commit()
        db.refresh(db_game)
    else:
        logging.error(f"Warning: Game record not found for game_db_id {game_db_id} to finalize.")

def increment_emojis_sent(db: Session, game_db_id: int, user_id: int):
    """Increments the emoji count for a player in a specific game."""
    player_record = db.query(GamePlayer).filter(
        GamePlayer.game_id == game_db_id,
        GamePlayer.user_id == user_id
    ).first()
    if player_record:
        player_record.emojis_sent = (player_record.emojis_sent or 0) + 1
        db.commit()
    else:
        logger.warning(f"Could not find GamePlayer for G:{game_db_id}, U:{user_id} to increment emoji count.")

def get_game_by_id(db: Session, game_db_id: int) -> Game | None:
    return db.query(Game).filter(Game.id == game_db_id).first()

def get_word_submission_by_id(db: Session, submission_id: int) -> WordSubmission | None:
    return db.query(WordSubmission).filter(WordSubmission.id == submission_id).first()


def update_game_details(
    db: Session, 
    game_db_id: int, 
    matchmaking_game_id: str, 
    status: str, 
    winner_user_id: Optional[int],
    language: Optional[str] = None # Allow admin to edit language (less common for active games
) -> Game | None:
    db_game = get_game_by_id(db, game_db_id)
    if db_game:
        db_game.matchmaking_game_id = matchmaking_game_id
        db_game.status = status
        db_game.winner_user_id = winner_user_id
        if language: # Only update if provided
            db_game.language = language
        # Note: start_time and end_time might need specific handling if editable
        # For simplicity, not making them directly editable via simple text fields here
        # as they are datetime objects.
        db.commit()
        db.refresh(db_game)
        return db_game
    return None

def update_game_player_score_admin(db: Session, game_db_id: int, user_id: int, new_score: int):
    """Specific for admin updates to GamePlayer score."""
    db_game_player = db.query(GamePlayer).filter(
        GamePlayer.game_id == game_db_id,
        GamePlayer.user_id == user_id
    ).first()
    if db_game_player:
        db_game_player.score = new_score
        db.commit()
        db.refresh(db_game_player)
    else:
        logging.error(f"Admin: GamePlayer record not found for game_db_id {game_db_id}, user_id {user_id} to update score.")


def update_word_submission_details(
    db: Session,
    submission_id: int,
    submitted_word: str,
    is_valid: bool,
    # Add other fields if they should be editable
) -> WordSubmission | None:
    db_submission = get_word_submission_by_id(db, submission_id)
    if db_submission:
        db_submission.submitted_word = submitted_word
        db_submission.is_valid = is_valid
        db.commit()
        db.refresh(db_submission)
        return db_submission
    return None

def update_word_submission_details(
    db: Session,
    submission_id: int,
    submitted_word: str,
    time_taken_ms: Optional[int], # Allow None
    is_valid: bool,
    # Add other fields if they should be editable
) -> WordSubmission | None:
    db_submission = get_word_submission_by_id(db, submission_id)
    if db_submission:
        db_submission.submitted_word = submitted_word
        db_submission.time_taken_ms = time_taken_ms # Assign None if that's the value
        db_submission.is_valid = is_valid
        db.commit()
        db.refresh(db_submission)
        return db_submission
    return None