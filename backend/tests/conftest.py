import os
import tempfile
from pathlib import Path

# Point the app at an isolated SQLite file before any app module is imported.
_tmp_dir = tempfile.mkdtemp(prefix="zenith-test-")
os.environ["ZENITH_DATABASE_URL"] = f"sqlite:///{Path(_tmp_dir) / 'test.db'}"
os.environ["ZENITH_ENVIRONMENT"] = "test"
os.environ["ZENITH_QUALITY_AUTO_ANALYZE"] = "false"  # tests run the analysis explicitly (one test enables it)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


# --------------------------------------------------------------------------- Phase 2 helpers

import copy  # noqa: E402
from typing import Any  # noqa: E402

from sqlalchemy import delete, update  # noqa: E402

from app.core.database import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    CorrelationRun,
    Event,
    Incident,
    IncidentEvidence,
    IncidentFacet,
    IngestBatch,
    QualityIssue,
    QualityIssueEvidence,
    QualityIssueFacet,
    QualityRun,
    RawSubmission,
)

BASE_EVENT: dict[str, Any] = {
    "event_id": "evt-test-0001",
    "source_type": "radar",
    "device_id": "radar-07",
    "event_type": "speed_violation",
    "occurred_at": "2026-01-15T10:12:04Z",
    "received_at": "2026-01-15T10:12:18Z",
    "location": {"latitude": 25.2048, "longitude": 55.2708, "zone": "Gate-A"},
    "severity": "high",
    "confidence": 0.94,
    "payload": {"speed_kph": 112, "speed_limit_kph": 80, "track_id": "T12345"},
}


def make_event(**overrides: Any) -> dict[str, Any]:
    """A valid event; keyword overrides replace top-level fields (None removes the field)."""
    event = copy.deepcopy(BASE_EVENT)
    for key, value in overrides.items():
        if value is None:
            event.pop(key, None)
        else:
            event[key] = value
    return event


@pytest.fixture(autouse=True)
def clean_tables() -> None:
    """Every test starts with empty ingestion tables (the schema itself is kept)."""
    init_db()
    with SessionLocal() as session:
        for model in (IncidentEvidence, IncidentFacet, CorrelationRun):
            session.execute(delete(model))
        session.execute(update(Incident).values(superseded_by_id=None))
        session.execute(delete(Incident))
        for model in (QualityIssueEvidence, QualityIssueFacet, QualityIssue, QualityRun):
            session.execute(delete(model))
        session.execute(update(RawSubmission).values(normalized_event_id=None))
        session.execute(delete(Event))
        session.execute(delete(RawSubmission))
        session.execute(delete(IngestBatch))
        session.commit()
