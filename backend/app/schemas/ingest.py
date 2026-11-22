from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.events import ValidationIssue


class RecordReport(BaseModel):
    position: int = Field(description="1-based position in the request (line number for JSONL)")
    submission_id: int = Field(description="Raw submission id (evidence reference, GET /submissions/{id})")
    event_id: str | None = Field(description="External event_id, when it could be extracted")
    status: str = Field(description="accepted | invalid | duplicate")
    duplicate_kind: str | None = Field(description="exact | conflicting (duplicates only)")
    conflicting_fields: list[str] | None
    normalized_event_id: int | None = Field(description="Event created (accepted) or repeated (duplicate)")
    errors: list[ValidationIssue]
    warnings: list[ValidationIssue]


class IngestSummary(BaseModel):
    batch_id: str
    status: str
    channel: str
    filename: str | None
    total_received: int
    accepted: int
    invalid: int
    duplicate_event_ids: int = Field(
        description="Submissions whose event_id was already ingested (total = accepted + invalid + duplicates)"
    )
    exact_duplicates: int
    conflicting_duplicates: int
    with_warnings: int
    duration_ms: float
    records: list[RecordReport] = Field(
        description="Every record when total_received <= limit, otherwise only invalid/duplicate records"
    )
    records_truncated: bool
    quality_analysis: str = Field(
        "disabled",
        description="'scheduled': data-quality analysis runs in the background (see GET /quality-issues/summary)",
    )


class BatchOut(BaseModel):
    id: str
    channel: str
    filename: str | None
    status: str
    created_at: datetime
    completed_at: datetime | None
    total_received: int
    accepted: int
    invalid: int
    duplicates: int
    exact_duplicates: int
    conflicting_duplicates: int
    with_warnings: int
