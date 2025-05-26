import argparse
import json
import os
import sys
import time
import requests
import google.generativeai as genai

# Add project root to Python path to allow importing app modules
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from app.core.config import settings
    # If you need direct DB access (alternative to API calls):
    from app.db.session import SessionLocal # Uncommented
    from app.crud import crud_game_content # Uncommented
except ImportError as e:
    print(f"Error importing app modules: {e}")
    print("Make sure the script is run from the project root or the PYTHONPATH is set correctly.")
    sys.exit(1)

# --- Configuration ---
GEMINI_API_KEY = settings.GEMINI_API_KEY
API_BASE_URL = "http://localhost:8000" # Assuming default FastAPI port
SENTENCE_PROMPT_ENDPOINT = f"{API_BASE_URL}/api/v1/sentence-prompts/"
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5

# --- Gemini Model Setup ---
def configure_gemini():
    if not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_GEMINI_API_KEY_HERE":
        print("Error: GEMINI_API_KEY is not configured in .env or is set to the placeholder.")
        print("Please set your Gemini API Key in the .env file (e.g., GEMINI_API_KEY='your_actual_key').")
        sys.exit(1)
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash-latest') # Or the specific flash lite model if different
        print("Gemini client configured successfully.")
        return model
    except Exception as e:
        print(f"Error configuring Gemini client: {e}")
        sys.exit(1)

# --- API Interaction ---
def add_sentence_prompt_api(sentence, target_word, prompt, difficulty):
    payload = {
        "sentence_text": sentence,
        "target_word": target_word,
        "prompt_text": prompt,
        "difficulty": difficulty
    }
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(SENTENCE_PROMPT_ENDPOINT, json=payload)
            response.raise_for_status() # Raises HTTPError for bad responses (4XX or 5XX)
            print(f"Successfully added: {sentence[:30]}...")
            return response.json()
        except requests.exceptions.HTTPError as e:
            if response.status_code == 400: # Bad Request, likely validation error (e.g. duplicate)
                print(f"Validation error: {e.response.text}")
                # Check if it's a duplicate based on our specific error message for duplicates (if we implement that in API)
                # For now, assume any 400 for specific content might be a duplicate or invalid input.
                # If the API explicitly says "duplicate", we can return None or a specific marker.
                # The prompt asked for duplicate checking *before* calling API, this is a fallback.
                return None # Or re-raise if it's not a recoverable/ignorable error
            print(f"HTTP error adding prompt (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
        except requests.exceptions.RequestException as e:
            print(f"Request error adding prompt (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
        
        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAY_SECONDS)
        else:
            print("Max retries reached. Failed to add prompt.")
            return None
    return None # Should be unreachable if loop completes

# --- Content Generation (Placeholder) ---
def generate_content_with_gemini(model):
    print("Generating content with Gemini...")
    try:
        # Construct a more detailed prompt for Gemini
        # This prompt asks for a JSON output directly.
        prompt_text = f"""
        Generate a unique and creative sentence-prompt combination for a word game.
        The game involves players finding words that fit a task, related to a target word in a sentence.

        Output the result as a single JSON object with the following exact keys:
        - "sentence": A string containing an interesting sentence.
        - "target_word": A single word from the "sentence" that will be the focus.
        - "prompt": A short, engaging instruction for the player (e.g., "Make it more intense!", "Change the mood to somber.", "Use a synonym for elegance."). The prompt should guide the player to change or describe the target_word.
        - "difficulty": An integer from 1 (very easy) to 5 (very hard), representing the estimated difficulty of finding a good word for this combination.

        Example JSON output:
        {{
            "sentence": "The old house stood silently on the hill.",
            "target_word": "silently",
            "prompt": "Describe its sound instead.",
            "difficulty": 3
        }}

        Ensure the target_word is actually present in the sentence.
        Do not include any markdown formatting (like ```json) around the JSON output.
        Generate a new, previously unseen combination.
        """
        
        response = model.generate_content(prompt_text)
        
        # Attempt to clean and parse the response
        # Gemini can sometimes include ```json markdown, try to strip it.
        cleaned_response_text = response.text.strip()
        if cleaned_response_text.startswith("```json"):
            cleaned_response_text = cleaned_response_text[7:]
        if cleaned_response_text.endswith("```"):
            cleaned_response_text = cleaned_response_text[:-3]
        
        content_data = json.loads(cleaned_response_text)
        
        # Basic validation of structure
        if not all(k in content_data for k in ["sentence", "target_word", "prompt", "difficulty"]):
            print("Error: Gemini response missing one or more required keys.")
            return None
        if not isinstance(content_data["difficulty"], int):
            print("Error: Gemini response 'difficulty' is not an integer.")
            return None
        
        print(f"Gemini generated: {content_data}")
        return content_data

    except json.JSONDecodeError as e:
        print(f"Error decoding JSON from Gemini response: {e}")
        print(f"Raw response was: {response.text}")
        return None
    except Exception as e:
        print(f"Error during Gemini content generation: {e}")
        # You might want to inspect response.prompt_feedback here if available
        # print(f"Prompt feedback: {response.prompt_feedback}")
        return None

# --- Duplicate Checking (Placeholder - API should ideally handle this, or use direct DB access) ---
# The plan asks for duplicate checking *before* calling the API.
# This would require direct DB access or a dedicated GET endpoint.
# For now, we'll rely on the API potentially rejecting duplicates (e.g. via a 400/409 error)
# or the user can implement direct DB check here if preferred.
def check_for_duplicate_db(sentence, target, prompt_text):
    db = None  # Initialize db to None
    try:
        db = SessionLocal()
        existing = crud_game_content.get_sentence_prompt_by_content(
            db, sentence_text=sentence, target_word=target, prompt_text=prompt_text
        )
        return existing is not None
    except Exception as e:
        print(f"Database error during duplicate check: {e}")
        return True # Assume duplicate or error to be safe
    finally:
        if db:
            db.close()

# --- Main Logic ---
def main():
    parser = argparse.ArgumentParser(description="Generate sentence-prompt combinations using Gemini.")
    parser.add_argument(
        "-n", "--num_examples", type=int, default=10,
        help="Number of unique sentence-prompt examples to generate and add."
    )
    args = parser.parse_args()

    print(f"Starting content generation script. Goal: {args.num_examples} unique examples.")
    
    gemini_model = configure_gemini()
    if not gemini_model:
        return

    generated_count = 0
    attempts_total = 0 # To avoid infinite loops if Gemini struggles

    while generated_count < args.num_examples and attempts_total < args.num_examples * 5: # Safety break
        attempts_total += 1
        print(f"--- Attempt {attempts_total} ---")

        # 1. Generate content
        content_data = generate_content_with_gemini(gemini_model)
        if not content_data:
            print("Failed to generate content from Gemini.")
            continue

        sentence = content_data.get("sentence")
        target_word = content_data.get("target_word")
        prompt_text = content_data.get("prompt")
        difficulty = content_data.get("difficulty")

        if not all([sentence, target_word, prompt_text, difficulty]):
            print("Generated content is missing required fields. Skipping.")
            continue
        
        if target_word.lower() not in sentence.lower():
            print(f"Target word '{target_word}' not found in sentence '{sentence}'. Skipping.")
            continue

        # 2. Check for duplicates using direct DB access
        if check_for_duplicate_db(sentence, target_word, prompt_text):
            print(f"Duplicate found in DB for: {sentence[:30]}... Skipping.")
            continue
        
        # 3. Add to database via API
        api_response = add_sentence_prompt_api(sentence, target_word, prompt_text, difficulty)

        if api_response:
            generated_count += 1
            print(f"Successfully generated and added example {generated_count}/{args.num_examples}.")
        else:
            # API call failed or indicated a problem (e.g., duplicate if API handles that)
            print(f"Failed to add example via API or it was a duplicate.")
        
        if generated_count < args.num_examples:
            time.sleep(2) # Small delay between generations

    print(f"--- Script Finished ---")
    print(f"Successfully generated and added {generated_count} unique examples.")
    if attempts_total >= args.num_examples * 5:
        print("Reached maximum attempts. There might be issues with generation or finding unique content.")

if __name__ == "__main__":
    main()
