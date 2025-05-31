from enum import Enum

class RoundEndReason(Enum):
    REPEATED_WORD_MAX_MISTAKES = "repeated_word_max_mistakes"
    INVALID_WORD_MAX_MISTAKES = "invalid_word_max_mistakes"
    TIMEOUT_MAX_MISTAKES = "timeout_max_mistakes"
    DOUBLE_TIMEOUT = "double_timeout"
    OPPONENT_DISCONNECTED = "opponent_disconnected"
    MAX_ROUNDS_REACHED_OR_SCORE_LIMIT = "max_rounds_reached_or_score_limit" # General reason for normal game conclusion
    UNKNOWN = "unknown"  # Fallback reason for unexpected cases
    # Add more specific reasons if needed