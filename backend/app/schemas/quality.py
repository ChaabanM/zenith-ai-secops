"""API models for data-quality issues."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.events import FacetValue, Severity, ValidationIssue

ISSUE_TYPES = (
    "duplicate_submission",
    "invalid_record",
    "delayed_event",
    "out_of_order_arrival",
    "timestamp_conflict",
    "location_conflict",
    "conflicting_sensor_results",
    "repeated_device_events",
    "event_spike",
    "device_offline",
    "device_degraded",
    "sensor_failure",
    "device_recovered",
    "device_health_warning",
    "low_confidence_detection",
    "uncorrelatable_event",
)


class QualityIssueSummary(BaseModel):
    id: int
    issue_type: str
    subtype: str | None
    severity: Severity
    title: str
    description: str
    rule_id: str
    rule_version: str
    confidence: float | None = Field(description="How certain the rule is that the issue is real (not the sensor's confidence)")
    occurred_from: datetime
    occurred_to: datetime
    time_basis: str = Field(description="occurred_at (event time) or ingested_at (records with no valid event time)")
    event_count: int
    submission_count: int
    devices: list[str]
    zones: list[str]
    sources: list[str]
    status: str
    first_detected_at: datetime
    last_detected_at: datetime


class EvidenceEvent(BaseModel):
    id: int
    event_id: str
    source_type: str
    device_id: str
    event_type: str
    occurred_at: datetime
    received_at: datetime | None
    zone: str | None
    latitude: float | None
    longitude: float | None
    severity: Severity | None
    confidence: float | None


class EvidenceSubmission(BaseModel):
    id: int
    batch_id: str
    position: int
    status: str
    duplicate_kind: str | None
    external_event_id: str | None
    errors: list[ValidationIssue]
    ingested_at: datetime


class EvidenceItem(BaseModel):
    role: str = Field(description="What this record contributes to the finding")
    external_event_id: str | None
    event: EvidenceEvent | None = Field(description="Normalized event (GET /events/{id}); null for rejected records")
    submission: EvidenceSubmission | None = Field(description="Raw submission (GET /submissions/{id} returns the original content)")


class QualityIssueDetail(QualityIssueSummary):
    detection_reason: str
    uncertainty: str | None
    missing_evidence: str | None
    details: dict[str, Any]
    evidence: list[EvidenceItem]
    evidence_total: int
    evidence_truncated: bool


class QualityRunOut(BaseModel):
    id: int
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: float | None
    events_analyzed: int
    submissions_analyzed: int
    issues_total: int
    inserted: int
    updated: int
    unchanged: int
    removed: int
    details: dict[str, Any] | None
    error: str | None


class QualitySummary(BaseModel):
    total: int
    by_type: list[FacetValue]
    by_severity: list[FacetValue]
    by_rule: list[FacetValue]
    devices: list[FacetValue]
    zones: list[FacetValue]
    sources: list[FacetValue]
    last_run: QualityRunOut | None
    analysis_pending: bool = Field(description="A background analysis is queued or running")

