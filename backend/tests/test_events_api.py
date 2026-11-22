"""GET /events, /events/{id}, /events/facets, /submissions and /batches."""

import pytest

from tests.conftest import make_event


@pytest.fixture
def seeded(client):
    """12 events: 3 sources x 2 zones x 2 severities, 10 minutes apart, plus two sharing a timestamp."""
    events = []
    sources = ["radar", "anpr", "access_control"]
    for i in range(10):
        events.append(
            make_event(
                event_id=f"evt-{i:02d}",
                source_type=sources[i % 3],
                device_id=f"dev-{i % 4}",
                severity="high" if i % 2 else "low",
                occurred_at=f"2026-01-15T10:{i * 5:02d}:00Z",
                location={"latitude": 25.2, "longitude": 55.3, "zone": "Gate-A" if i < 5 else "Gate-B"},
            )
        )
    events.append(make_event(event_id="evt-tie-a", occurred_at="2026-01-15T10:20:00Z", severity="critical"))
    events.append(make_event(event_id="evt-tie-b", occurred_at="2026-01-15T10:20:00Z", severity="critical"))
    body = client.post("/ingest", json=events).json()
    assert body["accepted"] == 12
    return body


def ids(response) -> list[str]:
    return [e["event_id"] for e in response.json()["items"]]


def test_empty_listing(client):
    body = client.get("/events").json()
    assert body == {"items": [], "page": 1, "page_size": 50, "total": 0, "total_pages": 0, "has_next": False}


def test_pagination_metadata_and_traversal(client, seeded):
    first = client.get("/events", params={"page_size": 5}).json()
    assert (first["total"], first["total_pages"], first["has_next"], len(first["items"])) == (12, 3, True, 5)
    seen = []
    for page in (1, 2, 3):
        seen += ids(client.get("/events", params={"page": page, "page_size": 5}))
    assert len(seen) == 12 and len(set(seen)) == 12
    last = client.get("/events", params={"page": 3, "page_size": 5}).json()
    assert last["has_next"] is False and len(last["items"]) == 2
    beyond = client.get("/events", params={"page": 9, "page_size": 5}).json()
    assert beyond["items"] == [] and beyond["total"] == 12


@pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 0}, {"page_size": 501}, {"sort": "severity"}, {"page": "x"}])
def test_invalid_paging_parameters(client, params):
    response = client.get("/events", params=params)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_stable_sort_with_equal_timestamps(client, seeded):
    desc = ids(client.get("/events"))
    assert desc[0] == "evt-09"
    # equal occurred_at: id breaks the tie (newest id first in descending order)
    assert desc.index("evt-tie-b") < desc.index("evt-tie-a")
    asc = ids(client.get("/events", params={"sort": "occurred_at"}))
    assert asc == list(reversed(desc))
    # tie order is identical on repeated requests and across page boundaries
    paged = []
    for page in range(1, 13):
        paged += ids(client.get("/events", params={"page": page, "page_size": 1}))
    assert paged == desc


def test_filter_by_severity(client, seeded):
    assert client.get("/events", params={"severity": "critical"}).json()["total"] == 2
    assert client.get("/events", params={"severity": "critical,high"}).json()["total"] == 7
    assert client.get("/events", params=[("severity", "low"), ("severity", "critical")]).json()["total"] == 7


def test_filter_by_source_accepts_aliases(client, seeded):
    radar = client.get("/events", params={"source_type": "radar"}).json()["total"]
    assert radar == client.get("/events", params={"source_type": "speed_radar"}).json()["total"] == 6
    assert client.get("/events", params={"source_type": "anpr,access_control"}).json()["total"] == 6


def test_filter_by_device_and_zone(client, seeded):
    assert set(ids(client.get("/events", params={"device_id": "dev-1"}))) == {"evt-01", "evt-05", "evt-09"}
    assert client.get("/events", params={"zone": "Gate-B"}).json()["total"] == 5
    assert client.get("/events", params={"zone": "Gate-A", "device_id": "dev-0"}).json()["total"] == 2


def test_filter_by_time_range(client, seeded):
    params = {"occurred_from": "2026-01-15T10:10:00Z", "occurred_to": "2026-01-15T10:20:00Z"}
    assert set(ids(client.get("/events", params=params))) == {"evt-02", "evt-03"}  # from inclusive, to exclusive
    shifted = {"occurred_from": "2026-01-15T14:10:00+04:00", "occurred_to": "2026-01-15T14:20:00+04:00"}
    assert ids(client.get("/events", params=shifted)) == ids(client.get("/events", params=params))


def test_combined_filters(client, seeded):
    params = {"source_type": "radar", "severity": "low", "zone": "Gate-A", "occurred_from": "2026-01-15T10:00:00Z"}
    assert set(ids(client.get("/events", params=params))) == {"evt-00"}


@pytest.mark.parametrize(
    "params,field",
    [({"severity": "urgent"}, "severity"), ({"source_type": "toaster"}, "source_type"), ({"occurred_from": "yesterday"}, "occurred_from"), ({"occurred_to": "2026-01-15T10:00:00"}, "occurred_to")],
)
def test_invalid_filters_use_error_envelope(client, params, field):
    response = client.get("/events", params=params)
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == field


def test_event_detail_exposes_evidence_and_provenance(client, seeded):
    record = seeded["records"][0]
    detail = client.get(f"/events/{record['normalized_event_id']}").json()
    assert detail["event_id"] == "evt-00"
    assert detail["submission"]["id"] == record["submission_id"]
    assert detail["submission"]["batch_id"] == seeded["batch_id"]
    assert detail["submission"]["position"] == 1
    assert detail["submission"]["status"] == "accepted"
    assert '"event_id":"evt-00"' in detail["submission"]["raw_content"]
    assert len(detail["content_sha256"]) == 64 and len(detail["submission"]["content_sha256"]) == 64
    assert detail["duplicate_submissions"] == []


def test_event_detail_not_found(client):
    response = client.get("/events/999999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_facets(client, seeded):
    facets = client.get("/events/facets").json()
    assert facets["total_events"] == 12
    assert {f["value"]: f["count"] for f in facets["sources"]} == {"radar": 6, "anpr": 3, "access_control": 3}
    assert {f["value"] for f in facets["zones"]} == {"Gate-A", "Gate-B"}
    assert facets["occurred_min"] == "2026-01-15T10:00:00Z"
    assert facets["occurred_max"] == "2026-01-15T10:45:00Z"


def test_submissions_and_batches(client, seeded):
    client.post("/ingest", json=[make_event(event_id="evt-00"), make_event(event_id="x", confidence=4)])
    invalid = client.get("/submissions", params={"status": "invalid"}).json()
    assert invalid["total"] == 1 and invalid["items"][0]["external_event_id"] == "x"
    duplicates = client.get("/submissions", params={"status": "duplicate"}).json()
    assert duplicates["items"][0]["normalized_event_id"] == seeded["records"][0]["normalized_event_id"]
    batches = client.get("/batches").json()
    assert batches["total"] == 2
    newest = batches["items"][0]
    assert (newest["total_received"], newest["accepted"], newest["invalid"], newest["duplicates"]) == (2, 0, 1, 1)
    assert client.get(f"/batches/{seeded['batch_id']}").json()["accepted"] == 12
    assert client.get("/batches/nope").status_code == 404
    assert client.get("/submissions/999999").status_code == 404
    assert client.get("/submissions", params={"status": "bogus"}).status_code == 422
