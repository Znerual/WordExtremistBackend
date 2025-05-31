# app/services/word_validator.py
from sqlalchemy.orm import Session
from app.schemas.game_log import WordSubmission # Import your WordSubmission SQLAlchemy model
from typing import Optional, Dict, Any # Added
# from pydantic import BaseModel # Removed
import google.generativeai as genai # Added
import json # Added
from app.core.config import settings # Added
from app.models.validation import WordValidationResult # Added
# from app.crud.crud_game_log import log_word_submission # Commented out as per instruction

# WordValidationResult class definition removed from here

# Add this schema definition
GEMINI_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "is_valid": {"type": "BOOLEAN", "description": "Whether the submitted word is valid according to the prompt and target word."},
        "creativity_score": {"type": "INTEGER", "description": "Creativity score from 1-5 if valid. If invalid, this may be 0 or a default value based on schema adherence."},
        "reason": {"type": "STRING", "description": "A brief explanation for the validation decision."}
    },
    "required": ["is_valid", "reason", "creativity_score"] # Making creativity_score required, Gemini should provide a default (e.g. 0) if word is invalid.
}

def validate_word_against_prompt(
    db: Session,
    word: str,
    sentence_prompt_id: int,
    target_word: str, # From the current prompt object
    prompt_text: str, # From the current prompt object
    sentence_text: str, # From the current prompt object,
    language: str = "en", # Default language, can be overridden
    # game_db_id: int, # Not adding these yet as per instruction
    # round_number: int,
    # user_id: int,
) -> WordValidationResult: # New return type
    """
    Validates a word.
    1. Checks if this exact word has been submitted before for this specific sentence_prompt_id.
       If yes, returns its previously determined validity and creativity score.
    2. If not submitted before, performs validation using Gemini.
    """
    word_lower = word.strip().lower() # Normalize the word for checking

    # 1. Check for previous submission of this word for this prompt
    previous_submission = (
        db.query(WordSubmission)
        .filter(
            WordSubmission.sentence_prompt_id == sentence_prompt_id,
            WordSubmission.submitted_word.ilike(word_lower) # Case-insensitive check for the word
        )
        .order_by(WordSubmission.submission_timestamp.desc())
        .first()
    )

    if previous_submission:
        print(f"VALIDATION CACHE: Word='{word}' (prompt_id={sentence_prompt_id}) previously submitted. Validity: {previous_submission.is_valid}, Creativity: {previous_submission.creativity_score}")
        return WordValidationResult(
            is_valid=previous_submission.is_valid,
            creativity_score=previous_submission.creativity_score,
            from_cache=True
        )

    # 2. If not previously submitted, perform validation using Gemini
    print(f"VALIDATION GEMINI: Word='{word}' (prompt_id={sentence_prompt_id}) not previously submitted. Calling Gemini.")

    if not settings.GEMINI_API_KEY or settings.GEMINI_API_KEY == "YOUR_GEMINI_API_KEY_HERE":
        print("Error: GEMINI_API_KEY is not configured.")
        # Not logging submission here as per instruction (calling service will log)
        return WordValidationResult(is_valid=False, creativity_score=None, error_message="Gemini API key not configured")

    try:
        genai.configure(api_key=settings.GEMINI_API_KEY)
        # TODO: Confirm specific model name if 'gemini-1.5-flash-latest' isn't the exact desired one
        model = genai.GenerativeModel('gemini-1.5-flash-latest') 
    except Exception as e:
        print(f"Error configuring Gemini client: {e}")
        # Not logging submission here
        return WordValidationResult(is_valid=False, creativity_score=None, error_message=f"Gemini client configuration error: {e}")

    gemini_prompt_text = f"""
You are a word game judge. The game content is in the language with code '{language}'. Given a sentence, a target word within that sentence,
a prompt for modifying the target word, and a submitted word from a player,
determine if the submitted word is valid according to the prompt and how creative it is.
Don't be too harsh, if the word is a reasonable response to the prompt, consider it valid.

            Your response will be structured as a JSON object according to a predefined schema.

            Sentence: "{sentence_text}"
            Target Word: "{target_word}"
            Prompt: "{prompt_text}"
            Submitted Word: "{word}"

            Please provide your judgment based on these fields:
            - "is_valid": (boolean) True if the submitted word is a valid response, false otherwise.
            - "creativity_score": (integer) From 1 (obvious) to 5 (highly creative). If "is_valid" is false, this score should be 0.
            - "reason": (string) A brief explanation for your decision, especially if invalid.

            Example of how to think about the content for a valid word:
            If Sentence="The fire was warm.", Target Word="warm", Prompt="Make it more extreme!", Submitted Word="hot":
            Then is_valid=true, creativity_score=1 (or similar, based on your judgment), reason="The word 'hot' strongly amplifies 'warm'... and fits the sentence context."

            Example for an invalid word:
            If Sentence="The cat is quick.", Target Word="quick", Prompt="Use synonyms", Submitted Word="slow":
            Then is_valid=false, creativity_score=0, reason="The word 'slow' is the opposite of fast and not a synonym."
"""
    
    gemini_is_valid = False
    gemini_creativity_score = None
    gemini_reason = "Gemini call failed or produced unexpected output."

    try:
        # Use dict for generation_config as it's simpler and supported
        generation_config = {
            "response_mime_type": "application/json",
            "response_schema": GEMINI_RESPONSE_SCHEMA
        }

        response = model.generate_content(
            gemini_prompt_text,
            generation_config=generation_config
        )
        
        # Manual cleaning removed
        
        judgment = json.loads(response.text) # Parse response.text directly
            # Fields are marked as required in the schema.
            # Using .get() provides a safety net against unexpected schema violations or missing fields.
        gemini_is_valid = judgment.get("is_valid")
        gemini_creativity_score = judgment.get("creativity_score")
        gemini_reason = judgment.get("reason") # Schema requires reason

            # Initial default reason if Gemini's is missing (though schema requires it)
        if gemini_reason is None:
            gemini_reason = "No reason provided by Gemini."

            # Validate is_valid type and handle missing
        if not isinstance(gemini_is_valid, bool):
            is_valid_original_type = type(gemini_is_valid).__name__
            gemini_is_valid = False # Default to invalid
            gemini_reason = f"Validation Error: Gemini output 'is_valid' was missing or not a boolean (type: {is_valid_original_type}). Original reason: {gemini_reason}"
            gemini_creativity_score = 0 # Creativity is 0 if validity is compromised
            
            # Validate creativity_score type and handle missing (even if required, for robustness)
        if not isinstance(gemini_creativity_score, int):
            creativity_original_type = type(gemini_creativity_score).__name__
            gemini_reason = f"Validation Error: Gemini output 'creativity_score' was missing or not an integer (type: {creativity_original_type}). Original reason: {gemini_reason}"
                # Default creativity score based on determined validity so far
            gemini_creativity_score = 0 # If type is wrong, it's effectively invalid or unscorable creatively
            if gemini_is_valid: # If it was deemed valid but score type is wrong, mark as valid but minimally creative
                 gemini_creativity_score = 1 # Or keep 0 and add to reason it's unscorable

            # Adjust creativity_score based on validity
        if gemini_is_valid:
            if not (1 <= gemini_creativity_score <= 5):
                gemini_reason += f" (Note: Gemini 'creativity_score' {gemini_creativity_score} was out of range 1-5 for a valid word. Clamping to 1.)"
                gemini_creativity_score = 1 # Clamp to 1 if valid but score is out of expected range (e.g. 0 or >5)
        else: # Not valid
            if gemini_creativity_score != 0:
                    # This is a corrective measure if Gemini didn't follow the "score should be 0 if invalid" instruction.
                gemini_reason += f" (Note: Word is invalid; 'creativity_score' was {gemini_creativity_score}. Setting to 0.)"
                gemini_creativity_score = 0
            
            # Ensure reason is never None before logging it (it could be if judgment.get("reason") was None and not updated)
        if gemini_reason is None: # Should not happen with the new check above, but as a final safety.
            gemini_reason = "Reason processing failed."

        print(f"Gemini judgment for '{word}': Valid={gemini_is_valid}, Creativity={gemini_creativity_score}, Reason='{gemini_reason}'")

    except json.JSONDecodeError as e:
        print(f"Error decoding JSON from Gemini: {e}. Response: {response.text if 'response' in locals() else 'N/A'}")
        gemini_reason = f"JSON decode error: {e}"
    except Exception as e:
        print(f"Error calling Gemini or processing response: {e}")
        gemini_reason = f"Gemini processing error: {e}"

    # As per instruction, the calling service will handle logging the submission.
    # This function returns the validation result.
    return WordValidationResult(
        is_valid=gemini_is_valid, 
        creativity_score=gemini_creativity_score, 
        from_cache=False,
        error_message=None if gemini_is_valid else gemini_reason
    )