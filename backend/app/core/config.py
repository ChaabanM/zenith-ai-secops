"""Environment-based application settings.

Values are read from environment variables (prefix ``ZENITH_``) and an optional
``.env`` file in the backend directory. Defaults are suitable for local development.
"""

import json
from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ZENITH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Zenith AI SecOps Platform"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = False

    database_url: str = "sqlite:///./zenith.db"

    # Ingestion limits (see docs/ingestion.md)
    ingest_chunk_size: int = 1000  # records per database transaction
    max_record_bytes: int = 64 * 1024  # larger records are rejected; evidence truncated to this
    max_json_body_bytes: int = 64 * 1024 * 1024  # JSON bodies; JSONL is streamed and unbounded
    max_record_reports: int = 1000  # record-level results returned inline per ingest response

    # Either a comma-separated list (ZENITH_CORS_ORIGINS=http://localhost:5173,http://localhost:3000)
    # or a JSON list (ZENITH_CORS_ORIGINS='["http://localhost:5173"]'). An empty value allows no origins.
    # NoDecode stops pydantic-settings from JSON-decoding the raw value before the validator sees it.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                try:
                    value = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"ZENITH_CORS_ORIGINS is not a valid JSON list: {exc.msg}") from None
            else:
                value = [origin.strip() for origin in text.split(",") if origin.strip()]
        if not isinstance(value, list) or not all(isinstance(o, str) for o in value):
            raise ValueError("ZENITH_CORS_ORIGINS must be a list of origin strings")
        origins = [o.strip().rstrip("/") for o in value if o.strip()]
        for origin in origins:
            if origin != "*" and not origin.startswith(("http://", "https://")):
                raise ValueError(f"invalid CORS origin {origin!r}: expected http(s)://host[:port] or *")
        return origins


@lru_cache
def get_settings() -> Settings:
    return Settings()
