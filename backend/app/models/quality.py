"""Persistence for data-quality findings.

* ``quality_issues``: one row per finding, identified by a deterministic
  ``fingerprint`` (rule id + the evidence that defines it) so re-running the
  analysis updates rows instead of duplicating them.
* ``quality_issue_evidence``: relational links from an issue to the normalized
  events and/or raw submissions that support it (with a role per link).
* ``quality_issue_facets``: device ids, zones and source types an issue concerns,
  indexed for filtering.
* ``quality_runs``: one row per analysis run (trigger, duration, counts).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import UTCDateTime

SEVERITY_RANK = {"critical": 5, "high": 4, "medium": 3, "low": 2, "informational": 1}


class QualityIssue(Base):
    __tablename__ = "quality_issues"
    __table_args__ = (
        Index("ix_quality_severity", "severity_rank", "occurred_to", "id"),
        Index("ix_quality_type", "issue_type", "severity_rank", "occurred_to", "id"),
        Index("ix_quality_occurred", "occurred_to", "id"),
        Index("ix_quality_occurred_from", "occurred_from"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(200), unique=True)
    issue_type: Mapped[str] = mapped_column(String(48))
    subtype: Mapped[str | None] = mapped_column(String(64))
    rule_id: Mapped[str] = mapped_column(String(16), index=True)
    rule_version: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(16))
    severity_rank: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    detection_reason: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)  # how certain the rule is that the issue is real
    uncertainty: Mapped[str | None] = mapped_column(Text)
    missing_evidence: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSON)
    # event-time span of the evidence (ingestion time when no valid event time exists)
    occurred_from: Mapped[datetime] = mapped_column(UTCDateTime())
    occurred_to: Mapped[datetime] = mapped_column(UTCDateTime())
    time_basis: Mapped[str] = mapped_column(String(16))  # occurred_at | ingested_at
    event_count: Mapped[int] = mapped_column(Integer)
    submission_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="open")
    first_detected_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_detected_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_run_id: Mapped[int] = mapped_column(Integer)
    content_sha256: Mapped[str] = mapped_column(String(64))


class QualityIssueEvidence(Base):
    __tablename__ = "quality_issue_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("quality_issues.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(48))
    event_pk: Mapped[int | None] = mapped_column(ForeignKey("events.id"), index=True)
    submission_id: Mapped[int | None] = mapped_column(ForeignKey("raw_submissions.id"), index=True)
    external_event_id: Mapped[str | None] = mapped_column(String(256), index=True)


class QualityIssueFacet(Base):
    __tablename__ = "quality_issue_facets"
    __table_args__ = (Index("ix_quality_facet_lookup", "kind", "value", "issue_id"),)

    issue_id: Mapped[int] = mapped_column(ForeignKey("quality_issues.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)  # device | zone | source
    value: Mapped[str] = mapped_column(String(128), primary_key=True)


class QualityRun(Base):
    __tablename__ = "quality_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger: Mapped[str] = mapped_column(String(32))  # ingest | manual | startup
    status: Mapped[str] = mapped_column(String(16))  # running | completed | failed
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    duration_ms: Mapped[float | None] = mapped_column(Float)
    events_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    submissions_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    issues_total: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    unchanged: Mapped[int] = mapped_column(Integer, default=0)
    removed: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # per-rule counts and timings
    error: Mapped[str | None] = mapped_column(Text)
