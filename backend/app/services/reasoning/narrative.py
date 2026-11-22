"""Executive-summary narrative: deterministic template, optionally reworded by a local model.

The deterministic text is always produced first and is complete on its own. A
configured model may only *reword* it: the model receives that text plus the
grounded facts, and its answer is accepted only when ``check_narrative`` finds no
identifier or number that is absent from the database-derived content. Any
error, timeout or rejected answer falls back to the deterministic text, and the
response says which text was used and why.
"""

from __future__ import annotations

import json
import urllib.request
from contextlib import contextmanager
from typing import Protocol

from app.schemas.briefing import Narrative
from app.services.reasoning.configuration import ReasoningSettings, get_reasoning_settings
from app.services.reasoning.grounding import check_narrative

PROMPT = """You are helping a security operations manager. Rewrite the briefing summary below in clear, concise
operational English (at most 6 sentences). Rules: use ONLY facts, identifiers and numbers that appear in the summary or
the facts; do not add events, incidents, devices, locations, numbers or causes; keep uncertainty statements; keep
incident numbers like #12 exactly; do not present inferences as verified facts. Return only the rewritten text.

SUMMARY:
{summary}

FACTS (JSON):
{facts}
"""


class NarrativeProvider(Protocol):
    name: str

    def generate(self, prompt: str) -> str: ...


class OllamaProvider:
    """Local model through an Ollama server (no GPU or paid API required; CPU models are slow but work)."""

    def __init__(self, url: str, model: str, timeout: float) -> None:
        self.url, self.model, self.timeout = url.rstrip("/"), model, timeout
        self.name = f"ollama:{model}"

    def generate(self, prompt: str) -> str:
        body = json.dumps({"model": self.model, "prompt": prompt, "stream": False, "options": {"temperature": 0}}).encode()
        req = urllib.request.Request(f"{self.url}/api/generate", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (configured local URL)
            return str(json.loads(resp.read().decode())["response"]).strip()


_override: list[NarrativeProvider | None] = []


@contextmanager
def use_provider(provider: NarrativeProvider | None):
    """Replace the configured provider (tests, or callers that manage their own model)."""
    _override.append(provider)
    try:
        yield
    finally:
        _override.pop()


def configured_provider(settings: ReasoningSettings | None = None) -> NarrativeProvider | None:
    if _override:
        return _override[-1]
    s = settings or get_reasoning_settings()
    if s.provider == "ollama":
        return OllamaProvider(s.ollama_url, s.ollama_model, s.timeout_seconds)
    return None


def compose(deterministic_text: str, grounded_corpus: str, facts: dict, settings: ReasoningSettings | None = None) -> Narrative:
    s = settings or get_reasoning_settings()
    provider = configured_provider(s)
    if provider is None:
        return Narrative(text=deterministic_text, generated_by="deterministic_template", provider="deterministic", validated=True)
    try:
        candidate = provider.generate(PROMPT.format(summary=deterministic_text, facts=json.dumps(facts, default=str)[:6000]))
    except Exception as exc:  # noqa: BLE001 - any provider failure falls back
        reason = f"provider {provider.name} failed: {type(exc).__name__}: {str(exc)[:200]}"
    else:
        problem = check_narrative(candidate, grounded_corpus, s.max_narrative_chars)
        if problem is None:
            return Narrative(text=candidate, generated_by="local_language_model", provider=provider.name, validated=True)
        reason = f"model output rejected by the grounding check: {problem}"
    return Narrative(text=deterministic_text, generated_by="deterministic_template", provider="deterministic", validated=True,
                     fallback_reason=reason)
