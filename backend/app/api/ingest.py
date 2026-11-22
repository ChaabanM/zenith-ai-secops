"""POST /ingest: single event, JSON array, {"events": [...]} wrapper, or streamed JSONL."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import AppError
from app.schemas.ingest import IngestSummary
from app.services.ingestion.jsonl import aiter_lines
from app.services.ingestion.records import loads_strict, prepare_line, prepare_object
from app.services.ingestion.service import BatchIngestor, record_rejected_request
from app.services.quality.configuration import get_quality_settings
from app.services.quality.engine import scheduler

router = APIRouter(tags=["ingestion"])

JSONL_CONTENT_TYPES = {
    "application/x-ndjson",
    "application/ndjson",
    "application/jsonl",
    "application/x-jsonl",
    "application/json-lines",
    "application/x-jsonlines",
}

INGEST_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {"description": "Processed; no new events (all valid records were duplicates)"},
    201: {"description": "Processed; at least one new event was stored (may include invalid records)"},
    400: {"description": "Malformed request: body is not valid JSON, empty, or wrong batch shape"},
    413: {"description": "JSON body exceeds the configured limit (use JSONL streaming)"},
    415: {"description": "Unsupported content type (multipart uploads are not accepted)"},
    422: {"description": "Every record in the request was invalid; details carry the full summary"},
}


@router.post("/ingest", response_model=IngestSummary, status_code=201, responses=INGEST_RESPONSES)
async def ingest(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    format: Annotated[
        Literal["json", "jsonl"] | None, Query(description="Override content-type detection")
    ] = None,
    filename: Annotated[str | None, Query(max_length=255, description="Recorded on the batch")] = None,
) -> JSONResponse:
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type.startswith("multipart/"):
        raise AppError(
            "multipart uploads are not supported; send the file body as application/json or application/x-ndjson",
            code="unsupported_media_type",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )
    use_jsonl = format == "jsonl" or (format is None and content_type in JSONL_CONTENT_TYPES)
    auto = get_quality_settings().auto_analyze
    try:
        if use_jsonl:
            summary = await _ingest_jsonl(request, db, filename)
        else:
            summary = await _ingest_json(request, db, filename)
    except AppError as exc:
        if auto and isinstance(exc.details, dict) and "batch_id" in exc.details:
            scheduler.request()  # rejected requests are preserved as invalid submissions, which are quality issues too
        raise
    summary["quality_analysis"] = "scheduled" if auto else "disabled"
    if auto:
        scheduler.request()
    return _respond(summary)


def _respond(summary: dict[str, Any]) -> JSONResponse:
    body = IngestSummary.model_validate(summary).model_dump(mode="json")
    if summary["accepted"] == 0 and summary["duplicate_event_ids"] == 0:
        raise AppError(
            "no valid records in request",
            code="validation_error",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            details=body,
        )
    code = status.HTTP_201_CREATED if summary["accepted"] > 0 else status.HTTP_200_OK
    return JSONResponse(status_code=code, content=body)


async def _read_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise _too_large(limit)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise _too_large(limit)
    return bytes(body)


def _too_large(limit: int) -> AppError:
    return AppError(
        f"JSON body exceeds {limit} bytes; stream large datasets as JSONL (Content-Type: application/x-ndjson)",
        code="payload_too_large",
        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    )


async def _reject(db: Session, channel: str, filename: str | None, body: bytes, code: str, message: str) -> AppError:
    settings = get_settings()
    batch_id, submission_id = await run_in_threadpool(
        record_rejected_request, db, channel, filename, body, code, message, settings.max_record_bytes
    )
    return AppError(
        message,
        code=code,
        status_code=status.HTTP_400_BAD_REQUEST,
        details={"batch_id": batch_id, "submission_id": submission_id},
    )


async def _ingest_json(request: Request, db: Session, filename: str | None) -> dict[str, Any]:
    settings = get_settings()
    body = await _read_body(request, settings.max_json_body_bytes)
    if not body.strip():
        raise AppError("request body is empty", code="empty_body", status_code=status.HTTP_400_BAD_REQUEST)
    try:
        data = await run_in_threadpool(loads_strict, body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise await _reject(db, "json", filename, body, "malformed_json", f"request body is not valid JSON: {exc}") from None

    if isinstance(data, dict) and "events" in data and "event_id" not in data:
        records = data["events"]
        if not isinstance(records, list):
            raise await _reject(db, "json", filename, body, "invalid_batch_format", "'events' must be a JSON array")
    elif isinstance(data, list):
        records = data
    else:
        records = [data]  # single event (or a non-object value, reported as an invalid record)
    if not records:
        raise AppError("batch contains no records", code="empty_batch", status_code=status.HTTP_400_BAD_REQUEST)

    def run() -> dict[str, Any]:
        ingestor = BatchIngestor(db, "json", filename, settings)
        try:
            for position, obj in enumerate(records, start=1):
                if ingestor.add(prepare_object(position, obj, settings.max_record_bytes)):
                    ingestor.flush()
            return ingestor.finish()
        except Exception:
            ingestor.fail()
            raise

    return await run_in_threadpool(run)


async def _ingest_jsonl(request: Request, db: Session, filename: str | None) -> dict[str, Any]:
    settings = get_settings()
    ingestor = await run_in_threadpool(BatchIngestor, db, "jsonl", filename, settings)
    try:
        async for line in aiter_lines(request.stream(), settings.max_record_bytes):
            if line.is_blank:
                continue
            record = prepare_line(line.number, line.data, line.size, line.sha256, settings.max_record_bytes)
            if ingestor.add(record):
                await run_in_threadpool(ingestor.flush)
        summary = await run_in_threadpool(ingestor.finish)
    except Exception:
        await run_in_threadpool(ingestor.fail)
        raise
    if summary["total_received"] == 0:
        raise AppError(
            "JSONL body contains no records",
            code="empty_body",
            status_code=status.HTTP_400_BAD_REQUEST,
            details={"batch_id": summary["batch_id"]},
        )
    return summary
