"""A. duplicate submissions and B. malformed / incomplete records.

Both rules read the Phase 2 raw-submission layer; nothing is re-validated from
scratch except to describe *what* differs in a conflicting resubmission.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import aliased

from app.models.ingestion import Event, RawSubmission, SubmissionStatus
from app.schemas.events import KNOWN_FIELDS
from app.services.ingestion.records import COMPARED_FIELDS, comparable, loads_strict, validate_event
from app.services.quality.rules.base import QualityContext, Rule, fmt_time
from app.services.quality.schemas import Evidence, IssueDraft

MANDATORY = ("event_id", "source_type", "device_id", "event_type", "occurred_at")


def _short(value: Any, limit: int = 80) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[: limit - 1] + "…"


class DuplicateSubmissionRule(Rule):
    """A. Exact duplicate submissions and repeated event IDs with conflicting content."""

    rule_id = "DUP-001"
    issue_type = "duplicate_submission"
    summary = "A later submission reused an event_id that was already ingested."

    def detect(self, ctx: QualityContext) -> Iterator[IssueDraft]:
        original = aliased(RawSubmission)
        stmt = (
            select(
                RawSubmission.id.label("dup_id"),
                RawSubmission.batch_id.label("dup_batch"),
                RawSubmission.position.label("dup_position"),
                RawSubmission.duplicate_kind,
                RawSubmission.conflicting_fields,
                RawSubmission.raw_content,
                RawSubmission.ingested_at.label("dup_ingested"),
                Event,
                original.batch_id.label("orig_batch"),
                original.position.label("orig_position"),
                original.ingested_at.label("orig_ingested"),
            )
            .join(Event, Event.id == RawSubmission.normalized_event_id)
            .join(original, original.id == Event.submission_id)
            .where(RawSubmission.status == SubmissionStatus.DUPLICATE)
            .order_by(RawSubmission.id)
        )
        for row in ctx.stream(stmt):
            event: Event = row.Event
            exact = row.duplicate_kind == "exact"
            evidence = [
                Evidence("stored_event", event.id, None, event.event_id),
                Evidence("original_submission", None, event.submission_id, event.event_id),
                Evidence("duplicate_submission", None, row.dup_id, event.event_id),
            ]
            where = f"Submission #{row.dup_id} (batch {row.dup_batch}, record {row.dup_position})"
            first = f"submission #{event.submission_id} (batch {row.orig_batch}, record {row.orig_position})"
            details: dict[str, Any] = {
                "event_id": event.event_id,
                "original_submission_id": event.submission_id,
                "duplicate_submission_id": row.dup_id,
                "duplicate_kind": row.duplicate_kind,
            }
            if exact:
                title = f"Exact duplicate submission of {event.event_id}"
                reason = (
                    f"{where} carries event_id {event.event_id}, which was first ingested from {first}. "
                    "Every normalized field is identical, so this is a retransmission of the same record."
                )
                description = "The same event was submitted more than once. The copy is kept as evidence; the stored event is unchanged."
                severity, uncertainty, subtype = "low", None, "exact"
            else:
                differences = self._differences(event, row.raw_content, row.conflicting_fields or [])
                details["conflicting_fields"] = row.conflicting_fields or []
                details["differences"] = differences
                fields = row.conflicting_fields or []
                changed = ", ".join(fields) or "content"
                title = f"Conflicting resubmission of {event.event_id} ({changed} {'differs' if len(fields) <= 1 else 'differ'})"
                diff_text = "; ".join(f"{d['field']}: stored {_short(d['stored'])} vs resubmitted {_short(d['resubmitted'])}" for d in differences[:6])
                reason = (
                    f"{where} reuses event_id {event.event_id} from {first} but reports different values "
                    f"({diff_text or changed}). The first valid submission is kept as the normalized event; the "
                    "later version is preserved but never applied."
                )
                description = "Two submissions claim to be the same event but disagree. Treat the affected fields as unreliable until the source confirms which version is correct."
                severity, subtype = "medium", "conflicting"
                uncertainty = "The data does not show which version is correct; the stored values are simply the first ones received."
            draft = self.draft(
                str(row.dup_id),
                subtype=subtype,
                severity=severity,
                title=title,
                description=description,
                detection_reason=reason,
                occurred_from=event.occurred_at,
                occurred_to=event.occurred_at,
                confidence=1.0,
                uncertainty=uncertainty,
                details=details,
                evidence=evidence,
            )
            draft.devices.add(event.device_id)
            draft.sources.add(event.source_type)
            if event.zone:
                draft.zones.add(event.zone)
            yield draft

    @staticmethod
    def _differences(event: Event, raw: str, fields: list[str]) -> list[dict[str, Any]]:
        stored = comparable({name: getattr(event, name) for name in COMPARED_FIELDS})
        try:
            resubmitted_event, _, _ = validate_event(loads_strict(raw))
        except ValueError:
            resubmitted_event = None
        if resubmitted_event is None:
            return [{"field": f, "stored": stored.get(f), "resubmitted": None} for f in fields]
        resubmitted = comparable(resubmitted_event)
        out = []
        for name in fields:
            a, b = stored.get(name), resubmitted.get(name)
            if name == "payload" and isinstance(a, dict) and isinstance(b, dict):
                for key in sorted(set(a) | set(b)):
                    if a.get(key) != b.get(key):
                        out.append({"field": f"payload.{key}", "stored": a.get(key), "resubmitted": b.get(key)})
            else:
                out.append({"field": name, "stored": a, "resubmitted": b})
        return out


# ------------------------------------------------------------------------------------- B

CATEGORY_LABELS = {
    "malformed_json": "Malformed JSON / unparseable record",
    "not_an_object": "Record is not a JSON object",
    "oversized_record": "Record exceeds the size limit",
    "missing_mandatory_field": "Missing mandatory field",
    "invalid_timestamp": "Invalid timestamp",
    "invalid_coordinates": "Invalid coordinates",
    "invalid_confidence": "Invalid confidence value",
    "incorrect_type": "Incorrect field type",
    "unknown_source_type": "Unknown source type",
    "schema_violation": "Schema violation",
}
CATEGORY_PRIORITY = list(CATEGORY_LABELS)


def classify_error(error: dict[str, str]) -> str:
    field, code = error.get("field", ""), error.get("code", "")
    root = field.split(".")[0]
    if code in ("malformed_json", "invalid_encoding"):
        return "malformed_json"
    if code in ("not_an_object", "invalid_batch_format"):
        return "not_an_object"
    if code == "record_too_large":
        return "oversized_record"
    if code == "missing" and root in MANDATORY:
        return "missing_mandatory_field"
    if root in ("occurred_at", "received_at"):
        return "invalid_timestamp"
    if root == "location":
        return "invalid_coordinates"
    if root == "confidence":
        return "invalid_confidence"
    if root == "source_type":
        return "unknown_source_type"
    if code.endswith("_type") or code in ("string_type", "dict_type", "float_type", "int_type", "bool_type"):
        return "incorrect_type"
    return "schema_violation"


def _submitted_value(obj: Any, key: str) -> str | None:
    if isinstance(obj, dict):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:128]
    return None


class InvalidRecordRule(Rule):
    """B. Malformed and incomplete records rejected during ingestion."""

    rule_id = "VAL-001"
    issue_type = "invalid_record"
    summary = "A submitted record failed validation and was not stored as an event."

    def detect(self, ctx: QualityContext) -> Iterator[IssueDraft]:
        stmt = (
            select(
                RawSubmission.id,
                RawSubmission.batch_id,
                RawSubmission.position,
                RawSubmission.external_event_id,
                RawSubmission.errors,
                RawSubmission.raw_content,
                RawSubmission.raw_truncated,
                RawSubmission.ingested_at,
            )
            .where(RawSubmission.status == SubmissionStatus.INVALID)
            .order_by(RawSubmission.id)
        )
        for row in ctx.stream(stmt):
            errors = row.errors or []
            categories = sorted({classify_error(e) for e in errors}, key=CATEGORY_PRIORITY.index) or ["schema_violation"]
            primary = categories[0]
            missing = sorted({e["field"] for e in errors if classify_error(e) == "missing_mandatory_field"})
            try:
                obj = None if row.raw_truncated else loads_strict(row.raw_content)
            except ValueError:
                obj = None
            ident = row.external_event_id or "a record without a usable event_id"
            error_text = "; ".join(f"{e.get('field')}: {e.get('message')}" for e in errors[:5])
            title = f"{CATEGORY_LABELS[primary]}: {ident}" if row.external_event_id else f"{CATEGORY_LABELS[primary]} (batch record {row.position})"
            reason = (
                f"Submission #{row.id} (batch {row.batch_id}, record {row.position}) was rejected by schema validation: {error_text}. "
                "The original content is preserved as evidence; no normalized event was created."
            )
            if isinstance(obj, dict):
                unknown_keys = sorted(set(obj) - KNOWN_FIELDS)
            else:
                unknown_keys = []
            draft = self.draft(
                str(row.id),
                subtype=primary,
                severity="medium",
                title=title,
                description="The observation in this record is unusable until the producer re-sends a valid version.",
                detection_reason=reason,
                occurred_from=row.ingested_at,
                occurred_to=row.ingested_at,
                time_basis="ingested_at",
                confidence=1.0,
                missing_evidence=(f"Mandatory field(s) not provided: {', '.join(missing)}" if missing else None),
                details={
                    "categories": categories,
                    "errors": errors,
                    "submitted_event_id": row.external_event_id,
                    "batch_id": row.batch_id,
                    "position": row.position,
                    "ingested_at": fmt_time(row.ingested_at),
                    "unknown_fields": unknown_keys,
                    "note": "device, zone and source facets are taken from the submitted (unvalidated) content",
                },
                evidence=[Evidence("invalid_submission", None, row.id, row.external_event_id)],
            )
            for key, target in (("device_id", draft.devices), ("source_type", draft.sources)):
                value = _submitted_value(obj, key)
                if value:
                    target.add(value)
            location = obj.get("location") if isinstance(obj, dict) else None
            zone = _submitted_value(location, "zone")
            if zone:
                draft.zones.add(zone)
            yield draft
