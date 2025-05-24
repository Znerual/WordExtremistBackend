# tests/crud/test_crud_game_content.py
from sqlalchemy.orm import Session
from app.crud import crud_game_content
from app.schemas.game_content import SentencePrompt as DBSentencePrompt

def test_create_sentence_prompt(db_session: Session):
    sentence = "This is a test sentence."
    target = "test"
    prompt = "BE MORE SPECIFIC"

    db_item = crud_game_content.create_sentence_prompt(
        db_session, sentence_text=sentence, target_word=target, prompt_text=prompt
    )
    assert db_item.sentence_text == sentence
    assert db_item.target_word == target
    assert db_item.prompt_text == prompt
    assert db_item.id is not None

    queried_item = db_session.query(DBSentencePrompt).filter(DBSentencePrompt.id == db_item.id).first()
    assert queried_item is not None
    assert queried_item.sentence_text == sentence

def test_get_random_sentence_prompt(db_session: Session):
    # Case 1: No prompts in DB
    item_none = crud_game_content.get_random_sentence_prompt(db_session)
    assert item_none is None

    # Case 2: One prompt in DB
    crud_game_content.create_sentence_prompt(db_session, "s1", "t1", "p1")
    item_one = crud_game_content.get_random_sentence_prompt(db_session)
    assert item_one is not None
    assert item_one.sentence_text == "s1"

    # Case 3: Multiple prompts (hard to test randomness, just ensure one is returned)
    crud_game_content.create_sentence_prompt(db_session, "s2", "t2", "p2")
    crud_game_content.create_sentence_prompt(db_session, "s3", "t3", "p3")
    item_multiple = crud_game_content.get_random_sentence_prompt(db_session)
    assert item_multiple is not None
    assert item_multiple.id is not None