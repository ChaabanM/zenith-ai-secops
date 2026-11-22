"""Reasoning settings (``ZENITH_REASONING_*``).

The default provider is ``deterministic``: every sentence comes from templates
filled with database values, and nothing needs a model or the network. The
optional ``ollama`` provider asks a local model (via an Ollama server) to reword
the executive summary; its output is used only if it passes the grounding check.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class ReasoningSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ZENITH_REASONING_", env_file=".env", extra="ignore")

    provider: Literal["deterministic", "ollama"] = "deterministic"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2:3b"
    timeout_seconds: float = 20.0
    max_narrative_chars: int = 2000

    # prioritization weights (documented in docs/reasoning.md)
    severity_points: dict[str, float] = {"critical": 40, "high": 28, "medium": 15, "low": 6, "informational": 2}
    status_points: dict[str, float] = {"open": 12, "acknowledged": 6, "investigating": 4}
    confidence_points: float = 15  # x incident confidence (heuristic)
    corroboration_points: float = 2  # per additional independent source type, max 3
    conflict_points: float = 4  # per conflict, max 2
    device_failure_points: float = 5
    anomaly_points: float = 3
    immediate_attention_score: float = 55  # unresolved and critical/high, or at/above this score


@lru_cache
def get_reasoning_settings() -> ReasoningSettings:
    return ReasoningSettings()
