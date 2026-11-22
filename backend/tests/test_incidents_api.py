"""GET /incidents, GET /incidents/{id}, summary, status updates."""

import pytest

from tests.test_correlation import NORTH, alarm, correlate, denial, pass_events
from tests.test_quality_rules import GATE, health, ingest


@pytest.fixture
def seeded(client):
    ingest(client, pass_events("a", 0, track="T1") + pass_events("b", 3600, plate="Q 22222", track="T2")
           + [alarm("al", 7200)] + [denial(f"d{i}", 9000 + i * 20) for i in range(3)]
           + [health("h1", 10000, "device_offline", "offline", device="radar-int05")])
    correlate(client)
    return client


def test_list_pagination_and_sort(seeded):
    body = seeded.get("/incidents", params={"page_size": 2}).json()
    assert body["total"] == 5 and body["total_pages"] == 3 and body["has_next"]
    seen = []
    for page in (1, 2, 3):
        seen += seeded.get("/incidents", params={"page_size": 2, "page": page}).json()["items"]
    assert len({i["id"] for i in seen}) == 5
    ranks = {"critical": 5, "high": 4, "medium": 3, "low": 2, "informational": 1}
    assert [ranks[i["severity"]] for i in seen] == sorted((ranks[i["severity"]] for i in seen), reverse=True)
    chrono = seeded.get("/incidents", params={"sort": "first_occurred_at"}).json()["items"]
    assert [i["first_occurred_at"] for i in chrono] == sorted(i["first_occurred_at"] for i in chrono)


def test_filters(seeded):
    def cats(**params):
        return sorted(i["category"] for i in seeded.get("/incidents", params=params).json()["items"])

    assert cats(category="traffic_enforcement") == ["traffic_enforcement"] * 2
    assert cats(severity="critical") == ["device_failure"]
    assert cats(zone=NORTH["zone"]) == ["perimeter_intrusion"]
    assert cats(zone=GATE["zone"]) == ["unauthorized_access"]
    assert cats(device_id="radar-int05") == ["device_failure"]
    assert cats(source_type="lpr") == ["traffic_enforcement"] * 2  # alias accepted
    assert cats(event_id="b-anpr") == ["traffic_enforcement"]
    assert cats(status="open") == sorted(["traffic_enforcement"] * 2 + ["perimeter_intrusion", "unauthorized_access", "device_failure"])
    assert cats(occurred_from="2026-01-15T11:30:00Z", occurred_to="2026-01-15T12:30:00Z") == ["perimeter_intrusion"]


@pytest.mark.parametrize("params,field", [({"severity": "urgent"}, "severity"), ({"status": "closed"}, "status"),
                                          ({"category": "aliens"}, "category"), ({"occurred_from": "x"}, "occurred_from"),
                                          ({"source_type": "toaster"}, "source_type")])
def test_invalid_filters(seeded, params, field):
    response = seeded.get("/incidents", params=params)
    assert response.status_code == 422 and response.json()["error"]["details"][0]["field"] == field


def test_detail_has_all_section_4_2_fields(seeded):
    incident = seeded.get("/incidents", params={"category": "traffic_enforcement", "sort": "first_occurred_at"}).json()["items"][0]
    d = seeded.get(f"/incidents/{incident['id']}").json()
    for key in ("title", "category", "severity", "severity_reason", "confidence", "confidence_level", "confidence_reason", "status",
                "timeline", "devices", "zones", "linkage_explanation", "conflicts", "missing_information", "uncertainty",
                "recommended_actions", "observations", "inferences", "first_occurred_at", "last_occurred_at", "detected_at", "updated_at",
                "rule_version"):
        assert key in d, key
    entry = d["timeline"][0]
    for key in ("event_pk", "event_id", "submission_id", "role", "link_type", "core", "linkage_confidence", "linkage_explanation",
                "source_confidence"):
        assert key in entry
    assert seeded.get("/incidents/999999").json()["error"]["code"] == "not_found"


def test_status_update_and_validation(seeded):
    incident = seeded.get("/incidents").json()["items"][0]
    updated = seeded.patch(f"/incidents/{incident['id']}", json={"status": "false_positive", "note": "test alarm"}).json()
    assert updated["status"] == "false_positive" and updated["status_note"] == "test alarm" and updated["status_updated_at"]
    assert seeded.patch(f"/incidents/{incident['id']}", json={"status": "closed"}).status_code == 422
    correlate(seeded)  # re-correlation never touches operator status
    assert seeded.get(f"/incidents/{incident['id']}").json()["status"] == "false_positive"
    assert [i["id"] for i in seeded.get("/incidents", params={"status": "false_positive"}).json()["items"]] == [incident["id"]]


def test_summary(seeded):
    summary = seeded.get("/incidents/summary").json()
    assert summary["total"] == 5 and summary["inactive"] == 0
    assert {x["value"]: x["count"] for x in summary["by_category"]}["traffic_enforcement"] == 2
    assert summary["last_run"]["status"] == "completed" and summary["last_run"]["incidents_active"] == 5


# --------------------------------------------------------------------------- Phase 4B additions


def test_priority_sort_puts_unresolved_first_then_severity(seeded):
    items = seeded.get("/incidents", params={"page_size": 100}).json()["items"]
    critical = next(i for i in items if i["severity"] == "critical")
    assert seeded.patch(f"/incidents/{critical['id']}", json={"status": "resolved"}).status_code == 200
    ordered = seeded.get("/incidents", params={"sort": "priority", "page_size": 100}).json()["items"]
    assert ordered[-1]["id"] == critical["id"]  # resolved goes last although it is the most severe
    ranks = {"critical": 5, "high": 4, "medium": 3, "low": 2, "informational": 1}
    head = [ranks[i["severity"]] for i in ordered[:-1]]
    assert head == sorted(head, reverse=True)
    # stable across pages: concatenated pages equal the single list
    paged = []
    for page in (1, 2, 3):
        paged += seeded.get("/incidents", params={"sort": "priority", "page_size": 2, "page": page}).json()["items"]
    assert [i["id"] for i in paged] == [i["id"] for i in ordered]


def test_summary_unresolved_counts_follow_status(seeded):
    before = seeded.get("/incidents/summary").json()
    assert before["unresolved"] == 5
    by_sev = {x["value"]: x["count"] for x in before["unresolved_by_severity"]}
    assert by_sev == {x["value"]: x["count"] for x in before["by_severity"]}
    critical = seeded.get("/incidents", params={"severity": "critical"}).json()["items"][0]
    seeded.patch(f"/incidents/{critical['id']}", json={"status": "false_positive"})
    after = seeded.get("/incidents/summary").json()
    assert after["unresolved"] == 4 and after["total"] == 5
    assert {x["value"]: x["count"] for x in after["unresolved_by_severity"]}.get("critical", 0) == by_sev["critical"] - 1
    seeded.patch(f"/incidents/{critical['id']}", json={"status": "investigating"})  # investigating still counts as unresolved
    assert seeded.get("/incidents/summary").json()["unresolved"] == 5


def test_timeline_flags_low_source_confidence_and_describes_links(client):
    from tests.test_correlation import INT01
    from tests.test_quality_rules import ev

    low = ev(event_id="weak-out", source="radar", device="tcam-int02", event_type="speed_measurement", at=120, confidence=0.35, location=INT01)
    ingest(client, [health("h1", 0, "device_degraded", "degraded"), low, health("h2", 600, "sensor_failure", "fault")])
    correlate(client)
    incident = client.get("/incidents").json()["items"][0]
    d = client.get(f"/incidents/{incident['id']}").json()
    threshold = d["low_source_confidence_threshold"]
    assert 0 < threshold < 1
    for t in d["timeline"]:
        assert t["low_source_confidence"] == (t["source_confidence"] is not None and t["source_confidence"] < threshold)
        assert t["link_description"]
    weak = next(t for t in d["timeline"] if t["event_id"] == "weak-out")
    assert weak["low_source_confidence"] and not weak["core"] and "degradation" in weak["link_description"]
    occurred = [t["occurred_at"] for t in d["timeline"]]
    assert occurred == sorted(occurred)


def test_event_filter_links_event_back_to_its_incident(seeded):
    incident = seeded.get("/incidents", params={"event_id": "b-anpr"}).json()["items"][0]
    pk = next(t["event_pk"] for t in seeded.get(f"/incidents/{incident['id']}").json()["timeline"] if t["event_id"] == "b-anpr")
    assert [i["id"] for i in seeded.get("/incidents", params={"event": pk}).json()["items"]] == [incident["id"]]


def test_status_note_persists_and_unknown_incident_patch_is_404(seeded):
    incident = seeded.get("/incidents").json()["items"][0]
    seeded.patch(f"/incidents/{incident['id']}", json={"status": "acknowledged", "note": "on it"})
    d = seeded.get(f"/incidents/{incident['id']}").json()
    assert d["status"] == "acknowledged" and d["status_note"] == "on it"
    assert seeded.patch("/incidents/999999", json={"status": "resolved"}).status_code == 404
    assert seeded.patch(f"/incidents/{incident['id']}", json={"status": "resolved", "note": "x" * 2001}).status_code == 422
    assert seeded.get(f"/incidents/{incident['id']}").json()["status"] == "acknowledged"  # failed update changed nothing
