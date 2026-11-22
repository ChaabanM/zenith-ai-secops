"""Persistence for ingestion: batches, raw submissions (evidence) and normalized events.

Two layers:

* ``raw_submissions`` keeps every submitted record exactly once, whatever its outcome
  (accepted, invalid, duplicate). It is append-only evidence.
* ``events`` keeps one normalized row per external ``event_id`` (the first valid
  submission wins). Later submissions with the same ``event_id`` never overwrite it;
  they are stored as raw submissions with status ``duplicate`` that point at it.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import UTCDateTime


class SubmissionStatus:
    ACCEPTED = "accepted"
    INVALID = "invalid"
    DUPLICATE = "duplicate"


class DuplicateKind:
    EXACT = "exact"
    CONFLICTING = "conflicting"


class IngestBatch(Base):
    __tablename__ = "ingest_batches"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    channel: Mapped[str] = mapped_column(String(16))  # json | jsonl
    filename: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16))  # processing | completed | rejected | failed
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    total_received: Mapped[int] = mapped_column(Integer, default=0)
    accepted: Mapped[int] = mapped_column(Integer, default=0)
    invalid: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    exact_duplicates: Mapped[int] = mapped_column(Integer, default=0)
    conflicting_duplicates: Mapped[int] = mapped_column(Integer, default=0)
    with_warnings: Mapped[int] = mapped_column(Integer, default=0)


class RawSubmission(Base):
    __tablename__ = "raw_submissions"
    __table_args__ = (UniqueConstraint("batch_id", "position", name="uq_raw_batch_position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("ingest_batches.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)  # 1-based; line number for JSONL
    external_event_id: Mapped[str | None] = mapped_column(String(256), index=True)
    raw_content: Mapped[str] = mapped_column(Text)
    raw_size_bytes: Mapped[int] = mapped_column(Integer)
    raw_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    content_sha256: Mapped[str] = mapped_column(String(64))
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(16), index=True)
    duplicate_kind: Mapped[str | None] = mapped_column(String(16))
    conflicting_fields: Mapped[list[str] | None] = mapped_column(JSON)
    errors: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    warnings: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    # accepted -> the event it created; duplicate -> the existing event it repeats
    normalized_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("events.id", use_alter=True, name="fk_raw_normalized_event"), index=True
    )


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_occurred", "occurred_at", "id"),
        Index("ix_events_severity_occurred", "severity", "occurred_at", "id"),
        Index("ix_events_source_occurred", "source_type", "occurred_at", "id"),
        Index("ix_events_device_occurred", "device_id", "occurred_at", "id"),
        Index("ix_events_zone_occurred", "zone", "occurred_at", "id"),
        Index("ix_events_type_occurred", "event_type", "occurred_at", "id"),  # spike detection by event type
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), unique=True)
    source_type: Mapped[str] = mapped_column(String(40))
    device_id: Mapped[str] = mapped_column(String(128))
    event_type: Mapped[str] = mapped_column(String(128))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    received_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime())
    delay_seconds: Mapped[float | None] = mapped_column(Float)  # received_at - occurred_at
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    zone: Mapped[str | None] = mapped_column(String(128))
    severity: Mapped[str | None] = mapped_column(String(16))
    confidence: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    content_sha256: Mapped[str] = mapped_column(String(64))
    submission_id: Mapped[int] = mapped_column(ForeignKey("raw_submissions.id"), unique=True)
