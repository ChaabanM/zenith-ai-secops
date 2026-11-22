"""Read-side queries for events, raw submissions and batches (bounded, index-backed)."""

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models.ingestion import Event, IngestBatch, RawSubmission

EventSort = Literal["-occurred_at", "occurred_at", "-id", "id"]


@dataclass
class EventFilters:
    severities: list[str] | None = None
    sources: list[str] | None = None
    device_ids: list[str] | None = None
    zones: list[str] | None = None
    event_types: list[str] | None = None
    event_id: str | None = None
    occurred_from: datetime | None = None  # inclusive
    occurred_to: datetime | None = None  # exclusive


def _apply_filters(stmt: Select, f: EventFilters) -> Select:
    if f.severities:
        stmt = stmt.where(Event.severity.in_(f.severities))
    if f.sources:
        stmt = stmt.where(Event.source_type.in_(f.sources))
    if f.device_ids:
        stmt = stmt.where(Event.device_id.in_(f.device_ids))
    if f.zones:
        stmt = stmt.where(Event.zone.in_(f.zones))
    if f.event_types:
        stmt = stmt.where(Event.event_type.in_(f.event_types))
    if f.event_id:
        stmt = stmt.where(Event.event_id == f.event_id)
    if f.occurred_from:
        stmt = stmt.where(Event.occurred_at >= f.occurred_from)
    if f.occurred_to:
        stmt = stmt.where(Event.occurred_at < f.occurred_to)
    return stmt


def _order(sort: EventSort) -> list[Any]:
    # id is the deterministic tie-breaker for equal timestamps.
    return {
        "-occurred_at": [Event.occurred_at.desc(), Event.id.desc()],
        "occurred_at": [Event.occurred_at.asc(), Event.id.asc()],
        "-id": [Event.id.desc()],
        "id": [Event.id.asc()],
    }[sort]


def page_meta(total: int, page: int, page_size: int) -> dict[str, int | bool]:
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": math.ceil(total / page_size) if total else 0,
        "has_next": page * page_size < total,
    }


def list_events(db: Session, f: EventFilters, page: int, page_size: int, sort: EventSort) -> tuple[list[Event], int]:
    total = db.scalar(_apply_filters(select(func.count(Event.id)), f)) or 0
    stmt = _apply_filters(select(Event), f).order_by(*_order(sort)).offset((page - 1) * page_size).limit(page_size)
    return list(db.scalars(stmt)), total


def event_facets(db: Session, limit: int = 500) -> dict[str, Any]:
    def grouped(column) -> list[dict[str, Any]]:  # noqa: ANN001
        rows = db.execute(
            select(column, func.count()).where(column.is_not(None)).group_by(column).order_by(func.count().desc(), column).limit(limit)
        )
        return [{"value": value, "count": count} for value, count in rows]

    total, first, last = db.execute(select(func.count(Event.id), func.min(Event.occurred_at), func.max(Event.occurred_at))).one()
    from app.models.types import UTCDateTime  # result of min/max bypasses the column type

    as_utc = UTCDateTime().process_result_value
    return {
        "total_events": total,
        "sources": grouped(Event.source_type),
        "severities": grouped(Event.severity),
        "zones": grouped(Event.zone),
        "devices": grouped(Event.device_id),
        "occurred_min": as_utc(_coerce_dt(first), None),
        "occurred_max": as_utc(_coerce_dt(last), None),
    }


def _coerce_dt(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def list_submissions(
    db: Session,
    page: int,
    page_size: int,
    batch_id: str | None = None,
    status: str | None = None,
    external_event_id: str | None = None,
    normalized_event_id: int | None = None,
) -> tuple[list[RawSubmission], int]:
    conditions = []
    if batch_id:
        conditions.append(RawSubmission.batch_id == batch_id)
    if status:
        conditions.append(RawSubmission.status == status)
    if external_event_id:
        conditions.append(RawSubmission.external_event_id == external_event_id)
    if normalized_event_id is not None:
        conditions.append(RawSubmission.normalized_event_id == normalized_event_id)
    total = db.scalar(select(func.count(RawSubmission.id)).where(*conditions)) or 0
    rows = db.scalars(
        select(RawSubmission).where(*conditions).order_by(RawSubmission.id).offset((page - 1) * page_size).limit(page_size)
    )
    return list(rows), total


def list_batches(db: Session, page: int, page_size: int) -> tuple[list[IngestBatch], int]:
    total = db.scalar(select(func.count(IngestBatch.id))) or 0
    rows = db.scalars(
        select(IngestBatch).order_by(IngestBatch.created_at.desc(), IngestBatch.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return list(rows), total
