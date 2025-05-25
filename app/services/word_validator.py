# app/services/word_validator.py
from sqlalchemy.orm import Session
from app.schemas.game_log import WordSubmission # Import your WordSubmission SQLAlchemy model

def validate_word_against_prompt(
    db: Session,                     # NEW: Database session
    word: str,                       # Word being submitted
    sentence_prompt_id: int,         # NEW: ID of the current sentence prompt
    # user_id: int,                  # NEW: Optional - ID of the user submitting (if needed for other logic)
    target_word: str,                # From the current prompt object
    prompt_text: str,                # From the current prompt object
    sentence_text: str               # From the current prompt object
) -> bool:
    """
    Validates a word.
    1. Checks if this exact word has been submitted before for this specific sentence_prompt_id.
       If yes, returns its previously determined validity.
    2. If not submitted before, performs the actual validation logic.
    """
    word_lower = word.strip().lower() # Normalize the word for checking

    # 1. Check for previous submission of this word for this prompt
    previous_submission = (
        db.query(WordSubmission)
        .filter(
            WordSubmission.sentence_prompt_id == sentence_prompt_id,
            WordSubmission.submitted_word.ilike(word_lower) # Case-insensitive check for the word
        )
        .order_by(WordSubmission.submission_timestamp.desc()) # Get the latest if multiple (shouldn't happen for same word/prompt if logic is tight)
        .first()
    )

    if previous_submission:
        print(f"VALIDATION: Word='{word}' (for prompt_id={sentence_prompt_id}) previously submitted. Prior validity: {previous_submission.is_valid}")
        return previous_submission.is_valid # Return the stored validity

    # 2. If not previously submitted, perform the actual validation logic
    # This is your original dummy logic, replace with real validation
    print(f"VALIDATION: Word='{word}' (for prompt_id={sentence_prompt_id}) not previously submitted. Performing new validation.")
    print(f"DUMMY VALIDATION: Target='{target_word}', Prompt='{prompt_text}'")

    # Replace with real logic later
    if word_lower == "invalid_test_word": # Use normalized word for test
        is_currently_valid = False
    else:
        # TODO: Implement your actual word validation logic here based on:
        # - word (the submitted word)
        # - target_word (the word in the sentence to be replaced)
        # - prompt_text (e.g., "BE MORE EXTREME", "RHYME WITH", etc.)
        # - sentence_text (the original sentence)
        # This could involve checking if 'word' is "more extreme" than 'target_word',
        # if it fits grammatically, if it matches the prompt's criteria, etc.
        is_currently_valid = True  # Default to true for tests until implemented

    print(f"DUMMY VALIDATION RESULT for '{word_lower}': {is_currently_valid}")
    return is_currently_valid