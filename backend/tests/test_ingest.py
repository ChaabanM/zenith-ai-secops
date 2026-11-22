"""POST /ingest: validation, batching, JSONL, evidence preservation and duplicates."""

import json

import pytest

from app.core.config import get_settings
from app.schemas.sources import SOURCE_ALIASES, SourceType, resolve_source_type
from tests.conftest import make_event

NDJSON = {"Content-Type": "application/x-ndjson"}


def ingest(client, body, status=None):
    response = client.post("/ingest", json=body)
    if status is not None:
        assert response.status_code == status, response.text
    return response


def ingest_jsonl(client, text: str):
    return client.post("/ingest", content=text.encode("utf-8"), headers=NDJSON)


def error_codes(record) -> set[str]:
    return {e["code"] for e in record["errors"]}


def error_fields(record) -> set[str]:
    return {e["field"] for e in record["errors"]}


def warning_codes(record) -> set[str]:
    return {w["code"] for w in record["warnings"]}


def invalid_summary(response):
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    return error["details"]


# --------------------------------------------------------------------------- sources


@pytest.mark.parametrize("source", [s.value for s in SourceType])
def test_every_mandatory_source_is_accepted(client, source):
    body = ingest(client, make_event(event_id=f"evt-{source}", source_type=source), status=201).json()
    assert body["accepted"] == 1
    event = client.get(f"/events/{body['records'][0]['normalized_event_id']}").json()
    assert event["source_type"] == source


def test_fifteen_canonical_sources():
    assert len(SourceType) == 15


@pytest.mark.parametrize(
    "alias,canonical",
    [(alias, source.value) for source, aliases in SOURCE_ALIASES.items() for alias in aliases]
    + [("Red-Light Violation", "traffic_violation"), ("  CCTV ", "cctv"), ("Network Security Log", "network_security")],
)
def test_source_aliases_resolve(alias, canonical):
    assert resolve_source_type(alias).value == canonical


def test_alias_is_normalized_and_original_kept_in_evidence(client):
    body = ingest(client, make_event(source_type="red_light_violation", event_type="red_light"), status=201).json()
    record = body["records"][0]
    assert "source_alias_normalized" in warning_codes(record)
    event = client.get(f"/events/{record['normalized_event_id']}").json()
    assert event["source_type"] == "traffic_violation"
    assert json.loads(event["submission"]["raw_content"])["source_type"] == "red_light_violation"


def test_unknown_source_is_invalid(client):
    details = invalid_summary(ingest(client, make_event(source_type="toaster")))
    assert "source_type" in error_fields(details["records"][0])


# --------------------------------------------------------------------------- single events


def test_valid_single_event(client):
    response = ingest(client, make_event(), status=201)
    body = response.json()
    assert body["total_received"] == 1 and body["accepted"] == 1 and body["invalid"] == 0
    assert body["duplicate_event_ids"] == 0
    assert body["batch_id"].startswith("batch-")
    record = body["records"][0]
    assert record["status"] == "accepted" and record["event_id"] == "evt-test-0001"
    assert record["errors"] == [] and record["warnings"] == []

    event = client.get(f"/events/{record['normalized_event_id']}").json()
    assert event["event_id"] == "evt-test-0001"
    assert event["occurred_at"] == "2026-01-15T10:12:04Z"
    assert event["delay_seconds"] == 14
    assert event["location"] == {"latitude": 25.2048, "longitude": 55.2708, "zone": "Gate-A"}
    assert event["severity"] == "high" and event["confidence"] == 0.94
    assert event["payload"]["track_id"] == "T12345"


def test_invalid_single_event_returns_422_with_record_details(client):
    details = invalid_summary(ingest(client, make_event(confidence=1.7)))
    assert details["total_received"] == 1 and details["invalid"] == 1 and details["accepted"] == 0
    record = details["records"][0]
    assert record["status"] == "invalid" and record["event_id"] == "evt-test-0001"
    assert "confidence" in error_fields(record)
    # the rejected record is still preserved as evidence
    evidence = client.get(f"/submissions/{record['submission_id']}").json()
    assert evidence["status"] == "invalid"
    assert json.loads(evidence["raw_content"])["confidence"] == 1.7


@pytest.mark.parametrize("field", ["event_id", "source_type", "device_id", "event_type", "occurred_at"])
def test_missing_mandatory_field_is_invalid(client, field):
    details = invalid_summary(ingest(client, make_event(**{field: None})))
    record = details["records"][0]
    assert field in error_fields(record)
    assert "missing" in error_codes(record)


@pytest.mark.parametrize("field,value", [("event_id", "   "), ("event_id", "evt 1"), ("device_id", 1234), ("event_type", "")])
def test_bad_identifier_values_are_invalid(client, field, value):
    record = invalid_summary(ingest(client, make_event(**{field: value})))["records"][0]
    assert field in error_fields(record)


def test_missing_optional_fields_are_preserved_as_unknown(client):
    sparse = make_event(received_at=None, location=None, severity=None, confidence=None, payload=None)
    record = ingest(client, sparse, status=201).json()["records"][0]
    assert {"missing_received_at", "missing_location", "missing_severity", "missing_confidence"} <= warning_codes(record)
    event = client.get(f"/events/{record['normalized_event_id']}").json()
    # nothing is invented: unknown values stay null
    assert event["received_at"] is None and event["delay_seconds"] is None
    assert event["location"] == {"latitude": None, "longitude": None, "zone": None}
    assert event["severity"] is None and event["confidence"] is None
    assert event["payload"] == {}


def test_zone_without_coordinates_is_valid(client):
    record = ingest(client, make_event(location={"zone": "SOC-Network"}), status=201).json()["records"][0]
    assert "missing_coordinates" in warning_codes(record)


def test_unknown_fields_are_ignored_but_kept_in_evidence(client):
    record = ingest(client, make_event(vendor_extra={"fw": "1.2"}), status=201).json()["records"][0]
    assert "unknown_fields_ignored" in warning_codes(record)
    evidence = client.get(f"/submissions/{record['submission_id']}").json()
    assert json.loads(evidence["raw_content"])["vendor_extra"] == {"fw": "1.2"}


# --------------------------------------------------------------------------- field validation


@pytest.mark.parametrize(
    "value",
    ["15/01/2026 10:12", "2026-02-30T10:00:00Z", "2026-01-15T25:61:00Z", "2026-01-15T10:12:04", "2026-01-15", "yesterday", 1736935924, None],
)
def test_invalid_timestamps(client, value):
    body = make_event(occurred_at=value) if value is not None else make_event()
    if value is None:
        body["occurred_at"] = None  # explicit null
    record = invalid_summary(ingest(client, body))["records"][0]
    assert "occurred_at" in error_fields(record)


def test_invalid_received_at_is_invalid(client):
    record = invalid_summary(ingest(client, make_event(received_at="not-a-time")))["records"][0]
    assert "received_at" in error_fields(record)


def test_timezone_offsets_are_converted_to_utc(client):
    record = ingest(client, make_event(occurred_at="2026-01-15T14:12:04+04:00", received_at="2026-01-15T14:12:18.500+04:00"), status=201).json()["records"][0]
    event = client.get(f"/events/{record['normalized_event_id']}").json()
    assert event["occurred_at"] == "2026-01-15T10:12:04Z"
    assert event["received_at"] == "2026-01-15T10:12:18.500000Z"
    assert event["delay_seconds"] == 14.5


@pytest.mark.parametrize(
    "location,field",
    [
        ({"latitude": 91.0, "longitude": 55.2, "zone": "Gate-A"}, "location.latitude"),
        ({"latitude": -90.5, "longitude": 55.2}, "location.latitude"),
        ({"latitude": 25.2, "longitude": 180.01}, "location.longitude"),
        ({"latitude": 25.2, "longitude": -181}, "location.longitude"),
        ({"latitude": "25.2", "longitude": 55.2}, "location.latitude"),
        ({"latitude": 25.2}, "location"),
        ("Gate-A", "location"),
    ],
)
def test_invalid_coordinates(client, location, field):
    record = invalid_summary(ingest(client, make_event(location=location)))["records"][0]
    assert field in error_fields(record), record["errors"]


@pytest.mark.parametrize("value", [1.01, -0.1, 42, "0.9", "high", True])
def test_invalid_confidence(client, value):
    record = invalid_summary(ingest(client, make_event(confidence=value)))["records"][0]
    assert "confidence" in error_fields(record)


@pytest.mark.parametrize("value", [0, 0.0, 1, 1.0])
def test_confidence_bounds_are_inclusive(client, value):
    ingest(client, make_event(confidence=value), status=201)


def test_severity_validation_and_aliases(client):
    assert "severity" in error_fields(invalid_summary(ingest(client, make_event(severity="urgent")))["records"][0])
    body = ingest(client, [make_event(event_id="e-1", severity="HIGH"), make_event(event_id="e-2", severity="info")], status=201).json()
    severities = {client.get(f"/events/{r['normalized_event_id']}").json()["severity"] for r in body["records"]}
    assert severities == {"high", "informational"}


@pytest.mark.parametrize("payload", ["plate=Q 12345", [1, 2], 7])
def test_payload_must_be_an_object(client, payload):
    record = invalid_summary(ingest(client, make_event(payload=payload)))["records"][0]
    assert "payload" in error_fields(record)


# --------------------------------------------------------------------------- suspicious but valid


def test_delayed_event_is_valid(client):
    record = ingest(client, make_event(received_at="2026-01-15T16:12:04Z"), status=201).json()["records"][0]
    assert record["status"] == "accepted"
    assert client.get(f"/events/{record['normalized_event_id']}").json()["delay_seconds"] == 6 * 3600


def test_received_before_occurred_is_kept_as_suspicious(client):
    record = ingest(client, make_event(received_at="2026-01-15T10:02:04Z"), status=201).json()["records"][0]
    assert record["status"] == "accepted"
    assert "received_before_occurred" in warning_codes(record)
    assert client.get(f"/events/{record['normalized_event_id']}").json()["delay_seconds"] == -600


def test_low_confidence_event_is_valid(client):
    record = ingest(client, make_event(confidence=0.12), status=201).json()["records"][0]
    assert record["status"] == "accepted" and record["warnings"] == []


def test_out_of_order_arrival_is_accepted_and_listed_by_occurrence(client):
    later = make_event(event_id="evt-later", occurred_at="2026-01-15T12:00:00Z", received_at="2026-01-15T12:00:01Z")
    earlier = make_event(event_id="evt-earlier", occurred_at="2026-01-15T09:00:00Z", received_at="2026-01-15T12:00:05Z")
    ingest(client, later, status=201)
    ingest(client, earlier, status=201)
    ordered = [e["event_id"] for e in client.get("/events", params={"sort": "occurred_at"}).json()["items"]]
    assert ordered == ["evt-earlier", "evt-later"]
    arrival = [e["event_id"] for e in client.get("/events", params={"sort": "id"}).json()["items"]]
    assert arrival == ["evt-later", "evt-earlier"]


# --------------------------------------------------------------------------- batches


def test_json_array_batch(client):
    events = [make_event(event_id=f"evt-{i}") for i in range(5)]
    body = ingest(client, events, status=201).json()
    assert (body["total_received"], body["accepted"]) == (5, 5)
    assert [r["position"] for r in body["records"]] == [1, 2, 3, 4, 5]
    assert client.get("/events").json()["total"] == 5


def test_wrapper_object_batch(client):
    body = ingest(client, {"events": [make_event(event_id="w-1"), make_event(event_id="w-2")]}, status=201).json()
    assert body["accepted"] == 2


def test_mixed_batch_is_partially_accepted(client):
    batch = [
        make_event(event_id="ok-1"),
        make_event(event_id="bad-1", confidence=3),
        "not an object",
        make_event(event_id="ok-2", source_type="anpr"),
        make_event(event_id=None),
        {},
    ]
    body = ingest(client, batch, status=201).json()
    assert (body["total_received"], body["accepted"], body["invalid"]) == (6, 2, 4)
    by_position = {r["position"]: r for r in body["records"]}
    assert by_position[2]["event_id"] == "bad-1" and by_position[2]["status"] == "invalid"
    assert "not_an_object" in error_codes(by_position[3])
    assert by_position[5]["event_id"] is None
    # every submission, valid or not, is preserved
    submissions = client.get("/submissions", params={"batch_id": body["batch_id"]}).json()
    assert submissions["total"] == 6
    assert [s["status"] for s in submissions["items"]] == ["accepted", "invalid", "invalid", "accepted", "invalid", "invalid"]


def test_batch_record_reports_are_bounded(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_record_reports", 3)
    batch = [make_event(event_id=f"evt-{i}") for i in range(6)] + [make_event(event_id="bad", confidence=9)]
    body = ingest(client, batch, status=201).json()
    assert body["records_truncated"] is True
    assert [r["status"] for r in body["records"]] == ["invalid"]
    assert body["accepted"] == 6


def test_batches_are_processed_in_bounded_chunks(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "ingest_chunk_size", 2)
    batch = [make_event(event_id=f"c-{i}") for i in range(5)] + [make_event(event_id="c-0"), make_event(event_id="c-1", severity="low")]
    body = ingest(client, batch, status=201).json()
    assert (body["accepted"], body["exact_duplicates"], body["conflicting_duplicates"]) == (5, 1, 1)


# --------------------------------------------------------------------------- malformed requests


def test_malformed_json_body_is_400_and_preserved(client):
    raw = b'{"event_id": "evt-1", "source_type": "radar",'
    response = client.post("/ingest", content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "malformed_json"
    evidence = client.get(f"/submissions/{error['details']['submission_id']}").json()
    assert evidence["raw_content"] == raw.decode() and evidence["status"] == "invalid"
    assert client.get(f"/batches/{error['details']['batch_id']}").json()["status"] == "rejected"


@pytest.mark.parametrize(
    "body,code",
    [(b"", "empty_body"), (b"   ", "empty_body"), (b"[]", "empty_batch"), (b'{"events": {"a": 1}}', "invalid_batch_format"), (b"[NaN]", "malformed_json")],
)
def test_bad_request_bodies(client, body, code):
    response = client.post("/ingest", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == code


def test_multipart_upload_is_rejected(client):
    response = client.post("/ingest", files={"file": ("events.json", b"[]", "application/json")})
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"


def test_oversized_json_body_is_413(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_json_body_bytes", 100)
    response = ingest(client, [make_event(event_id=f"e-{i}") for i in range(5)])
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_oversized_record_is_invalid_with_truncated_evidence(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_record_bytes", 400)
    big = make_event(event_id="evt-big", payload={"blob": "x" * 1000})
    body = ingest(client, [make_event(), big], status=201).json()
    record = body["records"][1]
    assert record["status"] == "invalid" and "record_too_large" in error_codes(record)
    evidence = client.get(f"/submissions/{record['submission_id']}").json()
    assert evidence["raw_truncated"] is True and evidence["raw_size_bytes"] > 1000
    assert len(evidence["raw_content"].encode()) <= 400


# --------------------------------------------------------------------------- JSONL


def test_jsonl_with_malformed_lines_continues(client):
    lines = [
        json.dumps(make_event(event_id="j-1")),
        '{"event_id": "j-2", "source_type": "radar"',  # truncated
        "",  # blank lines are skipped
        "ANPR|anpr-int01|Q 12345|2026-01-15 10:12:04",
        json.dumps(make_event(event_id="j-3", confidence=-1)),
        json.dumps(make_event(event_id="j-4")),
    ]
    response = ingest_jsonl(client, "\n".join(lines) + "\n")
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["total_received"], body["accepted"], body["invalid"]) == (5, 2, 3)
    by_line = {r["position"]: r for r in body["records"]}
    assert set(by_line) == {1, 2, 4, 5, 6}  # positions are physical line numbers
    assert "malformed_json" in error_codes(by_line[2]) and by_line[2]["event_id"] == "j-2"
    assert "malformed_json" in error_codes(by_line[4]) and by_line[4]["event_id"] is None
    assert "confidence" in error_fields(by_line[5])
    assert by_line[6]["status"] == "accepted"
    evidence = client.get(f"/submissions/{by_line[4]['submission_id']}").json()
    assert evidence["raw_content"] == lines[3]


def test_jsonl_crlf_and_missing_final_newline(client):
    text = json.dumps(make_event(event_id="crlf-1")) + "\r\n" + json.dumps(make_event(event_id="crlf-2"))
    body = ingest_jsonl(client, text).json()
    assert body["accepted"] == 2


def test_jsonl_format_query_parameter_and_non_utf8(client):
    data = json.dumps(make_event(event_id="q-1")).encode() + b"\n\xff\xfe broken\n"
    response = client.post("/ingest", params={"format": "jsonl", "filename": "x.jsonl"}, content=data, headers={"Content-Type": "text/plain"})
    body = response.json()
    assert response.status_code == 201
    assert body["filename"] == "x.jsonl" and body["channel"] == "jsonl"
    assert "invalid_encoding" in error_codes(body["records"][1])


def test_jsonl_all_invalid_and_empty(client):
    assert ingest_jsonl(client, "nope\n{}\n").status_code == 422
    response = ingest_jsonl(client, "\n\n")
    assert response.status_code == 400 and response.json()["error"]["code"] == "empty_body"


def test_jsonl_streams_across_many_chunks(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "ingest_chunk_size", 7)
    text = "".join(json.dumps(make_event(event_id=f"s-{i}")) + "\n" for i in range(50))
    body = ingest_jsonl(client, text).json()
    assert body["accepted"] == 50 and client.get("/events").json()["total"] == 50


# --------------------------------------------------------------------------- duplicates


def test_exact_duplicate_in_same_request(client):
    body = ingest(client, [make_event(), make_event()], status=201).json()
    assert (body["accepted"], body["duplicate_event_ids"], body["exact_duplicates"]) == (1, 1, 1)
    dup = body["records"][1]
    assert dup["status"] == "duplicate" and dup["duplicate_kind"] == "exact"
    assert dup["normalized_event_id"] == body["records"][0]["normalized_event_id"]
    assert "duplicate_exact" in warning_codes(dup)


def test_conflicting_duplicate_never_overwrites_normalized_event(client):
    first = ingest(client, make_event(), status=201).json()["records"][0]
    altered = make_event(severity="critical", confidence=0.51, payload={"speed_kph": 140})
    response = ingest(client, altered, status=200)  # nothing new stored
    body = response.json()
    assert (body["accepted"], body["duplicate_event_ids"], body["conflicting_duplicates"]) == (0, 1, 1)
    dup = body["records"][0]
    assert dup["duplicate_kind"] == "conflicting"
    assert set(dup["conflicting_fields"]) == {"severity", "confidence", "payload"}
    assert dup["normalized_event_id"] == first["normalized_event_id"]

    event = client.get(f"/events/{first['normalized_event_id']}").json()
    assert event["severity"] == "high" and event["confidence"] == 0.94  # first valid submission wins
    assert event["submission"]["id"] == first["submission_id"]
    assert [d["id"] for d in event["duplicate_submissions"]] == [dup["submission_id"]]
    assert json.loads(event["duplicate_submissions"][0]["raw_content"])["severity"] == "critical"
    assert client.get("/events").json()["total"] == 1


def test_whitespace_or_key_order_differences_are_exact_duplicates(client):
    ingest(client, make_event(), status=201)
    reordered = dict(reversed(list(make_event().items())))
    reordered["event_id"] = " evt-test-0001 "
    body = ingest(client, reordered, status=200).json()
    assert body["records"][0]["duplicate_kind"] == "exact"


def test_invalid_submission_does_not_claim_event_id(client):
    invalid_summary(ingest(client, make_event(confidence=5)))
    body = ingest(client, make_event(), status=201).json()
    assert body["records"][0]["status"] == "accepted"
    submissions = client.get("/submissions", params={"event_id": "evt-test-0001"}).json()
    assert [s["status"] for s in submissions["items"]] == ["invalid", "accepted"]


def test_same_device_time_location_with_different_ids_are_both_kept(client):
    a = make_event(event_id="evt-a", payload={"track_id": "T1"})
    b = make_event(event_id="evt-b", payload={"track_id": "T2"})
    body = ingest(client, [a, b], status=201).json()
    assert body["accepted"] == 2 and body["duplicate_event_ids"] == 0


def test_original_event_id_is_preserved_everywhere(client):
    event = make_event(event_id="Cam-7:2026/01/15#000123")
    record = ingest(client, event, status=201).json()["records"][0]
    assert record["event_id"] == "Cam-7:2026/01/15#000123"
    detail = client.get(f"/events/{record['normalized_event_id']}").json()
    assert detail["event_id"] == "Cam-7:2026/01/15#000123"
    assert detail["submission"]["external_event_id"] == "Cam-7:2026/01/15#000123"
    listed = client.get("/events", params={"event_id": "Cam-7:2026/01/15#000123"}).json()
    assert listed["total"] == 1
