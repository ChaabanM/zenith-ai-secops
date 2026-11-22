"""Persistence for correlated incidents.

* ``incidents``: one row per incident. Automatically computed fields (title,
  category, severity, confidence, explanation lists, time span) are rewritten by
  each correlation run; operator-maintained fields (``status``, ``status_note``,
  ``status_updated_at``) are never touched by the engine.
* ``incident_evidence``: relational links to the normalized events (and their raw
  submissions) that support the incident, with role, link type, linkage
  explanation and linkage confidence. Source events are referenced, not copied.
* ``incident_facets``: zones, devices and source types, indexed for filtering.
* ``correlation_runs``: one row per correlation run.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import UTCDateTime

INCIDENT_STATUSES = ("open", "acknowledged", "investigating", "resolved", "false_positive")
CORRELATION_STATES = ("active", "merged", "obsolete")


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        Index("ix_incidents_state_severity", "correlation_state", "severity_rank", "last_occurred_at", "id"),
        Index("ix_incidents_state_last", "correlation_state", "last_occurred_at", "id"),
        Index("ix_incidents_state_first", "correlation_state", "first_occurred_at", "id"),
        Index("ix_incidents_category", "category", "correlation_state"),
        Index("ix_incidents_status", "status", "correlation_state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(200))  # category + anchor event (informational, not the identity key)

    # ---- computed by the correlation engine ------------------------------------------
    category: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(300))
    severity: Mapped[str] = mapped_column(String(16))
    severity_rank: Mapped[int] = mapped_column(Integer)
    severity_reason: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)  # heuristic incident confidence score (not a probability)
    confidence_level: Mapped[str] = mapped_column(String(8))  # high | medium | low
    confidence_reason: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)  # evidence summary
    linkage_explanation: Mapped[str] = mapped_column(Text)
    observations: Mapped[list[str]] = mapped_column(JSON)  # verified observations
    inferences: Mapped[list[str]] = mapped_column(JSON)
    conflicts: Mapped[list[str]] = mapped_column(JSON)
    missing_information: Mapped[list[str]] = mapped_column(JSON)
    uncertainty: Mapped[list[str]] = mapped_column(JSON)
    recommended_actions: Mapped[list[str]] = mapped_column(JSON)
    details: Mapped[dict[str, Any]] = mapped_column(JSON)  # entities, counts, lineage
    first_occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    event_count: Mapped[int] = mapped_column(Integer)
    rule_version: Mapped[str] = mapped_column(String(16))
    correlation_state: Mapped[str] = mapped_column(String(16), default="active")  # active | merged | obsolete
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"))
    lineage: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # split/merge history (engine-managed, persistent)
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime())  # first time the engine produced it
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())  # last time its computed content changed
    last_run_id: Mapped[int] = mapped_column(Integer)
    content_sha256: Mapped[str] = mapped_column(String(64))

    # ---- operator-maintained (never modified by the engine) ---------------------------
    status: Mapped[str] = mapped_column(String(16), default="open")
    status_note: Mapped[str | None] = mapped_column(Text)
    status_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class IncidentEvidence(Base):
    __tablename__ = "incident_evidence"
    __table_args__ = (UniqueConstraint("incident_id", "event_pk", name="uq_incident_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    event_pk: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    external_event_id: Mapped[str] = mapped_column(String(256), index=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("raw_submissions.id"))
    role: Mapped[str] = mapped_column(String(48))
    link_type: Mapped[str] = mapped_column(String(48))
    is_core: Mapped[bool] = mapped_column(Boolean)  # strongly linked (core) vs contextual (leaf)
    linkage_explanation: Mapped[str] = mapped_column(Text)
    linkage_confidence: Mapped[float] = mapped_column(Float)
    conflict: Mapped[str | None] = mapped_column(Text)


class IncidentFacet(Base):
    __tablename__ = "incident_facets"
    __table_args__ = (Index("ix_incident_facet_lookup", "kind", "value", "incident_id"),)

    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)  # zone | device | source
    value: Mapped[str] = mapped_column(String(128), primary_key=True)


class CorrelationRun(Base):
    __tablename__ = "correlation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    duration_ms: Mapped[float | None] = mapped_column(Float)
    observations_loaded: Mapped[int] = mapped_column(Integer, default=0)
    incidents_active: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    unchanged: Mapped[int] = mapped_column(Integer, default=0)
    merged: Mapped[int] = mapped_column(Integer, default=0)
    obsoleted: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
