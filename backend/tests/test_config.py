"""ZENITH_CORS_ORIGINS parsing (regression: the comma-separated form crashed startup)."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('["http://localhost:5173","http://127.0.0.1:5173"]', ["http://localhost:5173", "http://127.0.0.1:5173"]),
        ("http://localhost:5173,http://localhost:3000", ["http://localhost:5173", "http://localhost:3000"]),
        (" http://a.example , https://b.example/ ", ["http://a.example", "https://b.example"]),
        ("http://localhost:5174", ["http://localhost:5174"]),
        ("*", ["*"]),
        ("", []),
        ("[]", []),
    ],
)
def test_cors_origins_formats(monkeypatch, raw, expected):
    monkeypatch.setenv("ZENITH_CORS_ORIGINS", raw)
    assert Settings().cors_origins == expected


@pytest.mark.parametrize("raw", ['["http://a", ', "localhost:5173", '{"a": 1}', "[1, 2]", "ftp://x"])
def test_invalid_cors_origins_fail_with_a_clear_error(monkeypatch, raw):
    monkeypatch.setenv("ZENITH_CORS_ORIGINS", raw)
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert "cors_origins" in str(exc.value).lower() or "CORS" in str(exc.value)


def test_default_when_unset(monkeypatch):
    monkeypatch.delenv("ZENITH_CORS_ORIGINS", raising=False)
    assert Settings(_env_file=None).cors_origins == ["http://localhost:5173", "http://127.0.0.1:5173"]


@pytest.mark.parametrize("raw", ["http://localhost:5174,http://localhost:3000", '["http://localhost:5174"]'])
def test_app_starts_and_applies_cors_with_either_format(monkeypatch, raw):
    from app.core import config
    from app.main import create_app

    monkeypatch.setenv("ZENITH_CORS_ORIGINS", raw)
    config.get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            response = client.get("/health", headers={"Origin": "http://localhost:5174"})
            assert response.status_code == 200
            assert response.headers.get("access-control-allow-origin") == "http://localhost:5174"
            blocked = client.get("/health", headers={"Origin": "http://evil.example"})
            assert "access-control-allow-origin" not in blocked.headers
    finally:
        config.get_settings.cache_clear()
