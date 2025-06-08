# app/services/bot_service.py
import logging
import random
import json
import time
from typing import Tuple, Optional
from sqlalchemy import func
from sqlalchemy.orm import Session
import google.generativeai as genai

from app.models.game import GameState, GameStatePlayer
from app.schemas.game_log import WordSubmission
from app.core.config import settings

logger = logging.getLogger("app.services.bot_service")

GEMINI_BOT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "word": {"type": "STRING", "description": "A single, creative, and valid word that fits the sentence and the prompt."},
        "creativity": {"type": "INTEGER", "description": "A self-assessed creativity score for the word from 0 (obvious) to 5 (highly creative)."}
    },
    "required": ["word", "creativity"]
}

def _get_opponent(game_state: GameState) -> Optional[GameStatePlayer]:
    """Finds the human opponent in the game."""
    bot_id = game_state.current_player_id
    for player_id, player_state in game_state.players.items():
        if player_id != bot_id:
            return player_state
    return None

def _calculate_probability(opponent_level: int, max_prob: float, min_prob: float) -> float:
    """Calculates a probability that decreases as the opponent's level increases."""
    if opponent_level >= settings.LEVEL_CAP_FOR_SCALING:
        return min_prob
    if opponent_level <= 1:
        return max_prob
    
    # Linear interpolation between max and min probabilities
    progress = (opponent_level - 1) / (settings.LEVEL_CAP_FOR_SCALING - 1)
    probability = max_prob - progress * (max_prob - min_prob)
    return max(min_prob, probability)

def _get_mistake_move(game_state: GameState) -> str:
    """Returns a word that is intentionally a mistake."""
    # Mistake type 1: Repeat a word if possible
    if game_state.words_played_this_round_all:
        mistake_word = random.choice(game_state.words_played_this_round_all)
        logger.info(f"Bot decided to make a mistake by repeating word: '{mistake_word}'")
        return mistake_word
    
    # Mistake type 2: Submit the target word, which is not creative
    mistake_word = game_state.sentence_prompt.target_word
    logger.info(f"Bot decided to make a mistake by submitting the target word: '{mistake_word}'")
    return mistake_word

def _get_gemini_novel_word(game_state: GameState) -> Optional[Tuple[str, int]]:
    """Uses Gemini to generate a novel word for the bot."""
    if not settings.GEMINI_API_KEY or settings.GEMINI_API_KEY == "YOUR_GEMINI_API_KEY_HERE":
        logger.error("BOT ERROR: GEMINI_API_KEY is not configured. Cannot generate novel word.")
        return None

    try:
        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
    except Exception as e:
        logger.exception(f"BOT ERROR: Error configuring Gemini client: {e}")
        return None

    prompt = game_state.sentence_prompt
    words_to_avoid = ", ".join(game_state.words_played_this_round_all)
    
    gemini_prompt_text = f"""
You are a creative player in a word game in language '{game_state.language}'. 
Your goal is to find a single, novel word to replace the target word in the sentence, based on the prompt.
Do not repeat any of the words already played in this round.

Your response must be a JSON object matching this schema: {json.dumps(GEMINI_BOT_SCHEMA)}

Sentence: "{prompt.sentence_text}"
Target Word: "{prompt.target_word}"
Prompt: "{prompt.prompt_text}"
Words Already Played (Avoid These): "{words_to_avoid}"

Think of a creative and valid word. Provide the word and a self-assessed creativity score from 1 to 5.
"""
    
    try:
        response = model.generate_content(
            gemini_prompt_text,
            generation_config={"response_mime_type": "application/json", "response_schema": GEMINI_BOT_SCHEMA}
        )
        
        result = json.loads(response.text)
        word = result.get("word", "").strip()
        creativity = result.get("creativity", 1)

        if not isinstance(creativity, int) or not (1 <= creativity <= 5):
            creativity = 1 # Clamp to a safe value

        if word and word.lower() not in game_state.words_played_this_round_all:
            logger.info(f"Bot's Gemini call successful. Word: '{word}', Creativity: {creativity}")
            return word, creativity
        else:
            logger.warning(f"Bot's Gemini call produced an empty or repeated word: '{word}'.")
            return None

    except Exception as e:
        logger.exception(f"BOT ERROR: Gemini call failed. {e}")
        return None

def get_bot_move(game_state: GameState, db: Session) -> Tuple[Optional[str], int]:
    """
    Determines the bot's next move, which could be a valid word, a mistake, or a timeout.
    Returns a tuple of (word, creativity_score). If word is None, it signifies a timeout.
    """
    opponent = _get_opponent(game_state)
    if not opponent:
        logger.error("Bot could not find an opponent in the game state. Aborting move.")
        return game_state.sentence_prompt.target_word, 1 # Fallback

    opponent_level = opponent.level
    
    # 1. Decide if the bot should make a mistake
    mistake_prob = _calculate_probability(opponent_level, settings.MAX_MISTAKE_PROBABILITY, settings.MIN_MISTAKE_PROBABILITY)
    if random.random() < mistake_prob:
        return _get_mistake_move(game_state), 1 # Mistakes have low "creativity"

    # 2. Decide if the bot should "time out"
    timeout_prob = _calculate_probability(opponent_level, settings.MAX_TIMEOUT_PROBABILITY, settings.MIN_TIMEOUT_PROBABILITY)
    if random.random() < timeout_prob:
        logger.info(f"Bot decided to 'time out' for this round against level {opponent_level} player.")
        return None, 0 # Signal for timeout

    # 3. Try to find a good word from the database (cheap and fast)
    try:
        previous_valid_words_q = (
            db.query(WordSubmission)
            .filter(
                WordSubmission.sentence_prompt_id == game_state.sentence_prompt.id,
                WordSubmission.is_valid == True,
                WordSubmission.creativity_score > 1, # Look for decent words
                WordSubmission.submitted_word.notin_(game_state.words_played_this_round_all)
            )
            .order_by(func.random()) # Get a random one
            .first()
        )
        if previous_valid_words_q:
            logger.info(f"Bot found previous valid word '{previous_valid_words_q.submitted_word}' from DB.")
            # Use the stored creativity, or a default
            creativity = previous_valid_words_q.creativity_score or 2
            return previous_valid_words_q.submitted_word, creativity
    except Exception as e:
        logger.error(f"BOT ERROR: DB lookup for bot move failed: {e}", exc_info=True)

    # 4. If no DB word, use Gemini to find a novel word
    gemini_result = _get_gemini_novel_word(game_state)
    if gemini_result:
        return gemini_result

    # 5. Fallback if all else fails
    logger.warning("Bot is using fallback move (target word) after other strategies failed.")
    return game_state.sentence_prompt.target_word, 1