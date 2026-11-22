"""Event input schema (validation) and API output schemas.

Mandatory fields: event_id, source_type, device_id, event_type, occurred_at.
Optional fields:  received_at, location, severity, confidence, payload.

A missing optional field is stored as null (never invented) and reported as a
validation *warning*; a missing or malformed mandatory field makes the record
*invalid*. See docs/event-schema.md.
"""

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Generic, TypeVar

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictStr,
    StringConstraints,
    model_validator,
)

from app.schemas.sources import SourceType, resolve_source_type


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFORMATIONAL = "informational"


SEVERITY_ALIASES = {"info": Severity.INFORMATIONAL}

# --------------------------------------------------------------------------- validators

_TIMESTAMP_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def parse_utc_timestamp(value: Any) -> datetime:
    """Parse an ISO-8601 string with an explicit offset and convert it to UTC.

    Rejected: non-strings (including epoch numbers), date-only values, unparseable
    strings and timestamps without a timezone offset (ambiguous local time).
    """
    if not isinstance(value, str):
        raise ValueError("must be an ISO-8601 timestamp string")
    text = value.strip()
    if not _TIMESTAMP_SHAPE.match(text):
        raise ValueError("must be an ISO-8601 timestamp such as 2026-01-15T10:12:04Z")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid timestamp: {exc}") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone offset (e.g. 'Z' or '+04:00')")
    return parsed.astimezone(UTC)


def _parse_source(value: Any) -> SourceType:
    if not isinstance(value, str):
        raise ValueError("must be a string")
    source = resolve_source_type(value)
    if source is None:
        allowed = ", ".join(s.value for s in SourceType)
        raise ValueError(f"unknown source_type '{value}'. Allowed: {allowed} (or a documented alias)")
    return source


def _parse_severity(value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("must be a string")
    key = value.strip().lower()
    return SEVERITY_ALIASES.get(key, key)


Identifier = Annotated[
    StrictStr,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128, pattern=r"^\S+$"),
]
Label = Annotated[StrictStr, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
UtcTimestamp = Annotated[datetime, BeforeValidator(parse_utc_timestamp)]
Probability = Annotated[float, Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)]
Latitude = Annotated[float, Field(strict=True, ge=-90.0, le=90.0, allow_inf_nan=False)]
Longitude = Annotated[float, Field(strict=True, ge=-180.0, le=180.0, allow_inf_nan=False)]

# --------------------------------------------------------------------------- input model


class LocationIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    latitude: Latitude | None = None
    longitude: Longitude | None = None
    zone: Label | None = None

    @model_validator(mode="after")
    def coordinates_paired(self) -> "LocationIn":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self


class EventIn(BaseModel):
    """A single submitted event. Unknown top-level fields are ignored (kept in raw evidence)."""

    model_config = ConfigDict(extra="ignore")

    event_id: Identifier
    source_type: Annotated[SourceType, BeforeValidator(_parse_source)]
    device_id: Identifier
    event_type: Label
    occurred_at: UtcTimestamp
    received_at: UtcTimestamp | None = None
    location: LocationIn | None = None
    severity: Annotated[Severity | None, BeforeValidator(_parse_severity)] = None
    confidence: Probability | None = None
    payload: dict[str, Any] | None = None


KNOWN_FIELDS = frozenset(EventIn.model_fields)

# --------------------------------------------------------------------------- output models


class LocationOut(BaseModel):
    latitude: float | None
    longitude: float | None
    zone: str | None


class EventSummary(BaseModel):
    id: int
    event_id: str
    source_type: SourceType
    device_id: str
    event_type: str
    occurred_at: datetime
    received_at: datetime | None
    ingested_at: datetime
    delay_seconds: float | None
    location: LocationOut
    severity: Severity | None
    confidence: float | None


class ValidationIssue(BaseModel):
    field: str
    code: str
    message: str


class SubmissionOut(BaseModel):
    id: int
    batch_id: str
    position: int
    external_event_id: str | None
    status: str
    duplicate_kind: str | None
    conflicting_fields: list[str] | None
    errors: list[ValidationIssue]
    warnings: list[ValidationIssue]
    normalized_event_id: int | None
    ingested_at: datetime
    content_sha256: str
    raw_size_bytes: int
    raw_truncated: bool
    raw_content: str


class EventQualityIssueRef(BaseModel):
    id: int
    issue_type: str
    severity: Severity
    title: str


class EventDetail(EventSummary):
    payload: dict[str, Any]
    content_sha256: str
    submission: SubmissionOut
    duplicate_submissions: list[SubmissionOut]
    quality_issues: list[EventQualityIssueRef] = []


T = TypeVar("T")


class Page(BaseModel, Generic[T]):  # noqa: UP046 - keep Python 3.11 compatible
    items: list[T]
    page: int
    page_size: int
    total: int
    total_pages: int
    has_next: bool


class FacetValue(BaseModel):
    value: str
    count: int


class EventFacets(BaseModel):
    total_events: int
    sources: list[FacetValue]
    severities: list[FacetValue]
    zones: list[FacetValue]
    devices: list[FacetValue]
    occurred_min: datetime | None
    occurred_max: datetime | None
