"""GET /quality-issues API, idempotent re-analysis and the ingest-triggered workflow."""

import pytest

from app.services.quality.configuration import get_quality_settings
from app.services.quality.engine import scheduler
from tests.test_quality_rules import INT01, analyze, ev, health, plate_read


@pytest.fixture
def seeded(client):
    events = [
        ev(event_id="late-1", device="cam-1", delay=900, location={"zone": "Gate-A"}),
        ev(event_id="late-2", device="cam-2", delay=4000, location={"zone": "Gate-B"}),
        ev(event_id="skew", device="cam-3", delay=-400, location={"zone": "Gate-B"}),
        health("h-off", 100, "device_offline", "offline", device="radar-int01"),
        ev(event_id="lowconf", source="face_recognition", device="frs-1", event_type="face_match", confidence=0.3,
           payload={"match_status": "matched", "subject_id": "EMP-1"}),
        plate_read("noplate", 50, "anpr-int01", INT01, plate=None, status="unreadable", confidence=0.1),
    ]
    client.post("/ingest", json=events)
    client.post("/ingest", json=[events[0]])  # exact duplicate
    client.post("/ingest", json=[ev(event_id=None)])  # invalid
    analyze(client)
    return client


def test_list_pagination_and_stable_sort(seeded):
    body = seeded.get("/quality-issues", params={"page_size": 3}).json()
    total = body["total"]
    assert total >= 8 and body["total_pages"] == -(-total // 3) and body["has_next"]
    ranks = {"critical": 5, "high": 4, "medium": 3, "low": 2, "informational": 1}
    seen = []
    for page in range(1, body["total_pages"] + 1):
        seen += seeded.get("/quality-issues", params={"page_size": 3, "page": page}).json()["items"]
    assert len(seen) == total and len({i["id"] for i in seen}) == total
    assert [ranks[i["severity"]] for i in seen] == sorted((ranks[i["severity"]] for i in seen), reverse=True)
    assert seen[0]["issue_type"] == "device_offline" and seen[0]["severity"] == "critical"


def test_filters(seeded):
    def ids(**params):
        return {i["issue_type"] for i in seeded.get("/quality-issues", params=params).json()["items"]}

    assert ids(issue_type="delayed_event") == {"delayed_event"}
    assert ids(issue_type="delayed_event,timestamp_conflict") == {"delayed_event", "timestamp_conflict"}
    assert ids(severity="critical") == {"device_offline"}
    assert ids(device_id="cam-3") == {"timestamp_conflict"}
    assert ids(zone="Gate-B") == {"delayed_event", "timestamp_conflict"}
    assert ids(source_type="frs") == {"low_confidence_detection"}  # alias accepted
    assert ids(source_type="device_health") == {"device_offline"}
    assert ids(event_id="noplate") == {"low_confidence_detection", "uncorrelatable_event"}
    assert ids(occurred_from="2030-01-01T00:00:00Z") == set()
    assert "invalid_record" in ids(rule_id="VAL-001")
    assert seeded.get("/quality-issues", params={"occurred_from": "2026-01-15T10:01:00Z", "issue_type": "delayed_event"}).json()["total"] == 0


@pytest.mark.parametrize("params,field", [({"issue_type": "nope"}, "issue_type"), ({"severity": "urgent"}, "severity"),
                                          ({"source_type": "toaster"}, "source_type"), ({"occurred_to": "yesterday"}, "occurred_to")])
def test_invalid_filters(seeded, params, field):
    response = seeded.get("/quality-issues", params=params)
    assert response.status_code == 422 and response.json()["error"]["details"][0]["field"] == field


def test_detail_and_not_found(seeded):
    item = seeded.get("/quality-issues", params={"issue_type": "delayed_event", "sort": "occurred_at"}).json()["items"][0]
    d = seeded.get(f"/quality-issues/{item['id']}").json()
    assert d["detection_reason"] and d["description"] and d["rule_id"] == "DLY-001" and d["rule_version"]
    evidence = d["evidence"][0]
    assert evidence["event"]["event_id"] == evidence["external_event_id"]
    assert evidence["submission"]["status"] == "accepted"
    # evidence is traceable to the stored event and the raw submission
    assert seeded.get(f"/events/{evidence['event']['id']}").json()["event_id"] == evidence["external_event_id"]
    assert seeded.get(f"/submissions/{evidence['submission']['id']}").json()["raw_content"]
    assert seeded.get("/quality-issues/999999").json()["error"]["code"] == "not_found"


def test_summary_and_event_cross_reference(seeded):
    summary = seeded.get("/quality-issues/summary").json()
    counts = {x["value"]: x["count"] for x in summary["by_type"]}
    assert summary["total"] == sum(counts.values()) == seeded.get("/quality-issues").json()["total"]
    assert counts["duplicate_submission"] == 1 and counts["invalid_record"] == 1
    assert summary["last_run"]["status"] == "completed" and summary["analysis_pending"] is False
    event_pk = seeded.get("/events", params={"event_id": "noplate"}).json()["items"][0]["id"]
    linked = seeded.get(f"/events/{event_pk}").json()["quality_issues"]
    assert {i["issue_type"] for i in linked} == {"low_confidence_detection", "uncorrelatable_event"}


def test_reanalysis_is_idempotent(seeded):
    before = {i["id"]: i for i in seeded.get("/quality-issues", params={"page_size": 500}).json()["items"]}
    run = analyze(seeded)
    assert (run["inserted"], run["updated"], run["removed"]) == (0, 0, 0)
    assert run["unchanged"] == len(before) == run["issues_total"]
    after = {i["id"]: i for i in seeded.get("/quality-issues", params={"page_size": 500}).json()["items"]}
    assert set(after) == set(before)
    assert all(after[k]["first_detected_at"] == before[k]["first_detected_at"] for k in after)


def test_new_data_updates_and_removes_findings(client):
    client.post("/ingest", json=[health("h1", 0, "device_offline", "offline", device="cam-x")])
    analyze(client)
    offline = client.get("/quality-issues", params={"issue_type": "device_offline"}).json()["items"][0]
    assert offline["severity"] == "critical"
    client.post("/ingest", json=[health("h2", 600, "device_recovered", "online", device="cam-x")])
    run = analyze(client)
    assert run["updated"] == 1 and run["inserted"] == 1  # offline issue updated in place; recovery issue added
    updated = client.get(f"/quality-issues/{offline['id']}").json()
    assert updated["severity"] == "high" and updated["details"]["recovered"] is True
    assert updated["first_detected_at"] == offline["first_detected_at"]


def test_ingest_triggers_background_analysis(client, monkeypatch):
    monkeypatch.setattr(get_quality_settings(), "auto_analyze", True)
    response = client.post("/ingest", json=[ev(event_id="late", delay=3600)])
    assert response.json()["quality_analysis"] == "scheduled"
    assert scheduler.wait_idle(30)
    found = client.get("/quality-issues", params={"issue_type": "delayed_event"}).json()
    assert found["total"] == 1
    assert client.get("/quality-issues/summary").json()["last_run"]["trigger"] == "ingest"


def test_ingest_reports_disabled_analysis(client):
    assert client.post("/ingest", json=[ev()]).json()["quality_analysis"] == "disabled"


def test_production_code_never_reads_ground_truth():
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    offenders = [str(p) for p in app_dir.rglob("*.py") if "datagen" in p.read_text() or "ground_truth" in p.read_text()]
    assert offenders == []
