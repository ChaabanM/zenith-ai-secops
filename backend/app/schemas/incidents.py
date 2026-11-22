"""API models for incidents."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.events import FacetValue, Severity

IncidentStatus = Literal["open", "acknowledged", "investigating", "resolved", "false_positive"]
INCIDENT_CATEGORIES = (
    "traffic_enforcement",
    "unauthorized_access",
    "perimeter_intrusion",
    "suspicious_activity",
    "emergency",
    "device_failure",
    "cyber_security",
    "fleet_anomaly",
)


class IncidentSummary(BaseModel):
    id: int
    title: str
    category: str
    severity: Severity
    confidence: float = Field(description="Heuristic incident confidence score in [0, 1]; not a calibrated probability")
    confidence_level: Literal["high", "medium", "low"]
    status: IncidentStatus = Field(description="Operator-maintained; never changed by the correlation engine")
    correlation_state: Literal["active", "merged", "obsolete"]
    first_occurred_at: datetime
    last_occurred_at: datetime
    detected_at: datetime
    updated_at: datetime
    event_count: int
    zones: list[str]
    devices: list[str]
    sources: list[str]
    summary: str
    conflict_count: int
    rule_version: str


class TimelineEntry(BaseModel):
    event_pk: int = Field(description="Internal event id (GET /events/{id})")
    event_id: str = Field(description="Original external event_id as submitted")
    submission_id: int = Field(description="Raw submission that created the event (GET /submissions/{id})")
    occurred_at: datetime
    received_at: datetime | None
    source_type: str
    device_id: str
    event_type: str
    zone: str | None
    severity: Severity | None
    source_confidence: float | None = Field(description="The sensor's own detection confidence")
    role: str
    link_type: str
    core: bool = Field(description="True: strongly linked (shared identifier / explicit reference); false: contextual leaf")
    linkage_confidence: float = Field(description="Strength of the link to the incident (heuristic, per link type)")
    linkage_explanation: str
    link_description: str = Field(description="What this link type means (same for every link of the type)")
    low_source_confidence: bool = Field(
        description="True when the sensor's own confidence is below the engine's low-confidence threshold: treat as uncertain"
    )
    conflict: str | None


class IncidentQualityIssue(BaseModel):
    id: int
    issue_type: str
    subtype: str | None
    severity: Severity
    title: str
    event_ids: list[str]


class IncidentDetail(IncidentSummary):
    severity_reason: str
    confidence_reason: str
    linkage_explanation: str
    observations: list[str] = Field(description="Verified observations: what the sensors reported")
    inferences: list[str] = Field(description="Conclusions drawn by the engine from linkage, not observed directly")
    conflicts: list[str]
    missing_information: list[str]
    uncertainty: list[str]
    recommended_actions: list[str] = Field(description="Advisory only; nothing is triggered automatically")
    details: dict[str, Any]
    status_note: str | None
    status_updated_at: datetime | None
    superseded_by_id: int | None
    lineage: dict[str, Any] | None
    merged_incident_ids: list[int]
    timeline: list[TimelineEntry]
    timeline_total: int
    low_source_confidence_threshold: float = Field(description="Sensor confidence below this is treated as uncertain evidence")
    quality_issues: list[IncidentQualityIssue] = Field(description="Phase 3 findings on the incident's events (traceability)")


class IncidentStatusUpdate(BaseModel):
    status: IncidentStatus
    note: str | None = Field(None, max_length=2000)


class CorrelationRunOut(BaseModel):
    id: int
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: float | None
    observations_loaded: int
    incidents_active: int
    created: int
    updated: int
    unchanged: int
    merged: int
    obsoleted: int
    details: dict[str, Any] | None
    error: str | None


class IncidentsSummary(BaseModel):
    total: int
    by_category: list[FacetValue]
    by_severity: list[FacetValue]
    by_status: list[FacetValue]
    by_confidence: list[FacetValue]
    inactive: int = Field(description="Merged or obsolete incidents kept for history")
    unresolved: int = Field(description="Active incidents whose status is open, acknowledged or investigating")
    unresolved_by_severity: list[FacetValue]
    last_run: CorrelationRunOut | None
