# tests/core/test_config.py (New file)
from app.core.config import get_settings, Settings

def test_get_settings_loads_defaults():
    settings = get_settings()
    assert isinstance(settings, Settings)
    assert settings.PROJECT_NAME == "Word Extremist Backend" # Check a default value
    # You can also temporarily set environment variables and test if they are picked up
    # For example, using pytest-env or monkeypatching os.environ