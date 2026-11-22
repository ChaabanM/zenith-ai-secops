"""Quality engine: runs every rule over the stored data and syncs the findings.

Strategy: each run is a deterministic full analysis of the current database
(streamed, index-backed queries; about 5-10 s for 50K events), and the results are
synced idempotently by fingerprint. Cross-event rules (spikes, ordering, conflicts)
need the full context anyway, and a full run keeps the stored issues exactly
consistent with the evidence, including findings that disappear when new data
changes a baseline.

Workflow integration: ``QualityScheduler.request()`` is called after every ingest
request. It runs the analysis on a background thread and coalesces bursts of
requests into one follow-up run, so ingestion latency is unaffected and the
issues become queryable shortly after the data. ``POST /quality-issues/analyze``
runs it synchronously on demand.
"""

from __future__ import annotations

import logging
import resource
import sys
import threading
import time
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ingestion import Event, RawSubmission
from app.models.quality import QualityRun
from app.services.quality.configuration import QualitySettings, get_quality_settings
from app.services.quality.repository import sync_issues
from app.services.quality.rules import ALL_RULES, QualityContext, Rule
from app.services.quality.schemas import IssueDraft

logger = logging.getLogger("zenith.quality")
_run_lock = threading.Lock()  # one analysis at a time (manual and background runs serialize)


def _peak_rss_mb() -> float:
    scale = 1 if sys.platform == "darwin" else 1024
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale / 1024**2, 1)


def run_analysis(db: Session, trigger: str = "manual", settings: QualitySettings | None = None,
                 rules: tuple[type[Rule], ...] = ALL_RULES) -> QualityRun:
    settings = settings or get_quality_settings()
    with _run_lock:
        started = time.perf_counter()
        run = QualityRun(trigger=trigger, status="running", started_at=datetime.now(UTC))
        db.add(run)
        db.commit()
        run_id = run.id
        try:
            ctx = QualityContext(db=db, settings=settings)
            drafts: list[IssueDraft] = []
            per_rule: dict[str, dict[str, float]] = {}
            for rule_cls in rules:
                t = time.perf_counter()
                produced = list(rule_cls().detect(ctx))
                drafts += produced
                per_rule[rule_cls.rule_id] = {"issues": len(produced), "ms": round((time.perf_counter() - t) * 1000, 1)}
            now = datetime.now(UTC)
            result = sync_issues(db, drafts, run_id, now)
            run = db.get(QualityRun, run_id)
            run.status = "completed"
            run.finished_at = datetime.now(UTC)
            run.duration_ms = round((time.perf_counter() - started) * 1000, 1)
            run.events_analyzed = db.scalar(select(func.count(Event.id))) or 0
            run.submissions_analyzed = db.scalar(select(func.count(RawSubmission.id))) or 0
            run.issues_total = len({d.fingerprint for d in drafts})
            run.inserted, run.updated, run.unchanged, run.removed = result.inserted, result.updated, result.unchanged, result.removed
            run.details = {"rules": per_rule, "process_peak_rss_mb": _peak_rss_mb()}
            db.commit()
            return run
        except Exception as exc:
            db.rollback()
            failed = db.get(QualityRun, run_id)
            if failed is not None:
                failed.status = "failed"
                failed.finished_at = datetime.now(UTC)
                failed.duration_ms = round((time.perf_counter() - started) * 1000, 1)
                failed.error = f"{type(exc).__name__}: {exc}"[:2000]
                db.commit()
            raise


class QualityScheduler:
    """Runs the background analysis pipeline (quality analysis, then incident correlation) in a
    background thread; repeated requests coalesce into one follow-up run."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._dirty = False
        self._thread: threading.Thread | None = None
        self._idle = threading.Event()
        self._idle.set()

    @property
    def busy(self) -> bool:
        return not self._idle.is_set()

    def request(self, trigger: str = "ingest") -> None:
        with self._lock:
            self._dirty = True
            self._idle.clear()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, args=(trigger,), name="quality-analysis", daemon=True)
                self._thread.start()

    def wait_idle(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout)

    def _loop(self, trigger: str) -> None:
        from app.core.database import SessionLocal  # late import: avoids a cycle at module import

        while True:
            with self._lock:
                if not self._dirty:
                    self._thread = None
                    self._idle.set()
                    return
                self._dirty = False
            try:
                with SessionLocal() as db:
                    run_analysis(db, trigger)
            except Exception:  # noqa: BLE001 - logged and recorded on the run row
                logger.exception("background quality analysis failed")
            # Phase 4A: correlation runs after the quality analysis so it can annotate incidents with fresh findings
            from app.services.correlation.configuration import get_correlation_settings
            from app.services.correlation.engine import run_correlation

            if get_correlation_settings().auto_correlate:
                try:
                    with SessionLocal() as db:
                        run_correlation(db, trigger)
                except Exception:  # noqa: BLE001 - logged and recorded on the run row
                    logger.exception("background correlation failed")


scheduler = QualityScheduler()
