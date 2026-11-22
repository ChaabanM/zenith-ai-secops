"""GET /quality-issues (+ summary, detail) and POST /quality-issues/analyze."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.api.events import MultiValue, PageNumber, PageSize, _invalid_param, _split
from app.core.database import get_db
from app.core.errors import AppError
from app.models.quality import QualityIssue
from app.schemas.events import Page, Severity, parse_utc_timestamp
from app.schemas.quality import (
    ISSUE_TYPES,
    EvidenceEvent,
    EvidenceItem,
    EvidenceSubmission,
    QualityIssueDetail,
    QualityIssueSummary,
    QualityRunOut,
    QualitySummary,
)
from app.schemas.sources import resolve_source_type
from app.services.ingestion.queries import page_meta
from app.services.quality import repository
from app.services.quality.engine import run_analysis, scheduler
from app.services.quality.repository import IssueFilters

router = APIRouter(tags=["quality"])

IssueSort = Literal["-severity", "-occurred_at", "occurred_at", "-id"]
EVIDENCE_LIMIT = 500


def _summary(issue: QualityIssue, facets: dict[str, list[str]]) -> QualityIssueSummary:
    return QualityIssueSummary(
        id=issue.id,
        issue_type=issue.issue_type,
        subtype=issue.subtype,
        severity=issue.severity,
        title=issue.title,
        description=issue.description,
        rule_id=issue.rule_id,
        rule_version=issue.rule_version,
        confidence=issue.confidence,
        occurred_from=issue.occurred_from,
        occurred_to=issue.occurred_to,
        time_basis=issue.time_basis,
        event_count=issue.event_count,
        submission_count=issue.submission_count,
        devices=facets["device"],
        zones=facets["zone"],
        sources=facets["source"],
        status=issue.status,
        first_detected_at=issue.first_detected_at,
        last_detected_at=issue.last_detected_at,
    )


@router.get("/quality-issues", response_model=Page[QualityIssueSummary])
def list_quality_issues(
    db: Annotated[Session, Depends(get_db)],
    page: PageNumber = 1,
    page_size: PageSize = 50,
    issue_type: MultiValue = None,
    severity: MultiValue = None,
    rule_id: MultiValue = None,
    source_type: MultiValue = None,
    device_id: MultiValue = None,
    zone: MultiValue = None,
    event_id: Annotated[str | None, Query(description="Issues whose evidence includes this external event_id")] = None,
    event: Annotated[int | None, Query(description="Issues whose evidence includes this internal event id")] = None,
    occurred_from: Annotated[str | None, Query(description="Issues whose time span ends at or after this (ISO-8601 with offset)")] = None,
    occurred_to: Annotated[str | None, Query(description="Issues whose time span starts before this (exclusive)")] = None,
    sort: Annotated[IssueSort, Query(description="Default: most severe first, then most recent; ties broken by id")] = "-severity",
) -> Page[QualityIssueSummary]:
    types = _split(issue_type)
    if types and (bad := [t for t in types if t not in ISSUE_TYPES]):
        raise _invalid_param("issue_type", f"unknown value(s) {bad}; allowed: {list(ISSUE_TYPES)}")
    severities = _split(severity)
    if severities and (bad := [s for s in severities if s not in {v.value for v in Severity}]):
        raise _invalid_param("severity", f"unknown value(s) {bad}")
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
    filters = IssueFilters(
        issue_types=types,
        severities=severities,
        rule_ids=_split(rule_id),
        sources=sources,
        devices=_split(device_id),
        zones=_split(zone),
        event_id=event_id,
        event_pk=event,
        occurred_from=bounds.get("occurred_from"),
        occurred_to=bounds.get("occurred_to"),
    )
    rows, total, facets = repository.list_issues(db, filters, page, page_size, sort)
    return Page[QualityIssueSummary](items=[_summary(r, facets[r.id]) for r in rows], **page_meta(total, page, page_size))


@router.get("/quality-issues/summary", response_model=QualitySummary)
def quality_summary(db: Annotated[Session, Depends(get_db)]) -> QualitySummary:
    """Issue counts by type, severity, rule, device, zone and source, plus the latest analysis run."""
    data = repository.summary(db)
    run = data.pop("last_run")
    return QualitySummary(
        **data,
        last_run=QualityRunOut.model_validate(run, from_attributes=True) if run else None,
        analysis_pending=scheduler.busy,
    )


@router.post("/quality-issues/analyze", response_model=QualityRunOut)
async def analyze(db: Annotated[Session, Depends(get_db)]) -> QualityRunOut:
    """Run the full analysis now (synchronously) and return the run statistics. Idempotent."""
    run = await run_in_threadpool(run_analysis, db, "manual")
    return QualityRunOut.model_validate(run, from_attributes=True)


@router.get("/quality-issues/{issue_id}", response_model=QualityIssueDetail)
def get_quality_issue(
    db: Annotated[Session, Depends(get_db)],
    issue_id: Annotated[int, Path(description="Issue id from GET /quality-issues")],
) -> QualityIssueDetail:
    issue = db.get(QualityIssue, issue_id)
    if issue is None:
        raise AppError(f"quality issue {issue_id} not found", code="not_found", status_code=status.HTTP_404_NOT_FOUND)
    facets = repository.facets_for(db, [issue.id])[issue.id]
    evidence, total = repository.evidence_for(db, issue.id, EVIDENCE_LIMIT)
    items = []
    for item in evidence:
        link, event, sub = item["link"], item["event"], item["submission"]
        items.append(
            EvidenceItem(
                role=link.role,
                external_event_id=link.external_event_id,
                event=EvidenceEvent.model_validate(event, from_attributes=True) if event else None,
                submission=EvidenceSubmission(
                    id=sub.id, batch_id=sub.batch_id, position=sub.position, status=sub.status, duplicate_kind=sub.duplicate_kind,
                    external_event_id=sub.external_event_id, errors=sub.errors or [], ingested_at=sub.ingested_at,
                ) if sub else None,
            )
        )
    return QualityIssueDetail(
        **_summary(issue, facets).model_dump(),
        detection_reason=issue.detection_reason,
        uncertainty=issue.uncertainty,
        missing_evidence=issue.missing_evidence,
        details=issue.details,
        evidence=items,
        evidence_total=total,
        evidence_truncated=total > len(items),
    )
