# tests/core/test_security.py
import pytest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException

from app.core.security import verify_google_id_token
from app.core.config import settings # To get GOOGLE_CLIENT_ID

@pytest.mark.asyncio
async def test_verify_google_id_token_success(mocker):
    mock_google_verify = mocker.patch("google.oauth2.id_token.verify_oauth2_token")
    mock_payload = {
        "iss": "accounts.google.com",
        "sub": "test_google_user_123",
        "aud": settings.GOOGLE_CLIENT_ID,
        "email": "test@example.com",
        "email_verified": True,
        "name": "Test User",
        "picture": "http://example.com/pic.jpg",
        "exp": 9999999999 # A future timestamp
    }
    mock_google_verify.return_value = mock_payload

    token_str = "fake_google_id_token"
    payload = await verify_google_id_token(token_str)

    assert payload == mock_payload
    mock_google_verify.assert_called_once_with(
        token_str, mocker.ANY, settings.GOOGLE_CLIENT_ID
    )

@pytest.mark.asyncio
async def test_verify_google_id_token_invalid_issuer(mocker):
    mock_google_verify = mocker.patch("google.oauth2.id_token.verify_oauth2_token")
    mock_payload = {
        "iss": "not.google.com", # Invalid issuer
        "sub": "test_google_user_123",
        "aud": settings.GOOGLE_CLIENT_ID,
        "email": "test@example.com",
        "exp": 9999999999
    }
    mock_google_verify.return_value = mock_payload

    with pytest.raises(HTTPException) as exc_info:
        await verify_google_id_token("fake_token")
    assert exc_info.value.status_code == 401
    assert "Could not validate Google credentials" in str(exc_info.value.detail) # This is correct
# The stdout "Google ID Token ValueError: Wrong issuer." confirms your internal check.
   
@pytest.mark.asyncio
async def test_verify_google_id_token_value_error_from_google_lib(mocker):
    mock_google_verify = mocker.patch("google.oauth2.id_token.verify_oauth2_token")
    mock_google_verify.side_effect = ValueError("Google lib validation failed")

    with pytest.raises(HTTPException) as exc_info:
        await verify_google_id_token("fake_token")
    assert exc_info.value.status_code == 401
    assert "Could not validate Google credentials" in exc_info.value.detail