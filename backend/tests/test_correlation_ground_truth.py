"""Incident correlation vs the generator's ground truth (related and unrelated groups).

Floors sit below the values measured on this sample so the test guards against
regressions without claiming perfection; docs/correlation.md reports the 50K numbers.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, update

from app.core.database import SessionLocal, engine, init_db
from app.main import app
from app.models import (
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
from datagen.correlation_eval import evaluate
from datagen.generator import Config, generate


@pytest.fixture(scope="module")
def report():
    dataset = generate(Config(count=8000, seed=21))
    truth = dataset.ground_truth("f")
    with TestClient(app) as client:
        init_db()
        with SessionLocal() as s:
            for model in (IncidentEvidence, IncidentFacet, CorrelationRun, QualityIssueEvidence, QualityIssueFacet, QualityIssue, QualityRun):
                s.execute(delete(model))
            s.execute(update(Incident).values(superseded_by_id=None))
            s.execute(delete(Incident))
            s.execute(update(RawSubmission).values(normalized_event_id=None))
            s.execute(delete(Event))
            s.execute(delete(RawSubmission))
            s.execute(delete(IngestBatch))
            s.commit()
        client.post("/ingest", content=("\n".join(dataset.lines()) + "\n").encode(), headers={"Content-Type": "application/x-ndjson"})
        client.post("/quality-issues/analyze")
        assert client.post("/incidents/correlate").status_code == 200
    con = sqlite3.connect(engine.url.database)
    try:
        return evaluate(con, truth)
    finally:
        con.close()


def test_pairwise_precision_and_recall(report):
    p = report["pairwise"]
    assert p["positive_pairs"] > 100
    assert p["precision"] >= 0.97, p
    assert p["recall"] >= 0.95, p


def test_no_labelled_false_merges(report):
    # unrelated bystanders, separate vehicles, scenario K groups and ambiguous L events are never merged
    assert report["pairwise"]["false_merges_labelled"] == 0, report["pairwise"]


def test_related_scenarios_are_grouped(report):
    assert report["event_grouping"]["mean_share_in_main_incident"] >= 0.95
    for key in ("A:speeding", "A:red_light", "C:fence_cut", "C:beam_break", "C:fence_climb", "E:conflicting_timestamps",
                "E:conflicting_locations", "E:conflicting_sensor_results", "E:contradictory_vehicle_details"):
        cov = report["coverage"].get(key)
        if cov:
            assert cov.get("no_incident", 0) == 0 and cov.get("fragmented", 0) == 0, (key, cov)


def test_unrelated_and_ambiguous_scenarios_do_not_become_incidents(report):
    for key, cov in report["coverage"].items():
        if key.startswith(("K:", "H:", "G:clock_skew", "G:reverse_order_arrival", "J:repeated_device_events", "J:access_failure_spike")):
            assert cov.get("incident_raised", 0) == 0, (key, cov)
