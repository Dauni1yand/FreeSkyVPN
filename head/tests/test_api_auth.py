"""The service token is the only thing standing between a caller and
another user's config, so its absence must be a hard failure rather than a
soft one."""

import pytest
from fastapi import HTTPException

from app.api.auth import require_service_token
from app.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_correct_token_is_accepted(monkeypatch):
    monkeypatch.setenv("HEAD_SECRET_KEY", "s3cret")
    get_settings.cache_clear()

    require_service_token("s3cret")  # must not raise


def test_wrong_token_is_rejected(monkeypatch):
    monkeypatch.setenv("HEAD_SECRET_KEY", "s3cret")
    get_settings.cache_clear()

    with pytest.raises(HTTPException) as excinfo:
        require_service_token("guess")
    assert excinfo.value.status_code == 401


def test_missing_token_is_rejected(monkeypatch):
    monkeypatch.setenv("HEAD_SECRET_KEY", "s3cret")
    get_settings.cache_clear()

    with pytest.raises(HTTPException) as excinfo:
        require_service_token(None)
    assert excinfo.value.status_code == 401


def test_default_secret_refuses_to_serve(monkeypatch):
    """Shipping with the placeholder secret would authenticate everyone."""
    monkeypatch.setenv("HEAD_SECRET_KEY", "change-me")
    get_settings.cache_clear()

    with pytest.raises(HTTPException) as excinfo:
        require_service_token("change-me")
    assert excinfo.value.status_code == 500


def test_empty_secret_refuses_to_serve(monkeypatch):
    monkeypatch.setenv("HEAD_SECRET_KEY", "")
    get_settings.cache_clear()

    with pytest.raises(HTTPException) as excinfo:
        require_service_token("")
    assert excinfo.value.status_code == 500
