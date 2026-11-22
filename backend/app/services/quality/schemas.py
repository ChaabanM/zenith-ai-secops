"""Internal data structures produced by quality rules (not API models)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Evidence:
    role: str
    event_pk: int | None = None
    submission_id: int | None = None
    external_event_id: str | None = None


@dataclass
class IssueDraft:
    """A finding produced by a rule. ``fingerprint`` must be deterministic."""

    fingerprint: str
    rule_id: str
    rule_version: str
    issue_type: str
    severity: str
    title: str
    description: str
    detection_reason: str
    occurred_from: datetime
    occurred_to: datetime
    subtype: str | None = None
    time_basis: str = "occurred_at"
    confidence: float | None = None
    uncertainty: str | None = None
    missing_evidence: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    devices: set[str] = field(default_factory=set)
    zones: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)

    def evidence_counts(self) -> tuple[int, int]:
        events = {e.event_pk for e in self.evidence if e.event_pk is not None}
        submissions = {e.submission_id for e in self.evidence if e.submission_id is not None}
        return len(events), len(submissions)

    def content_hash(self) -> str:
        """Hash of everything that defines the finding (used to detect changes on re-runs)."""
        body = {
            "type": self.issue_type,
            "subtype": self.subtype,
            "rule": [self.rule_id, self.rule_version],
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "reason": self.detection_reason,
            "confidence": self.confidence,
            "uncertainty": self.uncertainty,
            "missing": self.missing_evidence,
            "details": self.details,
            "time": [self.occurred_from.isoformat(), self.occurred_to.isoformat(), self.time_basis],
            "evidence": [[e.role, e.event_pk, e.submission_id, e.external_event_id] for e in self.evidence],
            "facets": [sorted(self.devices), sorted(self.zones), sorted(self.sources)],
        }
        text = json.dumps(body, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(text.encode()).hexdigest()
