"""Correlation engine: candidate selection -> evidence-gated clustering -> explanations -> sync.

Each run is a deterministic full rebuild over the stored events (bounded columns,
in-memory hash/time indexes; no pairwise scans), followed by an identity-preserving
sync (see repository.py). Runs are serialized by a process-wide lock so two runs
never overwrite each other. In the normal workflow the background analysis
pipeline (quality analysis, then correlation) runs after every ingest request;
``POST /incidents/correlate`` runs it on demand.
"""

from __future__ import annotations

import logging
import resource
import sys
import threading
import time
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.incidents import CorrelationRun, Incident
from app.models.ingestion import RawSubmission, SubmissionStatus
from app.models.quality import QualityIssue, QualityIssueEvidence
from app.services.correlation.candidate_selection import load_candidates
from app.services.correlation.clustering import Correlator
from app.services.correlation.configuration import CorrelationSettings, get_correlation_settings
from app.services.correlation.explanations import QualityNote, build_incident
from app.services.correlation.repository import sync_incidents

logger = logging.getLogger("zenith.correlation")
_lock = threading.Lock()

NOTE_TYPES = ("conflicting_sensor_results", "location_conflict", "timestamp_conflict", "delayed_event", "out_of_order_arrival",
              "uncorrelatable_event", "low_confidence_detection")


def _quality_notes(db: Session, pks: set[int]) -> dict[int, list[QualityNote]]:
    notes: dict[int, list[QualityNote]] = defaultdict(list)
    if not pks:
        return notes
    rows = db.execute(
        select(QualityIssueEvidence.event_pk, QualityIssue.id, QualityIssue.issue_type, QualityIssue.subtype, QualityIssue.title)
        .join(QualityIssue, QualityIssue.id == QualityIssueEvidence.issue_id)
        .where(QualityIssueEvidence.event_pk.is_not(None), QualityIssue.issue_type.in_(NOTE_TYPES))
        .order_by(QualityIssue.id)
    )
    for pk, qid, itype, subtype, title in rows:
        if pk in pks:
            notes[pk].append(QualityNote(qid, itype, subtype, title))
    return notes


def _duplicates(db: Session) -> dict[int, int]:
    return dict(db.execute(
        select(RawSubmission.normalized_event_id, func.count())
        .where(RawSubmission.status == SubmissionStatus.DUPLICATE)
        .group_by(RawSubmission.normalized_event_id)
    ).all())


def _peak_rss_mb() -> float:
    scale = 1 if sys.platform == "darwin" else 1024
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale / 1024**2, 1)


def run_correlation(db: Session, trigger: str = "manual", settings: CorrelationSettings | None = None) -> CorrelationRun:
    settings = settings or get_correlation_settings()
    with _lock:
        started = time.perf_counter()
        run = CorrelationRun(trigger=trigger, status="running", started_at=datetime.now(UTC))
        db.add(run)
        db.commit()
        run_id = run.id
        try:
            timings: dict[str, float] = {}
            t = time.perf_counter()
            candidates = load_candidates(db)
            timings["candidate_selection_ms"] = round((time.perf_counter() - t) * 1000, 1)
            t = time.perf_counter()
            correlator = Correlator(db, candidates, settings)
            groups = correlator.run()
            timings["clustering_ms"] = round((time.perf_counter() - t) * 1000, 1)
            t = time.perf_counter()
            pks = {pk for g in groups for pk in g.members}
            notes, duplicates = _quality_notes(db, pks), _duplicates(db)
            drafts = [build_incident(g, notes, duplicates, settings) for g in groups]
            timings["explanations_ms"] = round((time.perf_counter() - t) * 1000, 1)
            t = time.perf_counter()
            now = datetime.now(UTC)
            result = sync_incidents(db, drafts, run_id, now)
            db.flush()  # sessions do not autoflush; counts below must see this run's changes
            timings["sync_ms"] = round((time.perf_counter() - t) * 1000, 1)
            run = db.get(CorrelationRun, run_id)
            run.status = "completed"
            run.finished_at = datetime.now(UTC)
            run.duration_ms = round((time.perf_counter() - started) * 1000, 1)
            run.observations_loaded = candidates.count
            run.incidents_active = db.scalar(select(func.count(Incident.id)).where(Incident.correlation_state == "active")) or 0
            run.created, run.updated, run.unchanged = result.created, result.updated, result.unchanged
            run.merged, run.obsoleted = result.merged, result.obsoleted
            run.details = {"timings": timings, "stats": dict(sorted(correlator.stats.items())), "process_peak_rss_mb": _peak_rss_mb()}
            db.commit()
            return run
        except Exception as exc:
            db.rollback()
            failed = db.get(CorrelationRun, run_id)
            if failed is not None:
                failed.status, failed.finished_at = "failed", datetime.now(UTC)
                failed.duration_ms = round((time.perf_counter() - started) * 1000, 1)
                failed.error = f"{type(exc).__name__}: {exc}"[:2000]
                db.commit()
            raise
