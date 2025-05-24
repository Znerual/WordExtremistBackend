# tests/api/test_game_data_api.py
from fastapi.testclient import TestClient
from app.core.config import settings
from app.crud import crud_game_content # To add data to test DB
from sqlalchemy.orm import Session # To use db_session

def test_get_random_sentence_prompt_api_success(client: TestClient, db_session: Session):
    # Add a prompt to the database
    crud_game_content.create_sentence_prompt(
        db_session, sentence_text="API Test Sentence", target_word="API", prompt_text="TEST THIS"
    )

    response = client.get(f"{settings.API_V1_STR}/game-content/sentence-prompt/random")
    assert response.status_code == 200
    data = response.json()
    assert data["sentence_text"] == "API Test Sentence"
    assert data["target_word"] == "API"
    assert data["prompt_text"] == "TEST THIS"

def test_get_random_sentence_prompt_api_not_found(client: TestClient, db_session: Session):
    # Ensure DB is empty of prompts for this test (db_session fixture ensures isolation)
    response = client.get(f"{settings.API_V1_STR}/game-content/sentence-prompt/random")
    assert response.status_code == 404
    assert "No sentence prompts found" in response.json()["detail"]