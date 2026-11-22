"""GET /dashboard/summary: compact operations overview built from the briefing engine."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.briefing import BriefingPeriod, Finding, Grounding, Narrative, OverallConfidence, PrioritizedIncident
from app.schemas.events import Severity


class CountItem(BaseModel):
    value: str
    count: int


class QualityIssueRef(BaseModel):
    id: int
    issue_type: str
    severity: Severity
    title: str
    occurred_from: datetime
    occurred_to: datetime
    event_count: int


class BriefingPreview(BaseModel):
    executive_summary: Narrative
    recommendations: list[Finding] = Field(description="Top recommended management actions (advisory)")
    recommendations_total: int
    confidence: OverallConfidence


class DashboardSummary(BaseModel):
    period: BriefingPeriod
    generated_at: datetime
    filters: dict[str, Any]
    scope: dict[str, str] = Field(description="What each group of numbers covers (period vs all stored data, time basis)")
    period_metrics: dict[str, Any] = Field(description="Exact counts for the period (events by occurrence time; incidents overlapping)")
    stored_totals: dict[str, Any] = Field(description="All stored data regardless of period; incident status as of now")
    priority_incidents: list[PrioritizedIncident]
    events_by_source: list[CountItem]
    events_per_hour: list[dict[str, Any]]
    spikes: list[Finding] = Field(description="Spikes reported by the data-quality engine in the period")
    quality_by_type: list[CountItem]
    quality_high_severity: list[QualityIssueRef]
    quality_high_severity_total: int
    uncertainty: dict[str, int]
    device_health: list[Finding]
    device_health_note: str
    briefing: BriefingPreview
    grounding: Grounding
