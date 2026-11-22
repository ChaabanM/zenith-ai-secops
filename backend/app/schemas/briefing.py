"""Structured reasoning and briefing output.

Every finding says what kind of statement it is (``basis``) and carries the stored
records that support it, so a reader can always get from a sentence back to the
original events, incidents and quality findings.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.events import Severity

FindingBasis = Literal["database_fact", "observed", "inference", "conflict", "missing", "recommendation"]
ConfidenceLevel = Literal["high", "medium", "low"]


class EvidenceRef(BaseModel):
    event_pk: int = Field(description="Internal event id (GET /events/{id})")
    event_id: str = Field(description="Original external event_id, exactly as submitted")


class FindingConfidence(BaseModel):
    level: ConfidenceLevel
    score: float | None = Field(None, description="Heuristic score in [0, 1] where one exists; never a calibrated probability")
    basis: str = Field(description="What the confidence rests on")


class Finding(BaseModel):
    id: str = Field(description="Stable identifier within this briefing, e.g. 'device_health-2'")
    category: str
    basis: FindingBasis = Field(
        description="database_fact: exact count/aggregate of stored records; observed: what sensors or operators reported "
        "(not verified); inference: drawn by the system; conflict: contradictory records; missing: evidence gap; "
        "recommendation: advisory action"
    )
    statement: str
    reasoning: str = Field(description="Why the system makes this statement")
    confidence: FindingConfidence
    uncertainty: list[str] = []
    missing_information: list[str] = []
    evidence: list[EvidenceRef] = Field([], description="Sample of supporting events (most relevant first)")
    evidence_total: int = Field(0, description="Number of supporting events in total (may exceed the listed sample)")
    event_query: dict[str, str] | None = Field(None, description="GET /events filters that list every supporting event")
    incident_ids: list[int] = []
    quality_issue_ids: list[int] = []
    recommended_actions: list[str] = []
    metrics: dict[str, Any] = {}


class PriorityFactor(BaseModel):
    factor: str
    points: float
    explanation: str


class PrioritizedIncident(BaseModel):
    incident_id: int
    title: str
    category: str
    severity: Severity = Field(description="Stored incident severity; never changed by prioritization")
    status: str
    confidence: float
    confidence_level: ConfidenceLevel
    first_occurred_at: datetime
    last_occurred_at: datetime
    detected_at: datetime
    event_count: int
    zones: list[str]
    devices: list[str]
    sources: list[str]
    conflict_count: int
    priority_score: float = Field(description="Transparent additive score (see factors); ranking aid, not a probability")
    priority_rank: int
    immediate_attention: bool
    factors: list[PriorityFactor]
    reason: str
    new_in_period: bool = Field(description="First event of the incident occurred inside the period")
    detected_after_period: bool = Field(description="The engine first produced the incident after the period ended (late data)")
    evidence: list[EvidenceRef] = Field(description="Strongly linked source events (first 5 by occurrence time)")
    recommended_actions: list[str]


class BriefingSection(BaseModel):
    key: str
    title: str
    summary: str
    findings: list[Finding]
    empty_reason: str | None = Field(None, description="Why the section has no findings (never filler)")


class BriefingPeriod(BaseModel):
    mode: Literal["shift", "day", "custom"]
    label: str
    start: datetime = Field(description="Inclusive UTC start")
    end: datetime = Field(description="Exclusive UTC end")
    timezone: str
    local_start: str = Field(description="Start in the display time zone (ISO-8601 with offset)")
    local_end: str
    time_basis: str
    baseline: str = Field(description="How comparison windows were chosen")
    default_reason: str | None = Field(None, description="Set when the period was chosen automatically")
    query: dict[str, str] = Field({}, description="GET /briefing parameters that reproduce exactly this period")


class Narrative(BaseModel):
    text: str
    generated_by: Literal["deterministic_template", "local_language_model"]
    provider: str
    validated: bool = Field(description="Model output passed the grounding check (always true for the template)")
    fallback_reason: str | None = Field(None, description="Why model output was not used, if a model was configured")


class ConfidenceFactor(BaseModel):
    factor: str
    effect: float
    explanation: str


class OverallConfidence(BaseModel):
    level: ConfidenceLevel
    score: float = Field(description="Heuristic data-reliability score in [0, 1]; not a probability")
    factors: list[ConfidenceFactor]
    limitations: list[str]


class Grounding(BaseModel):
    events_referenced: int
    incidents_referenced: int
    quality_issues_referenced: int
    all_references_verified: bool
    unverified_references: list[str]


class Briefing(BaseModel):
    period: BriefingPeriod
    generated_at: datetime
    filters: dict[str, Any]
    executive_summary: Narrative
    key_metrics: dict[str, Any]
    priority_incidents: list[PrioritizedIncident] = Field(description="Highest-priority unresolved incidents")
    unresolved_incidents: list[PrioritizedIncident]
    unresolved_total: int
    new_incidents: list[PrioritizedIncident]
    new_total: int
    sections: list[BriefingSection]
    recommended_actions: list[Finding]
    confidence: OverallConfidence
    evidence_index: list[EvidenceRef] = Field(description="Every source event referenced anywhere in the briefing")
    incident_ids: list[int]
    quality_issue_ids: list[int]
    grounding: Grounding


class IncidentReasoning(BaseModel):
    incident_id: int
    reference_time: datetime = Field(description="Recency is measured against this time (latest stored event occurrence)")
    priority: PrioritizedIncident
    findings: list[Finding]
