"""Detector output vs the generator's ground truth (precision / recall floors).

The floors are set below the values measured on this seed so the test guards
against regressions without claiming perfection; docs/data-quality.md reports
the measured numbers for the 50K datasets.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, update

from app.core.database import SessionLocal, engine, init_db
from app.main import app
from app.models import Event, IngestBatch, QualityIssue, QualityIssueEvidence, QualityIssueFacet, QualityRun, RawSubmission
from datagen.generator import Config, generate
from datagen.quality_eval import evaluate
from tests.test_quality_rules import analyze

# issue type -> (min precision, min recall); None = not asserted
FLOORS = {
    "duplicate_submission:exact": (1.0, 1.0),
    "duplicate_submission:conflicting": (1.0, 1.0),
    "invalid_record": (1.0, 1.0),
    "delayed_event": (0.9, 1.0),
    "out_of_order_arrival": (None, 1.0),
    "timestamp_conflict": (1.0, 1.0),
    "location_conflict": (1.0, 0.9),
    "conflicting_sensor_results": (0.95, 0.9),
    "device_offline": (1.0, 1.0),
    "device_degraded": (1.0, 1.0),
    "sensor_failure": (1.0, 1.0),
    "device_recovered": (1.0, 1.0),
    "low_confidence_detection": (0.7, 1.0),
    "uncorrelatable_event": (None, 0.8),
    # Spikes also (correctly) fire on bursts labelled as other anomalies (store-and-forward uploads,
    # access-denial bursts, multi-sensor captures), so label precision is not meaningful; see below.
    "event_spike": (None, 0.6),
}


@pytest.fixture(scope="module")
def report():
    dataset = generate(Config(count=6000, seed=7))
    truth = dataset.ground_truth("f")
    with TestClient(app) as client:
        init_db()  # module-scoped: start from empty tables (the per-test cleanup runs after this fixture)
        with SessionLocal() as s:
            for model in (QualityIssueEvidence, QualityIssueFacet, QualityIssue, QualityRun):
                s.execute(delete(model))
            s.execute(update(RawSubmission).values(normalized_event_id=None))
            s.execute(delete(Event))
            s.execute(delete(RawSubmission))
            s.execute(delete(IngestBatch))
            s.commit()
        body = client.post("/ingest", content=("\n".join(dataset.lines()) + "\n").encode(), headers={"Content-Type": "application/x-ndjson"}).json()
        analyze(client)
    con = sqlite3.connect(engine.url.database)
    try:
        return evaluate(con, truth, body["batch_id"])
    finally:
        con.close()


@pytest.mark.parametrize("key", list(FLOORS))
def test_precision_and_recall_floors(report, key):
    result = report["by_type"][key]
    min_precision, min_recall = FLOORS[key]
    if result["ground_truth_findings"] == 0 and result["issues"] == 0:
        pytest.skip("no instances in this sample")
    if min_precision is not None and result["precision"] is not None:
        assert result["precision"] >= min_precision, result
    if min_recall is not None and result["recall"] is not None:
        assert result["recall"] >= min_recall, result


def test_spikes_rarely_fire_on_unlabelled_background(report):
    spikes = report["by_type"]["event_spike"]
    assert spikes["fp_unexplained"] <= max(2, 0.15 * spikes["issues"]), spikes


def test_no_unexplained_false_positives_for_conflict_rules(report):
    for key in ("timestamp_conflict", "location_conflict", "conflicting_sensor_results", "out_of_order_arrival", "delayed_event"):
        assert report["by_type"][key]["fp_unexplained"] == 0, (key, report["by_type"][key])
