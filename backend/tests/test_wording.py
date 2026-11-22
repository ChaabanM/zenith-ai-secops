"""User-facing wording never presents time-and-place pairing as verified identity (Phase 7B)."""

import json

from tests.test_correlation import correlate, detail, incidents
from tests.test_quality_rules import analyze, badge, face, ingest, of_type


def test_identity_conflict_is_described_as_a_provisional_pairing(client):
    ingest(client, [badge("b1", 0, "EMP-1"), face("f1", 1, "EMP-2")])
    analyze(client)
    finding = client.get(f"/quality-issues/{of_type(client, 'conflicting_sensor_results')[0]['id']}").json()
    reason = finding["detection_reason"]
    assert "provisionally paired for conflict analysis" in reason and "It is not verified that they concern the same person." in reason
    assert "treated as the same person" not in reason
    assert {e["external_event_id"] for e in finding["evidence"]} == {"b1", "f1"}  # evidence unchanged
    assert finding["severity"] == "high" and finding["confidence"] is not None  # severity and score unchanged


def test_identity_incident_link_is_provisional(client):
    ingest(client, [badge("b1", 0, "EMP-1"), face("f1", 1, "EMP-2")])
    correlate(client)
    d = detail(client, incidents(client)[0]["id"])
    face_link = next(t for t in d["timeline"] if t["event_id"] == "f1")
    assert "not verified to be the same person" in face_link["linkage_explanation"]
    assert "not verified to be the same person" in face_link["link_description"]
    assert "not verified" in d["summary"]


def test_no_user_facing_text_claims_verified_identity(client):
    ingest(client, [badge("b1", 0, "EMP-1"), face("f1", 1, "EMP-2")])
    analyze(client)
    correlate(client)
    texts = json.dumps([client.get("/quality-issues", params={"page_size": 500}).json(),
                        [detail(client, i["id"]) for i in incidents(client)],
                        client.get("/briefing", params={"mode": "day", "date": "2026-01-15"}).json()])
    for claim in ("treated as the same person", "so they are the same person", "is the same person", "treated as the same vehicle"):
        assert claim not in texts, claim
