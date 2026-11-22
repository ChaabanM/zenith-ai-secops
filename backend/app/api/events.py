"""Event retrieval plus raw-evidence inspection (submissions and batches)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import AppError
from app.models.ingestion import Event, IngestBatch, RawSubmission
from app.schemas.events import (
    EventDetail,
    EventFacets,
    EventQualityIssueRef,
    EventSummary,
    Page,
    Severity,
    SubmissionOut,
    parse_utc_timestamp,
)
from app.schemas.ingest import BatchOut
from app.schemas.sources import resolve_source_type
from app.services.ingestion import queries
from app.services.ingestion.queries import EventFilters, EventSort
from app.services.quality.repository import issues_for_event

router = APIRouter()

PageNumber = Annotated[int, Query(ge=1, description="1-based page number")]
PageSize = Annotated[int, Query(ge=1, le=500, description="Items per page (max 500)")]
MultiValue = Annotated[list[str] | None, Query(description="Repeat the parameter or use commas")]


def _split(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    items = [part.strip() for value in values for part in value.split(",") if part.strip()]
    return items or None


def _invalid_param(name: str, message: str) -> AppError:
    return AppError(
        f"invalid query parameter '{name}': {message}",
        code="validation_error",
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        details=[{"field": name, "message": message}],
    )


def _event_summary(event: Event) -> EventSummary:
    return EventSummary(
        id=event.id,
        event_id=event.event_id,
        source_type=event.source_type,
        device_id=event.device_id,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        received_at=event.received_at,
        ingested_at=event.ingested_at,
        delay_seconds=event.delay_seconds,
        location={"latitude": event.latitude, "longitude": event.longitude, "zone": event.zone},
        severity=event.severity,
        confidence=event.confidence,
    )


def _submission(sub: RawSubmission) -> SubmissionOut:
    return SubmissionOut(
        id=sub.id,
        batch_id=sub.batch_id,
        position=sub.position,
        external_event_id=sub.external_event_id,
        status=sub.status,
        duplicate_kind=sub.duplicate_kind,
        conflicting_fields=sub.conflicting_fields,
        errors=sub.errors or [],
        warnings=sub.warnings or [],
        normalized_event_id=sub.normalized_event_id,
        ingested_at=sub.ingested_at,
        content_sha256=sub.content_sha256,
        raw_size_bytes=sub.raw_size_bytes,
        raw_truncated=sub.raw_truncated,
        raw_content=sub.raw_content,
    )


# --------------------------------------------------------------------------- events


@router.get("/events", response_model=Page[EventSummary], tags=["events"])
def list_events(
    db: Annotated[Session, Depends(get_db)],
    page: PageNumber = 1,
    page_size: PageSize = 50,
    severity: MultiValue = None,
    source_type: MultiValue = None,
    device_id: MultiValue = None,
    zone: MultiValue = None,
    event_type: MultiValue = None,
    event_id: Annotated[str | None, Query(description="Exact external event_id")] = None,
    occurred_from: Annotated[str | None, Query(description="Inclusive ISO-8601 lower bound with offset")] = None,
    occurred_to: Annotated[str | None, Query(description="Exclusive ISO-8601 upper bound with offset")] = None,
    sort: Annotated[EventSort, Query(description="Sort order; ties broken by id")] = "-occurred_at",
) -> Page[EventSummary]:
    severities = _split(severity)
    if severities:
        allowed = {s.value for s in Severity}
        bad = [s for s in severities if s not in allowed]
        if bad:
            raise _invalid_param("severity", f"unknown value(s) {bad}; allowed: {sorted(allowed)}")
    sources = _split(source_type)
    if sources:
        resolved = [resolve_source_type(s) for s in sources]
        if None in resolved:
            bad = [s for s, r in zip(sources, resolved, strict=True) if r is None]
            raise _invalid_param("source_type", f"unknown source(s) {bad}")
        sources = [r.value for r in resolved if r]
    bounds = {}
    for name, value in (("occurred_from", occurred_from), ("occurred_to", occurred_to)):
        if value:
            try:
                bounds[name] = parse_utc_timestamp(value)
            except ValueError as exc:
                raise _invalid_param(name, str(exc)) from None

    filters = EventFilters(
        severities=severities,
        sources=sources,
        device_ids=_split(device_id),
        zones=_split(zone),
        event_types=_split(event_type),
        event_id=event_id,
        occurred_from=bounds.get("occurred_from"),
        occurred_to=bounds.get("occurred_to"),
    )
    events, total = queries.list_events(db, filters, page, page_size, sort)
    return Page[EventSummary](items=[_event_summary(e) for e in events], **queries.page_meta(total, page, page_size))


@router.get("/events/facets", response_model=EventFacets, tags=["events"])
def event_facets(db: Annotated[Session, Depends(get_db)]) -> EventFacets:
    """Distinct sources, severities, zones and devices with counts (for filter controls)."""
    return EventFacets.model_validate(queries.event_facets(db))


@router.get("/events/{event_pk}", response_model=EventDetail, tags=["events"])
def get_event(
    db: Annotated[Session, Depends(get_db)],
    event_pk: Annotated[int, Path(description="Internal event id (see `id` in GET /events)")],
) -> EventDetail:
    event = db.get(Event, event_pk)
    if event is None:
        raise AppError(f"event {event_pk} not found", code="not_found", status_code=status.HTTP_404_NOT_FOUND)
    submission = db.get(RawSubmission, event.submission_id)
    duplicates = db.scalars(
        select(RawSubmission)
        .where(RawSubmission.normalized_event_id == event.id, RawSubmission.id != event.submission_id)
        .order_by(RawSubmission.id)
        .limit(100)
    )
    return EventDetail(
        **_event_summary(event).model_dump(),
        payload=event.payload,
        content_sha256=event.content_sha256,
        submission=_submission(submission),
        duplicate_submissions=[_submission(s) for s in duplicates],
        quality_issues=[
            EventQualityIssueRef(id=i.id, issue_type=i.issue_type, severity=i.severity, title=i.title)
            for i in issues_for_event(db, event.id)
        ],
    )


# --------------------------------------------------------------------------- raw evidence


@router.get("/submissions", response_model=Page[SubmissionOut], tags=["evidence"])
def list_submissions(
    db: Annotated[Session, Depends(get_db)],
    page: PageNumber = 1,
    page_size: PageSize = 50,
    batch_id: str | None = None,
    status_filter: Annotated[str | None, Query(alias="status", pattern="^(accepted|invalid|duplicate)$")] = None,
    event_id: Annotated[str | None, Query(description="External event_id as submitted")] = None,
    normalized_event_id: int | None = None,
) -> Page[SubmissionOut]:
    """Raw submitted records (every outcome) in ingestion order."""
    rows, total = queries.list_submissions(db, page, page_size, batch_id, status_filter, event_id, normalized_event_id)
    return Page[SubmissionOut](items=[_submission(s) for s in rows], **queries.page_meta(total, page, page_size))


@router.get("/submissions/{submission_id}", response_model=SubmissionOut, tags=["evidence"])
def get_submission(db: Annotated[Session, Depends(get_db)], submission_id: int) -> SubmissionOut:
    sub = db.get(RawSubmission, submission_id)
    if sub is None:
        raise AppError(f"submission {submission_id} not found", code="not_found", status_code=status.HTTP_404_NOT_FOUND)
    return _submission(sub)


@router.get("/batches", response_model=Page[BatchOut], tags=["evidence"])
def list_batches(db: Annotated[Session, Depends(get_db)], page: PageNumber = 1, page_size: PageSize = 20) -> Page[BatchOut]:
    rows, total = queries.list_batches(db, page, page_size)
    return Page[BatchOut](items=[BatchOut.model_validate(b, from_attributes=True) for b in rows], **queries.page_meta(total, page, page_size))


@router.get("/batches/{batch_id}", response_model=BatchOut, tags=["evidence"])
def get_batch(db: Annotated[Session, Depends(get_db)], batch_id: str) -> BatchOut:
    batch = db.get(IngestBatch, batch_id)
    if batch is None:
        raise AppError(f"batch {batch_id} not found", code="not_found", status_code=status.HTTP_404_NOT_FOUND)
    return BatchOut.model_validate(batch, from_attributes=True)
