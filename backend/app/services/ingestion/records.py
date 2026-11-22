"""Turn one submitted record (a parsed JSON value or a raw JSONL line) into a
``PreparedRecord``: preserved evidence + validation outcome + normalized values.

No database access happens here, so this module is pure and easy to test.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from app.schemas.events import KNOWN_FIELDS, EventIn

# Fields compared to decide whether a repeated event_id is an exact or conflicting duplicate.
COMPARED_FIELDS = (
    "event_id",
    "source_type",
    "device_id",
    "event_type",
    "occurred_at",
    "received_at",
    "latitude",
    "longitude",
    "zone",
    "severity",
    "confidence",
    "payload",
)

_EVENT_ID_IN_TEXT = re.compile(r'"event_id"\s*:\s*"([^"\\]{1,128})"')


def issue(field_name: str, code: str, message: str) -> dict[str, str]:
    return {"field": field_name, "code": code, "message": message}


@dataclass
class PreparedRecord:
    position: int
    raw_content: str
    raw_size_bytes: int
    raw_truncated: bool
    content_sha256: str
    external_event_id: str | None
    errors: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    event: dict[str, Any] | None = None  # normalized column values when valid
    normalized_sha256: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.event is not None


# --------------------------------------------------------------------------- helpers


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def comparable(event: dict[str, Any]) -> dict[str, Any]:
    """Normalized fields in a JSON-friendly form, used for hashing and diffs."""
    out = {name: event.get(name) for name in COMPARED_FIELDS}
    out["occurred_at"] = _iso(event.get("occurred_at"))
    out["received_at"] = _iso(event.get("received_at"))
    return out


def normalized_hash(event: dict[str, Any]) -> str:
    canonical = json.dumps(comparable(event), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def diff_fields(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    ca, cb = comparable(a), comparable(b)
    return [name for name in COMPARED_FIELDS if ca[name] != cb[name]]


def _reject_constant(token: str) -> Any:
    raise ValueError(f"non-standard JSON constant {token}")


def loads_strict(text: str | bytes) -> Any:
    """json.loads that rejects NaN/Infinity (not valid JSON)."""
    return json.loads(text, parse_constant=_reject_constant)


def _external_id(obj: Any) -> str | None:
    if isinstance(obj, dict):
        value = obj.get("event_id")
        if isinstance(value, str) and value.strip():
            return value.strip()[:256]
    return None


def _format_loc(loc: tuple[Any, ...]) -> str:
    return ".".join(str(part) for part in loc) if loc else "$"


# --------------------------------------------------------------------------- validation


def validate_event(obj: Any) -> tuple[dict[str, Any] | None, list[dict[str, str]], list[dict[str, str]]]:
    """Validate a parsed JSON value. Returns (normalized_event | None, errors, warnings)."""
    if not isinstance(obj, dict):
        return None, [issue("$", "not_an_object", f"record must be a JSON object, got {type(obj).__name__}")], []

    try:
        model = EventIn.model_validate(obj)
    except ValidationError as exc:
        errors = []
        for err in exc.errors(include_url=False):
            message = err["msg"].removeprefix("Value error, ")
            errors.append(issue(_format_loc(err["loc"]), err["type"], message))
        return None, errors, []

    warnings: list[dict[str, str]] = []
    submitted_source = obj["source_type"].strip()
    if submitted_source != model.source_type.value:
        warnings.append(
            issue("source_type", "source_alias_normalized", f"'{submitted_source}' normalized to '{model.source_type.value}'")
        )
    unknown = sorted(set(obj) - KNOWN_FIELDS)
    if unknown:
        warnings.append(issue("$", "unknown_fields_ignored", f"not part of the schema, kept in raw evidence only: {', '.join(unknown)}"))

    delay_seconds = None
    if model.received_at is None:
        warnings.append(issue("received_at", "missing_received_at", "received_at not provided; arrival delay unknown"))
    else:
        delay_seconds = (model.received_at - model.occurred_at).total_seconds()
        if delay_seconds < 0:
            warnings.append(
                issue(
                    "received_at",
                    "received_before_occurred",
                    f"received_at is {abs(delay_seconds):.0f}s earlier than occurred_at (possible clock skew)",
                )
            )

    location = model.location
    if location is None:
        warnings.append(issue("location", "missing_location", "no location provided"))
    else:
        if location.latitude is None:
            warnings.append(issue("location", "missing_coordinates", "no latitude/longitude provided"))
        if location.zone is None:
            warnings.append(issue("location.zone", "missing_zone", "no zone provided"))
    if model.severity is None:
        warnings.append(issue("severity", "missing_severity", "severity not provided; treated as unknown"))
    if model.confidence is None:
        warnings.append(issue("confidence", "missing_confidence", "confidence not provided; treated as unknown"))

    event = {
        "event_id": model.event_id,
        "source_type": model.source_type.value,
        "device_id": model.device_id,
        "event_type": model.event_type,
        "occurred_at": model.occurred_at,
        "received_at": model.received_at,
        "delay_seconds": delay_seconds,
        "latitude": location.latitude if location else None,
        "longitude": location.longitude if location else None,
        "zone": location.zone if location else None,
        "severity": model.severity.value if model.severity else None,
        "confidence": model.confidence,
        "payload": model.payload if model.payload is not None else {},
    }
    return event, [], warnings


# --------------------------------------------------------------------------- record builders


def _evidence(raw: bytes, max_bytes: int) -> tuple[str, bool]:
    if len(raw) <= max_bytes:
        return raw.decode("utf-8", errors="replace"), False
    return raw[:max_bytes].decode("utf-8", errors="ignore"), True


def prepare_object(position: int, obj: Any, max_bytes: int) -> PreparedRecord:
    """Prepare an element of a JSON request (array item, wrapper item or single object).

    Evidence is the element's compact JSON serialization (key order preserved).
    """
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _prepare(position, obj, raw, len(raw), hashlib.sha256(raw).hexdigest(), max_bytes)


def prepare_line(position: int, data: bytes, size: int, sha256: str, max_bytes: int) -> PreparedRecord:
    """Prepare one JSONL line. ``data`` may be a prefix when the line exceeded the limit."""
    if size > max_bytes:
        text, _ = _evidence(data, max_bytes)
        match = _EVENT_ID_IN_TEXT.search(text)
        return PreparedRecord(
            position=position,
            raw_content=text,
            raw_size_bytes=size,
            raw_truncated=True,
            content_sha256=sha256,
            external_event_id=match.group(1) if match else None,
            errors=[issue("$", "record_too_large", f"record is {size} bytes; limit is {max_bytes}")],
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return PreparedRecord(
            position=position,
            raw_content=data.decode("utf-8", errors="replace"),
            raw_size_bytes=size,
            raw_truncated=False,
            content_sha256=sha256,
            external_event_id=None,
            errors=[issue("$", "invalid_encoding", f"line is not valid UTF-8: {exc.reason}")],
        )
    try:
        obj = loads_strict(text)
    except ValueError as exc:
        match = _EVENT_ID_IN_TEXT.search(text)
        return PreparedRecord(
            position=position,
            raw_content=text,
            raw_size_bytes=size,
            raw_truncated=False,
            content_sha256=sha256,
            external_event_id=match.group(1) if match else None,
            errors=[issue("$", "malformed_json", f"line is not valid JSON: {exc}")],
        )
    return _prepare(position, obj, data, size, sha256, max_bytes)


def _prepare(position: int, obj: Any, raw: bytes, size: int, sha256: str, max_bytes: int) -> PreparedRecord:
    text, truncated = _evidence(raw, max_bytes)
    record = PreparedRecord(
        position=position,
        raw_content=text,
        raw_size_bytes=size,
        raw_truncated=truncated,
        content_sha256=sha256,
        external_event_id=_external_id(obj),
    )
    if truncated:
        record.errors = [issue("$", "record_too_large", f"record is {size} bytes; limit is {max_bytes}")]
        return record
    event, errors, warnings = validate_event(obj)
    record.errors, record.warnings = errors, warnings
    if event is not None:
        record.event = event
        record.normalized_sha256 = normalized_hash(event)
    return record
