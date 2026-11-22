"""GET /incidents, GET /incidents/{id} (+ summary, status update, correlation trigger)."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.events import MultiValue, PageNumber, PageSize, _invalid_param, _split
from app.core.database import get_db
from app.core.errors import AppError
from app.models.incidents import INCIDENT_STATUSES, Incident
from app.schemas.events import Page, Severity, parse_utc_timestamp
from app.schemas.incidents import (
    INCIDENT_CATEGORIES,
    CorrelationRunOut,
    IncidentDetail,
    IncidentQualityIssue,
    IncidentStatusUpdate,
    IncidentsSummary,
    IncidentSummary,
    TimelineEntry,
)
from app.schemas.sources import resolve_source_type
from app.services.correlation import repository
from app.services.correlation.configuration import get_correlation_settings
from app.services.correlation.engine import run_correlation
from app.services.correlation.repository import IncidentFilters
from app.services.correlation.scoring import LINK_TYPES
from app.services.ingestion.queries import page_meta

router = APIRouter(tags=["incidents"])

IncidentSort = Literal["priority", "-severity", "-last_occurred_at", "first_occurred_at", "-confidence", "-id"]
TIMELINE_LIMIT = 2000


def _summary(i: Incident, facets: dict[str, list[str]]) -> IncidentSummary:
    return IncidentSummary(
        id=i.id, title=i.title, category=i.category, severity=i.severity, confidence=i.confidence, confidence_level=i.confidence_level,
        status=i.status, correlation_state=i.correlation_state, first_occurred_at=i.first_occurred_at, last_occurred_at=i.last_occurred_at,
        detected_at=i.detected_at, updated_at=i.updated_at, event_count=i.event_count, zones=facets["zone"], devices=facets["device"],
        sources=facets["source"], summary=i.summary, conflict_count=len(i.conflicts or []), rule_version=i.rule_version,
    )


def _validate(values: list[str] | None, allowed: tuple[str, ...] | set[str], name: str) -> list[str] | None:
    if values and (bad := [v for v in values if v not in allowed]):
        raise _invalid_param(name, f"unknown value(s) {bad}; allowed: {sorted(allowed)}")
    return values


@router.get("/incidents", response_model=Page[IncidentSummary])
def list_incidents(
    db: Annotated[Session, Depends(get_db)],
    page: PageNumber = 1,
    page_size: PageSize = 50,
    severity: MultiValue = None,
    status_: Annotated[list[str] | None, Query(alias="status", description="Operator status; repeat or comma-separate")] = None,
    category: MultiValue = None,
    zone: MultiValue = None,
    device_id: MultiValue = None,
    source_type: MultiValue = None,
    event_id: Annotated[str | None, Query(description="Incidents whose evidence includes this external event_id")] = None,
    event: Annotated[int | None, Query(description="Incidents whose evidence includes this internal event id")] = None,
    occurred_from: Annotated[str | None, Query(description="Incidents active at or after this time (ISO-8601 with offset)")] = None,
    occurred_to: Annotated[str | None, Query(description="Incidents that started before this time (exclusive)")] = None,
    include_inactive: Annotated[bool, Query(description="Also return merged and obsolete incidents")] = False,
    sort: Annotated[
        IncidentSort,
        Query(description="Default: most severe, then most recent; 'priority' puts unresolved incidents first; ties broken by id"),
    ] = "-severity",
) -> Page[IncidentSummary]:
    sources = _split(source_type)
    if sources:
        resolved = [resolve_source_type(s) for s in sources]
        if None in resolved:
            raise _invalid_param("source_type", f"unknown source(s) {[s for s, r in zip(sources, resolved, strict=True) if r is None]}")
        sources = [r.value for r in resolved if r]
    bounds = {}
    for name, value in (("occurred_from", occurred_from), ("occurred_to", occurred_to)):
        if value:
            try:
                bounds[name] = parse_utc_timestamp(value)
            except ValueError as exc:
                raise _invalid_param(name, str(exc)) from None
    filters = IncidentFilters(
        severities=_validate(_split(severity), {s.value for s in Severity}, "severity"),
        statuses=_validate(_split(status_), INCIDENT_STATUSES, "status"),
        categories=_validate(_split(category), INCIDENT_CATEGORIES, "category"),
        zones=_split(zone),
        devices=_split(device_id),
        sources=sources,
        event_id=event_id,
        event_pk=event,
        occurred_from=bounds.get("occurred_from"),
        occurred_to=bounds.get("occurred_to"),
        states=["active", "merged", "obsolete"] if include_inactive else ["active"],
    )
    rows, total, facets = repository.list_incidents(db, filters, page, page_size, sort)
    return Page[IncidentSummary](items=[_summary(r, facets[r.id]) for r in rows], **page_meta(total, page, page_size))


@router.get("/incidents/summary", response_model=IncidentsSummary)
def incidents_summary(db: Annotated[Session, Depends(get_db)]) -> IncidentsSummary:
    data = repository.summary(db)
    run = data.pop("last_run")
    return IncidentsSummary(**data, last_run=CorrelationRunOut.model_validate(run, from_attributes=True) if run else None)


@router.post("/incidents/correlate", response_model=CorrelationRunOut)
async def correlate(db: Annotated[Session, Depends(get_db)]) -> CorrelationRunOut:
    """Run the correlation engine now (it also runs after every ingest). Idempotent; preserves incident ids and statuses."""
    run = await run_in_threadpool(run_correlation, db, "manual")
    return CorrelationRunOut.model_validate(run, from_attributes=True)


def _get(db: Session, incident_id: int) -> Incident:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise AppError(f"incident {incident_id} not found", code="not_found", status_code=status.HTTP_404_NOT_FOUND)
    return incident


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
def get_incident(db: Annotated[Session, Depends(get_db)], incident_id: Annotated[int, Path(description="Incident id from GET /incidents")]) -> IncidentDetail:
    incident = _get(db, incident_id)
    facets = repository.facets_for(db, [incident.id])[incident.id]
    evidence, total = repository.evidence_for(db, incident.id, TIMELINE_LIMIT)
    threshold = get_correlation_settings().low_source_confidence
    timeline = [
        TimelineEntry(
            event_pk=e.id, event_id=e.event_id, submission_id=e.submission_id, occurred_at=e.occurred_at, received_at=e.received_at,
            source_type=e.source_type, device_id=e.device_id, event_type=e.event_type, zone=e.zone, severity=e.severity,
            source_confidence=e.confidence, role=link.role, link_type=link.link_type, core=link.is_core,
            linkage_confidence=link.linkage_confidence, linkage_explanation=link.linkage_explanation,
            link_description=LINK_TYPES[link.link_type][2] if link.link_type in LINK_TYPES else link.link_type,
            low_source_confidence=e.confidence is not None and e.confidence < threshold, conflict=link.conflict,
        )
        for link, e in evidence
    ]
    merged = list(db.scalars(select(Incident.id).where(Incident.superseded_by_id == incident.id).order_by(Incident.id)))
    return IncidentDetail(
        **_summary(incident, facets).model_dump(),
        severity_reason=incident.severity_reason,
        confidence_reason=incident.confidence_reason,
        linkage_explanation=incident.linkage_explanation,
        observations=incident.observations,
        inferences=incident.inferences,
        conflicts=incident.conflicts,
        missing_information=incident.missing_information,
        uncertainty=incident.uncertainty,
        recommended_actions=incident.recommended_actions,
        details=incident.details,
        status_note=incident.status_note,
        status_updated_at=incident.status_updated_at,
        superseded_by_id=incident.superseded_by_id,
        lineage=incident.lineage,
        merged_incident_ids=merged,
        timeline=timeline,
        timeline_total=total,
        low_source_confidence_threshold=threshold,
        quality_issues=[IncidentQualityIssue(**q) for q in repository.quality_issues_for(db, incident.id)],
    )


@router.patch("/incidents/{incident_id}", response_model=IncidentDetail)
def update_incident_status(
    db: Annotated[Session, Depends(get_db)], incident_id: int, body: IncidentStatusUpdate
) -> IncidentDetail:
    """Set the operator-maintained status (open, acknowledged, investigating, resolved, false_positive)."""
    repository.set_status(db, _get(db, incident_id), body.status, body.note, datetime.now(UTC))
    return get_incident(db, incident_id)
