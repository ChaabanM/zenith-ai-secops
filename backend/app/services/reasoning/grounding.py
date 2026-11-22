"""Grounding checks.

* ``verify_references``: every event / incident / quality-issue reference in an
  output must exist in the database (events must also match their external id).
* ``check_narrative``: generated text may only use identifiers and numbers that
  already appear in the deterministic, database-derived content. Anything else
  (a new event id, incident number, device name or count) rejects the text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.incidents import Incident
from app.models.ingestion import Event
from app.models.quality import QualityIssue
from app.schemas.briefing import EvidenceRef

EVENT_ID = re.compile(r"\bevt-[A-Za-z0-9_-]+")
HASH_REF = re.compile(r"#\d+")
HYPHENATED = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+\b")
NUMBER = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?")
COMMON_HYPHENATED = {
    "low-confidence", "out-of-order", "follow-up", "real-time", "high-priority", "high-severity", "non-critical",
    "re-run", "day-to-day", "time-window", "multi-sensor", "cross-sensor", "on-site", "up-to-date", "short-term",
}


def _chunks(values: list, size: int = 500) -> Iterable[list]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def verify_references(db: Session, events: list[EvidenceRef], incident_ids: list[int], quality_ids: list[int]) -> list[str]:
    """Return a description of every reference that does not resolve (empty list = fully grounded)."""
    bad: list[str] = []
    wanted = {ref.event_pk: ref.event_id for ref in events}
    found: dict[int, str] = {}
    for part in _chunks(sorted(wanted)):
        found.update(db.execute(select(Event.id, Event.event_id).where(Event.id.in_(part))).tuples().all())
    bad += [f"event {pk} ({eid})" for pk, eid in wanted.items() if found.get(pk) != eid]
    for model, ids, label in ((Incident, incident_ids, "incident"), (QualityIssue, quality_ids, "quality issue")):
        present: set[int] = set()
        for part in _chunks(sorted(set(ids))):
            present.update(db.scalars(select(model.id).where(model.id.in_(part))))
        bad += [f"{label} {i}" for i in sorted(set(ids) - present)]
    return bad


def _numbers(text: str) -> set[float]:
    return {float(n) for n in NUMBER.findall(text)}


def check_narrative(text: str, grounded_corpus: str, max_chars: int) -> str | None:
    """Return None if ``text`` only uses identifiers and numbers from the corpus, else the reason it is rejected."""
    if not text or not text.strip():
        return "empty output"
    if len(text) > max_chars:
        return f"output longer than {max_chars} characters"
    corpus_lower = grounded_corpus.lower()
    for eid in EVENT_ID.findall(text):
        if eid.lower() not in corpus_lower:
            return f"unknown event id {eid}"
    allowed_refs = set(HASH_REF.findall(grounded_corpus))
    for ref in HASH_REF.findall(text):
        if ref not in allowed_refs:
            return f"unknown reference {ref}"
    for token in HYPHENATED.findall(text):
        if token.lower() not in corpus_lower and token.lower() not in COMMON_HYPHENATED:
            return f"unknown identifier {token}"
    extra = _numbers(text) - _numbers(grounded_corpus)
    if extra:
        return f"number(s) not present in the source data: {sorted(extra)[:5]}"
    return None
