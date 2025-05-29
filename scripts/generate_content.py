import argparse
import json
import os
import sys
import time
import requests
import google.generativeai as genai
import google.generativeai.types as genai_types

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
DEFAULT_BATCH_SIZE = 3 # Number of items to request from Gemini per call

# --- Gemini Model Setup ---
def configure_gemini():
    if not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_GEMINI_API_KEY_HERE":
        print("Error: GEMINI_API_KEY is not configured in .env or is set to the placeholder.")
        print("Please set your Gemini API Key in the .env file (e.g., GEMINI_API_KEY='your_actual_key').")
        sys.exit(1)
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-2.5-flash-preview-05-20') # Or the specific flash lite model if different
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
        "difficulty": difficulty,
        "language": "en" # Assuming English, adjust if needed
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


single_game_content_schema = genai_types.Schema(
    type=genai_types.Type.OBJECT,
    properties={
        'sentence': genai_types.Schema(type=genai_types.Type.STRING, description="An interesting sentence for the game."),
        'target_word': genai_types.Schema(type=genai_types.Type.STRING, description="A single word from the 'sentence' that will be the focus."),
        'prompt': genai_types.Schema(type=genai_types.Type.STRING, description="A short, engaging instruction for the player related to the target word. Example: 'Make it more intense!'"),
        'difficulty': genai_types.Schema(type=genai_types.Type.INTEGER, description="Estimated difficulty of finding a good word for this combination, from 1 (very easy) to 5 (very hard).")
    },
    required=['sentence', 'target_word', 'prompt', 'difficulty']
)
game_content_list_schema = genai_types.Schema(
    type=genai_types.Type.ARRAY,
    items=single_game_content_schema,
    description="A list of game content objects, each containing a sentence, target word, prompt, and difficulty."
)

# --- Content Generation (Placeholder) ---
def generate_multiple_content_items_with_gemini(model, num_items_to_generate: int):
    print(f"Generating {num_items_to_generate} content item(s) with Gemini using response schema...")
    try:
        prompt_text = f"""
        Generate {num_items_to_generate} unique and creative sentence-prompt combinations for a word game.
        Each combination should be distinct from the others in this batch.
        The game involves players finding words that fit a task, related to a target word in a sentence.

        For each combination, provide:
        - "sentence": An interesting sentence.
        - "target_word": A single word from that sentence.
        - "prompt": A short, engaging instruction for the player.
        - "difficulty": An integer from 1 (easy) to 5 (hard).

        Ensure the target_word is actually present in its corresponding sentence.
        The output must be a JSON array, where each element is an object matching the defined schema.
        """
        
        generation_config = genai_types.GenerationConfig(
            response_schema=game_content_list_schema # Use the schema for a list of items
            # temperature=0.9 # Optional: adjust for creativity
        )
        
        response = model.generate_content(
            prompt_text,
            generation_config=generation_config
        )
        
        cleaned_response_text = response.text.strip()
        # Minimal cleaning, schema should enforce JSON array
        if cleaned_response_text.startswith("```json"):
            cleaned_response_text = cleaned_response_text[7:]
        if cleaned_response_text.endswith("```"):
            cleaned_response_text = cleaned_response_text[:-3]
            
        content_data_list = json.loads(cleaned_response_text)
        
        if not isinstance(content_data_list, list):
            print("Error: Gemini response is not a list as expected by the schema.")
            print(f"Raw response was: {response.text}")
            return None

        # Validate each item in the list (basic validation)
        validated_items = []
        for item in content_data_list:
            if not all(k in item for k in ["sentence", "target_word", "prompt", "difficulty"]):
                print(f"Error: Gemini response item missing one or more required keys: {item}")
                continue # Skip this invalid item
            if not isinstance(item["difficulty"], int):
                print(f"Error: Gemini response item 'difficulty' is not an integer: {item}")
                continue # Skip this invalid item
            validated_items.append(item)
        
        print(f"Gemini generated {len(validated_items)} valid item(s) (requested {num_items_to_generate}).")
        return validated_items

    except json.JSONDecodeError as e:
        print(f"Error decoding JSON from Gemini response: {e}")
        print(f"Raw response text (at point of error): {cleaned_response_text if 'cleaned_response_text' in locals() else response.text}")
        return None
    except Exception as e:
        print(f"Error during Gemini content generation: {e}")
        if hasattr(response, 'prompt_feedback'):
             print(f"Prompt feedback: {response.prompt_feedback}")
        if hasattr(response, 'candidates') and response.candidates:
            for candidate in response.candidates:
                if hasattr(candidate, 'finish_reason') and candidate.finish_reason != 1: # 1 is "STOP"
                    print(f"Candidate finish reason: {candidate.finish_reason}")
                    if hasattr(candidate, 'safety_ratings'):
                        print(f"Safety ratings: {candidate.safety_ratings}")
        if 'response' in locals() and hasattr(response, 'text'):
            print(f"Raw response text on error: {response.text}")
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
    parser.add_argument(
        "-b", "--batch_size", type=int, default=DEFAULT_BATCH_SIZE,
        help=f"Number of examples to request from Gemini in a single API call (default: {DEFAULT_BATCH_SIZE})."
    )
    args = parser.parse_args()

    print(f"Starting content generation script. Goal: {args.num_examples} unique examples.")
    print(f"Requesting items in batches of: {args.batch_size}")
    
    gemini_model = configure_gemini()
    if not gemini_model:
        return

    generated_count = 0
    gemini_api_calls = 0
    # Safety break: max API calls = (target examples / min items per successful call (assume 1)) * some factor (e.g., 5)
    # Or more simply, target_examples * safety_factor_per_item
    max_api_calls = (args.num_examples * 3) // args.batch_size + 5 # Adjusted max calls based on batching

    while generated_count < args.num_examples and gemini_api_calls < max_api_calls:
        gemini_api_calls += 1
        print(f"\n--- Gemini API Call Attempt {gemini_api_calls}/{max_api_calls} ---")

        num_to_request_this_batch = min(args.batch_size, args.num_examples - generated_count)
        if num_to_request_this_batch <= 0: # Should not happen if loop condition is correct
            break

        content_item_list = generate_multiple_content_items_with_gemini(gemini_model, num_to_request_this_batch)
        
        if not content_item_list:
            print("Failed to generate a list of content from Gemini or list was empty/invalid. Retrying after delay...")
            time.sleep(RETRY_DELAY_SECONDS)
            continue

        items_processed_this_batch = 0
        for item_data in content_item_list:
            if generated_count >= args.num_examples:
                break # Reached target, no need to process more from this batch

            items_processed_this_batch +=1
            print(f"Processing item {items_processed_this_batch}/{len(content_item_list)} from batch...")

            sentence = item_data.get("sentence")
            target_word = item_data.get("target_word")
            prompt_text = item_data.get("prompt")
            difficulty = item_data.get("difficulty")

            # Individual item validation (already partially done in generation function)
            if not all([sentence, target_word, prompt_text, isinstance(difficulty, int)]):
                print(f"Generated item is missing required fields or has incorrect types: {item_data}. Skipping.")
                continue
            
            if target_word.lower() not in sentence.lower():
                print(f"Target word '{target_word}' not found (case-insensitive) in sentence '{sentence}'. Skipping.")
                print(f"Problematic Gemini data: {item_data}")
                continue

            if check_for_duplicate_db(sentence, target_word, prompt_text):
                print(f"Duplicate found in DB for: {sentence[:30]}... Skipping.")
                continue
            
            api_response = add_sentence_prompt_api(sentence, target_word, prompt_text, difficulty)

            if api_response:
                generated_count += 1
                print(f"Successfully generated and added example {generated_count}/{args.num_examples}.")
            else:
                print(f"Failed to add item via API (sentence: {sentence[:30]}...). May be duplicate at API level or other error.")
            
            if generated_count < args.num_examples: # Small delay between API posts, even within a batch
                time.sleep(1) 

        if generated_count < args.num_examples and items_processed_this_batch == len(content_item_list):
            # If we processed the whole batch but still need more, small delay before next Gemini call
            print(f"Batch processed. Current count: {generated_count}/{args.num_examples}. Continuing...")
            if len(content_item_list) < num_to_request_this_batch :
                 print(f"Note: Gemini returned fewer items ({len(content_item_list)}) than requested ({num_to_request_this_batch}).")
            time.sleep(2) # Delay before next big API call to Gemini

    print(f"\n--- Script Finished ---")
    print(f"Total Gemini API calls made: {gemini_api_calls}")
    print(f"Successfully generated and added {generated_count} unique examples out of {args.num_examples} requested.")
    if gemini_api_calls >= max_api_calls and generated_count < args.num_examples:
        print(f"Reached maximum API call attempts ({gemini_api_calls}). There might be issues with generation, finding unique content, or API errors.")

if __name__ == "__main__":
    main()