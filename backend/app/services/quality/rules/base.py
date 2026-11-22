"""Shared building blocks for quality rules.

A rule is a small class with a stable ``rule_id``/``version`` and a ``detect``
generator that reads the database through bounded, index-backed, streamed queries
and yields ``IssueDraft`` objects. Rules never write; the engine persists.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import Select, func
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.models.ingestion import Event
from app.services.quality.configuration import QualitySettings
from app.services.quality.schemas import Evidence, IssueDraft

STREAM_BATCH = 2000

# Columns most rules need; payload fields are pulled with json_extract when required.
EVENT_COLUMNS = (
    Event.id,
    Event.event_id,
    Event.submission_id,
    Event.source_type,
    Event.device_id,
    Event.event_type,
    Event.occurred_at,
    Event.received_at,
    Event.delay_seconds,
    Event.zone,
    Event.latitude,
    Event.longitude,
    Event.severity,
    Event.confidence,
)


def payload_field(path: str) -> Any:
    """SQL expression for a payload field, e.g. ``payload_field("plate_number")``."""
    return func.json_extract(Event.payload, f"$.{path}")


@dataclass
class QualityContext:
    db: Session
    settings: QualitySettings

    def stream(self, stmt: Select) -> Iterator[Row]:
        """Iterate a query in fixed-size batches (bounded memory for 50K+ rows)."""
        yield from self.db.execute(stmt.execution_options(yield_per=STREAM_BATCH))


class Rule:
    rule_id: ClassVar[str]
    version: ClassVar[str] = "1.0"
    issue_type: ClassVar[str]
    summary: ClassVar[str]

    def detect(self, ctx: QualityContext) -> Iterable[IssueDraft]:  # pragma: no cover - interface
        raise NotImplementedError

    def draft(self, fingerprint_key: str, **kwargs: Any) -> IssueDraft:
        kwargs.setdefault("issue_type", self.issue_type)
        return IssueDraft(fingerprint=f"{self.rule_id}:{fingerprint_key}", rule_id=self.rule_id, rule_version=self.version, **kwargs)


# --------------------------------------------------------------------------- helpers


def event_evidence(row: Any, role: str) -> Evidence:
    return Evidence(role=role, event_pk=row.id, submission_id=row.submission_id, external_event_id=row.event_id)


def add_facets(draft: IssueDraft, rows: Iterable[Any]) -> IssueDraft:
    for row in rows:
        draft.devices.add(row.device_id)
        draft.sources.add(row.source_type)
        if row.zone:
            draft.zones.add(row.zone)
    return draft


def span(rows: Iterable[Any]) -> tuple[datetime, datetime]:
    times = [r.occurred_at for r in rows]
    return min(times), max(times)


def aggregate_time(value: Any) -> datetime | None:
    """min()/max() over a UTCDateTime column returns raw naive values: make them UTC-aware."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def fmt_duration(seconds: float) -> str:
    s = abs(seconds)
    if s < 90:
        return f"{s:.0f} s"
    if s < 5400:
        return f"{s / 60:.1f} min"
    if s < 172800:
        return f"{s / 3600:.1f} h"
    return f"{s / 86400:.1f} days"


def fmt_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


def describe(row: Any) -> str:
    """Short human reference to an event: 'anpr plate_read evt-123 on anpr-int01'."""
    return f"{row.source_type} {row.event_type} {row.event_id} on {row.device_id}"


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle (haversine) distance."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def worst(*severities: str) -> str:
    order = ["informational", "low", "medium", "high", "critical"]
    return max(severities, key=order.index)
