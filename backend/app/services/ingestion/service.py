"""Batch ingestion: persists prepared records in bounded, transactional chunks.

Per chunk (default 1,000 records) a single transaction writes the raw submissions,
the new normalized events and the links between them, so the two layers never
disagree. Uniqueness contract: the first *valid* submission of an external
``event_id`` creates the normalized event; any later valid submission with that
``event_id`` is stored as a ``duplicate`` (``exact`` if all normalized fields are
identical, otherwise ``conflicting`` with the differing field names) and never
modifies the normalized event. Invalid submissions never claim an ``event_id``.
"""

import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.ingestion import DuplicateKind, Event, IngestBatch, RawSubmission, SubmissionStatus
from app.services.ingestion.records import COMPARED_FIELDS, PreparedRecord, diff_fields, issue

_LOOKUP_CHUNK = 900  # stay well below SQLite's bound-parameter limit


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_batch_id() -> str:
    return f"batch-{utcnow():%Y%m%dT%H%M%S}-{secrets.token_hex(3)}"


@dataclass
class RecordResult:
    position: int
    submission_id: int
    event_id: str | None
    status: str
    duplicate_kind: str | None = None
    conflicting_fields: list[str] | None = None
    normalized_event_id: int | None = None
    errors: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)


@dataclass
class _Decision:
    status: str
    duplicate_kind: str | None = None
    conflicting_fields: list[str] | None = None
    target_event_pk: int | None = None  # existing event (duplicates of already-stored events)
    target_chunk_index: int | None = None  # in-chunk winner (duplicates within this chunk)


class BatchIngestor:
    """Accumulates prepared records and persists them chunk by chunk.

    Usage: ``add()`` records, call ``flush()`` whenever ``add`` returns True, then
    ``finish()``. The async API runs ``flush``/``finish`` in a worker thread.
    """

    def __init__(self, db: Session, channel: str, filename: str | None = None, settings: Settings | None = None):
        self.db = db
        self.settings = settings or get_settings()
        self.started = time.perf_counter()
        self.batch = IngestBatch(
            id=new_batch_id(), channel=channel, filename=filename, status="processing", created_at=utcnow()
        )
        db.add(self.batch)
        db.commit()
        self.counts = {"total": 0, "accepted": 0, "invalid": 0, "duplicate": 0, "exact": 0, "conflicting": 0, "warn": 0}
        self.reports: list[RecordResult] = []
        self.reports_truncated = False
        self._pending: list[PreparedRecord] = []

    # ------------------------------------------------------------------ public API

    def add(self, record: PreparedRecord) -> bool:
        self._pending.append(record)
        return len(self._pending) >= self.settings.ingest_chunk_size

    def flush(self) -> None:
        if not self._pending:
            return
        chunk, self._pending = self._pending, []
        for attempt in range(2):
            try:
                results = self._persist(chunk)
                self.db.commit()
                break
            except IntegrityError:
                # A concurrent batch claimed one of these event_ids between our lookup and
                # insert. Roll back the whole chunk and re-run it once against fresh state.
                self.db.rollback()
                if attempt == 1:
                    raise
        for result in results:
            self._record(result)

    def finish(self, status: str = "completed") -> dict[str, Any]:
        self.flush()
        batch = self.batch
        batch.status = status
        batch.completed_at = utcnow()
        batch.total_received = self.counts["total"]
        batch.accepted = self.counts["accepted"]
        batch.invalid = self.counts["invalid"]
        batch.duplicates = self.counts["duplicate"]
        batch.exact_duplicates = self.counts["exact"]
        batch.conflicting_duplicates = self.counts["conflicting"]
        batch.with_warnings = self.counts["warn"]
        self.db.commit()
        return self.summary()

    def fail(self) -> None:
        """Mark the batch as failed (already committed chunks stay, as evidence)."""
        self.db.rollback()
        self._pending = []
        self.finish(status="failed")

    def summary(self) -> dict[str, Any]:
        c = self.counts
        return {
            "batch_id": self.batch.id,
            "status": self.batch.status,
            "channel": self.batch.channel,
            "filename": self.batch.filename,
            "total_received": c["total"],
            "accepted": c["accepted"],
            "invalid": c["invalid"],
            "duplicate_event_ids": c["duplicate"],
            "exact_duplicates": c["exact"],
            "conflicting_duplicates": c["conflicting"],
            "with_warnings": c["warn"],
            "duration_ms": round((time.perf_counter() - self.started) * 1000, 1),
            "records": [r.__dict__ for r in self.reports],
            "records_truncated": self.reports_truncated,
        }

    # ------------------------------------------------------------------ internals

    def _record(self, result: RecordResult) -> None:
        c = self.counts
        c["total"] += 1
        c[result.status] += 1
        if result.duplicate_kind:
            c[result.duplicate_kind] += 1
        if result.warnings:
            c["warn"] += 1

        limit = self.settings.max_record_reports
        if len(self.reports) < limit and not self.reports_truncated:
            self.reports.append(result)
            return
        if not self.reports_truncated:
            # Over the inline limit: keep only problem records from now on.
            self.reports_truncated = True
            self.reports = [r for r in self.reports if r.status != SubmissionStatus.ACCEPTED]
        if result.status != SubmissionStatus.ACCEPTED and len(self.reports) < limit:
            self.reports.append(result)

    def _existing_events(self, event_ids: list[str]) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        columns = [Event.id, Event.content_sha256, *(getattr(Event, name) for name in COMPARED_FIELDS)]
        for start in range(0, len(event_ids), _LOOKUP_CHUNK):
            part = event_ids[start : start + _LOOKUP_CHUNK]
            for row in self.db.execute(select(*columns).where(Event.event_id.in_(part))).mappings():
                found[row["event_id"]] = dict(row)
        return found

    def _persist(self, chunk: list[PreparedRecord]) -> list[RecordResult]:
        now = utcnow()
        valid_ids = sorted({r.event["event_id"] for r in chunk if r.event})
        existing = self._existing_events(valid_ids) if valid_ids else {}

        decisions: list[_Decision] = []
        winners: dict[str, int] = {}
        for index, record in enumerate(chunk):
            if record.event is None:
                decisions.append(_Decision(SubmissionStatus.INVALID))
                continue
            eid = record.event["event_id"]
            if eid in existing:
                target = existing[eid]
                same = target["content_sha256"] == record.normalized_sha256
                decisions.append(
                    _Decision(
                        SubmissionStatus.DUPLICATE,
                        DuplicateKind.EXACT if same else DuplicateKind.CONFLICTING,
                        None if same else diff_fields(target, record.event),
                        target_event_pk=target["id"],
                    )
                )
            elif eid in winners:
                winner = chunk[winners[eid]]
                same = winner.normalized_sha256 == record.normalized_sha256
                decisions.append(
                    _Decision(
                        SubmissionStatus.DUPLICATE,
                        DuplicateKind.EXACT if same else DuplicateKind.CONFLICTING,
                        None if same else diff_fields(winner.event, record.event),
                        target_chunk_index=winners[eid],
                    )
                )
            else:
                winners[eid] = index
                decisions.append(_Decision(SubmissionStatus.ACCEPTED))

        raw_rows = []
        for record, decision in zip(chunk, decisions, strict=True):
            warnings = list(record.warnings)
            if decision.status == SubmissionStatus.DUPLICATE:
                detail = (
                    "identical to the stored event"
                    if decision.duplicate_kind == DuplicateKind.EXACT
                    else f"differs from the stored event in: {', '.join(decision.conflicting_fields or [])}"
                )
                warnings.append(issue("event_id", f"duplicate_{decision.duplicate_kind}", f"event_id already ingested; {detail}"))
            raw_rows.append(
                {
                    "batch_id": self.batch.id,
                    "position": record.position,
                    "external_event_id": record.external_event_id,
                    "raw_content": record.raw_content,
                    "raw_size_bytes": record.raw_size_bytes,
                    "raw_truncated": record.raw_truncated,
                    "content_sha256": record.content_sha256,
                    "ingested_at": now,
                    "status": decision.status,
                    "duplicate_kind": decision.duplicate_kind,
                    "conflicting_fields": decision.conflicting_fields,
                    "errors": record.errors or None,
                    "warnings": warnings or None,
                    "normalized_event_id": decision.target_event_pk,
                }
            )
        submission_ids = list(
            self.db.scalars(insert(RawSubmission).returning(RawSubmission.id, sort_by_parameter_order=True), raw_rows)
        )

        accepted_indexes = [i for i, d in enumerate(decisions) if d.status == SubmissionStatus.ACCEPTED]
        event_pk_by_index: dict[int, int] = {}
        if accepted_indexes:
            event_rows = [
                {
                    **chunk[i].event,
                    "ingested_at": now,
                    "content_sha256": chunk[i].normalized_sha256,
                    "submission_id": submission_ids[i],
                }
                for i in accepted_indexes
            ]
            new_pks = list(self.db.scalars(insert(Event).returning(Event.id, sort_by_parameter_order=True), event_rows))
            event_pk_by_index = dict(zip(accepted_indexes, new_pks, strict=True))

        links = []
        for i, decision in enumerate(decisions):
            if decision.status == SubmissionStatus.ACCEPTED:
                links.append({"id": submission_ids[i], "normalized_event_id": event_pk_by_index[i]})
            elif decision.target_chunk_index is not None:
                decision.target_event_pk = event_pk_by_index[decision.target_chunk_index]
                links.append({"id": submission_ids[i], "normalized_event_id": decision.target_event_pk})
        if links:
            self.db.execute(update(RawSubmission), links)

        results = []
        for i, (record, decision) in enumerate(zip(chunk, decisions, strict=True)):
            results.append(
                RecordResult(
                    position=record.position,
                    submission_id=submission_ids[i],
                    event_id=record.external_event_id,
                    status=decision.status,
                    duplicate_kind=decision.duplicate_kind,
                    conflicting_fields=decision.conflicting_fields,
                    normalized_event_id=event_pk_by_index.get(i, decision.target_event_pk),
                    errors=record.errors,
                    warnings=raw_rows[i]["warnings"] or [],
                )
            )
        return results


def record_rejected_request(
    db: Session, channel: str, filename: str | None, body: bytes, code: str, message: str, max_bytes: int
) -> tuple[str, int]:
    """Preserve a request that could not be parsed at all (e.g. malformed JSON body)."""
    import hashlib

    now = utcnow()
    batch = IngestBatch(
        id=new_batch_id(),
        channel=channel,
        filename=filename,
        status="rejected",
        created_at=now,
        completed_at=now,
        total_received=1,
        invalid=1,
    )
    db.add(batch)
    db.flush()
    submission = RawSubmission(
        batch_id=batch.id,
        position=1,
        external_event_id=None,
        raw_content=body[:max_bytes].decode("utf-8", errors="replace"),
        raw_size_bytes=len(body),
        raw_truncated=len(body) > max_bytes,
        content_sha256=hashlib.sha256(body).hexdigest(),
        ingested_at=now,
        status=SubmissionStatus.INVALID,
        errors=[issue("$", code, message)],
    )
    db.add(submission)
    db.commit()
    return batch.id, submission.id
