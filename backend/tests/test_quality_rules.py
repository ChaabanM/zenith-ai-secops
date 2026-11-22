"""Quality rules: each anomaly is detected with traceable evidence, and look-alikes are not."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import make_event

T0 = datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)
GATE = {"latitude": 25.221003, "longitude": 55.329004, "zone": "Gate-A"}
INT01 = {"latitude": 25.265512, "longitude": 55.296204, "zone": "Harbour-Rd-INT01"}
INT06 = {"latitude": 25.112406, "longitude": 55.200118, "zone": "Expressway-Exit21-INT06"}  # ~19.5 km from INT01


def ts(seconds: float = 0) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


_counter = iter(range(1, 10**6))


def ev(source="cctv", device="cam-1", event_type="motion_detected", at=0.0, delay=2.0, **kw):
    """Event at T0+at seconds, received `delay` seconds later (None: no received_at)."""
    fields = {
        "event_id": kw.pop("event_id", f"evt-{next(_counter):06d}"),
        "source_type": source,
        "device_id": device,
        "event_type": event_type,
        "occurred_at": ts(at),
        "received_at": ts(at + delay) if delay is not None else None,
        "location": kw.pop("location", GATE),
        "severity": kw.pop("severity", "low"),
        "confidence": kw.pop("confidence", 0.9),
        "payload": kw.pop("payload", {}),
    }
    fields.update(kw)
    return make_event(**fields)


def ingest(client, events):
    response = client.post("/ingest", json=events)
    assert response.status_code in (200, 201, 422), response.text
    return response


def analyze(client):
    response = client.post("/quality-issues/analyze")
    assert response.status_code == 200, response.text
    return response.json()


def issues(client, **params):
    body = client.get("/quality-issues", params={"page_size": 500, **params}).json()
    return body["items"]


def detail(client, issue_id):
    return client.get(f"/quality-issues/{issue_id}").json()


def of_type(client, issue_type, subtype=None):
    found = issues(client, issue_type=issue_type)
    return [i for i in found if subtype is None or i["subtype"] == subtype]


def evidence_ids(client, issue):
    return {e["external_event_id"] for e in detail(client, issue["id"])["evidence"]}


# --------------------------------------------------------------------------- A. duplicates


def test_exact_and_conflicting_duplicates(client):
    original = ev(event_id="evt-dup", source="radar", device="radar-1", event_type="speed_violation", payload={"speed_kph": 90})
    altered = {**original, "confidence": 0.4, "payload": {"speed_kph": 140}}
    first = ingest(client, [original]).json()["records"][0]
    ingest(client, [original, altered])
    analyze(client)
    exact = of_type(client, "duplicate_submission", "exact")
    conflicting = of_type(client, "duplicate_submission", "conflicting")
    assert len(exact) == 1 and len(conflicting) == 1
    d = detail(client, conflicting[0]["id"])
    roles = {e["role"]: e for e in d["evidence"]}
    assert roles["original_submission"]["submission"]["id"] == first["submission_id"]
    assert roles["duplicate_submission"]["submission"]["status"] == "duplicate"
    assert roles["stored_event"]["event"]["event_id"] == "evt-dup"
    diffs = {x["field"]: x for x in d["details"]["differences"]}
    assert diffs["confidence"]["stored"] == 0.9 and diffs["confidence"]["resubmitted"] == 0.4
    assert diffs["payload.speed_kph"]["resubmitted"] == 140
    assert d["uncertainty"] and "first valid submission is kept" in d["detection_reason"]


def test_similar_observations_with_different_ids_are_not_duplicates(client):
    ingest(client, [ev(event_id="a", payload={"track_id": "T1"}), ev(event_id="b", payload={"track_id": "T2"})])
    analyze(client)
    assert of_type(client, "duplicate_submission") == []


# --------------------------------------------------------------------------- B. invalid records


def test_malformed_and_incomplete_records(client):
    lines = [
        '{"event_id": "evt-trunc", "source_type": "radar"',
        json.dumps(ev(event_id=None)),
        json.dumps(ev(event_id="bad-ts", occurred_at="15/01/2026 10:00")),
        json.dumps(ev(event_id="bad-lat", location={"latitude": 95.0, "longitude": 55.0, "zone": "Gate-A"})),
        json.dumps(ev(event_id="bad-conf", confidence=1.7)),
        json.dumps(ev(event_id="bad-type", device_id=12345)),
        json.dumps(ev(event_id="ok")),
    ]
    client.post("/ingest", content="\n".join(lines).encode(), headers={"Content-Type": "application/x-ndjson"})
    analyze(client)
    found = of_type(client, "invalid_record")
    assert len(found) == 6
    subtypes = sorted(i["subtype"] for i in found)
    assert subtypes == sorted(["malformed_json", "missing_mandatory_field", "invalid_timestamp", "invalid_coordinates",
                               "invalid_confidence", "incorrect_type"])
    missing = next(i for i in found if i["subtype"] == "missing_mandatory_field")
    d = detail(client, missing["id"])
    assert d["missing_evidence"] == "Mandatory field(s) not provided: event_id"
    assert d["time_basis"] == "ingested_at"
    assert d["evidence"][0]["submission"]["status"] == "invalid" and d["evidence"][0]["event"] is None
    assert "cam-1" in missing["devices"]  # facet from the submitted content


def test_rejected_request_body_is_an_issue(client):
    client.post("/ingest", content=b"{not json", headers={"Content-Type": "application/json"})
    analyze(client)
    found = of_type(client, "invalid_record")
    assert len(found) == 1 and found[0]["subtype"] == "malformed_json"


# --------------------------------------------------------------------------- C. delays


def test_delay_threshold_and_per_source_limits(client):
    ingest(client, [
        ev(event_id="late", delay=600),
        ev(event_id="slightly-late", delay=60),
        ev(event_id="report-20min", source="operator_report", device="console", event_type="incident_report", delay=1200),
        ev(event_id="report-2h", source="operator_report", device="console", event_type="incident_report", delay=7200),
    ])
    analyze(client)
    found = {next(iter(evidence_ids(client, i))): i for i in of_type(client, "delayed_event")}
    assert set(found) == {"late", "report-2h"}
    d = detail(client, found["late"]["id"])
    assert d["details"]["delay_seconds"] == 600 and d["details"]["threshold_seconds"] == 300
    assert found["report-2h"]["severity"] == "medium"


# --------------------------------------------------------------------------- D. ordering


def test_out_of_order_arrival_same_device(client):
    ingest(client, [ev(event_id="newer", device="cam-1", at=300), ev(event_id="older", device="cam-1", at=120)])
    analyze(client)
    found = of_type(client, "out_of_order_arrival")
    assert len(found) == 1
    d = detail(client, found[0]["id"])
    roles = {e["external_event_id"]: e["role"] for e in d["evidence"]}
    assert roles == {"older": "late_arrival", "newer": "occurred_later_but_arrived_first"}
    assert "ingestion sequence per device" in d["detection_reason"]


def test_interleaved_devices_jitter_and_late_but_valid_events_are_not_out_of_order(client):
    ingest(client, [
        ev(device="cam-1", at=300), ev(device="cam-2", at=100),  # different devices interleave legitimately
        ev(device="cam-3", at=200), ev(device="cam-3", at=190),  # 10 s inversion is jitter
        ev(device="cam-4", at=0, delay=3600),  # very late, but alone: delayed, not out of order
    ])
    analyze(client)
    assert of_type(client, "out_of_order_arrival") == []
    assert len(of_type(client, "delayed_event")) == 1


def test_future_clock_does_not_make_every_later_event_out_of_order(client):
    ingest(client, [ev(device="cam-9", at=900, delay=-600)] + [ev(device="cam-9", at=400 + i * 10) for i in range(5)])
    analyze(client)
    assert of_type(client, "out_of_order_arrival") == []
    assert len(of_type(client, "timestamp_conflict", "received_before_occurred")) == 1


# --------------------------------------------------------------------------- E. timestamps


def test_received_before_occurred_respects_skew_tolerance(client):
    ingest(client, [ev(event_id="skewed", delay=-300), ev(event_id="tiny-skew", delay=-30)])
    analyze(client)
    found = of_type(client, "timestamp_conflict", "received_before_occurred")
    assert len(found) == 1 and evidence_ids(client, found[0]) == {"skewed"}


def radar(eid, at, track, speed=120, device="radar-int01"):
    return ev(event_id=eid, source="radar", device=device, event_type="speed_violation", at=at, location=INT01,
              payload={"track_id": track, "speed_kph": speed, "speed_limit_kph": 60})


def violation(eid, at, track, speed=120, plate="Q 11111"):
    return ev(event_id=eid, source="traffic_violation", device="tcam-int01", event_type="speeding", at=at, location=INT01,
              payload={"plate_number": plate, "radar_device_id": "radar-int01", "radar_track_id": track, "measured_speed_kph": speed,
                       "vehicle_make": "Toyota", "vehicle_colour": "white"})


def test_linked_radar_and_violation_disagree_on_time(client):
    ingest(client, [radar("r1", 1000, "T1"), violation("v1", 1000 - 420, "T1"),  # camera clock 7 min behind
                    radar("r2", 5000, "T2"), violation("v2", 5000.8, "T2")])  # consistent pass
    analyze(client)
    found = of_type(client, "timestamp_conflict", "linked_records_disagree")
    assert len(found) == 1 and evidence_ids(client, found[0]) == {"r1", "v1"}


def test_linked_speed_mismatch_is_a_traffic_measurement_conflict(client):
    ingest(client, [radar("r1", 0, "T7", speed=120), violation("v1", 0.8, "T7", speed=95)])
    analyze(client)
    found = of_type(client, "conflicting_sensor_results", "traffic_measurement")
    assert len(found) == 1


def test_unrelated_events_with_similar_timestamps_are_not_conflicts(client):
    ingest(client, [radar("r1", 0, "T1"), radar("r2", 0, "T2"), violation("v1", 0.5, "T9", plate="Q 22222")])  # T9 never measured
    analyze(client)
    assert of_type(client, "timestamp_conflict") == [] and of_type(client, "conflicting_sensor_results") == []


# --------------------------------------------------------------------------- F. locations


def plate_read(eid, at, device, location, plate="Q 12345", make="Toyota", colour="white", status="read", confidence=0.95):
    return ev(event_id=eid, source="anpr", device=device, event_type="plate_read", at=at, location=location, severity="informational",
              confidence=confidence, payload={"plate_number": plate, "vehicle_make": make, "vehicle_model": "Camry", "vehicle_colour": colour,
                                              "read_status": status})


def test_impossible_travel_is_a_location_conflict(client):
    ingest(client, [plate_read("a", 0, "anpr-int01", INT01), plate_read("b", 60, "anpr-int06", INT06)])
    analyze(client)
    found = of_type(client, "location_conflict")
    assert len(found) == 1
    d = detail(client, found[0]["id"])
    assert d["details"]["link_basis"] == "identical plate_number" and d["details"]["distance_km"] > 15
    assert d["details"]["implied_speed_kph"] > 900


def test_normal_vehicle_movement_and_gps_jitter_are_not_conflicts(client):
    near = {"latitude": INT01["latitude"] + 0.003, "longitude": INT01["longitude"] + 0.003, "zone": "Harbour-Rd-INT01"}
    ingest(client, [
        plate_read("a", 0, "anpr-int01", INT01),
        plate_read("b", 20 * 60, "anpr-int06", INT06),  # 19.5 km in 20 min = ~59 km/h
        ev(source="gps_fleet", device="gps-1", event_type="position_update", at=0, location=INT01, payload={"plate_number": "Z 1"}, severity="informational"),
        ev(source="gps_fleet", device="gps-1", event_type="position_update", at=20, location=near, payload={"plate_number": "Z 1"}, severity="informational"),
    ])
    analyze(client)
    assert of_type(client, "location_conflict") == []


# --------------------------------------------------------------------------- G. sensor results


def test_same_plate_different_vehicle_details(client):
    ingest(client, [plate_read("a", 0, "anpr-int01", INT01), plate_read("b", 4, "tcam-int01", INT01, make="Nissan", colour="black")])
    analyze(client)
    found = of_type(client, "conflicting_sensor_results", "vehicle_identification")
    assert len(found) == 1 and evidence_ids(client, found[0]) == {"a", "b"}
    assert detail(client, found[0]["id"])["uncertainty"]


def test_different_vehicles_at_the_same_gate_are_not_conflicts(client):
    ingest(client, [
        plate_read("a", 0, "anpr-gate-a", GATE, plate="Q 11111", make="Toyota", colour="white"),
        plate_read("b", 3, "anpr-gate-a", GATE, plate="Q 22222", make="Nissan", colour="black"),
        plate_read("c", 30, "anpr-gate-a", GATE, plate="Q 3??45", make="Lexus", status="partial", confidence=0.4),
    ])
    analyze(client)
    assert of_type(client, "conflicting_sensor_results") == [] and of_type(client, "location_conflict") == []


def badge(eid, at, holder, zone=GATE):
    return ev(event_id=eid, source="access_control", device="acs-gate-a", event_type="access_granted", at=at, location=zone,
              severity="informational", confidence=None, payload={"credential_id": f"CRD-{holder}", "holder_id": holder, "result": "granted"})


def face(eid, at, subject=None, status="matched", score=0.93, candidate=None):
    payload = {"match_status": status, "subject_id": subject, "match_score": score}
    if candidate:
        payload["candidate_subject_id"] = candidate
    return ev(event_id=eid, source="face_recognition", device="frs-gate-a", event_type=f"face_{status}", at=at, confidence=score, payload=payload)


def test_badge_and_face_name_different_people(client):
    ingest(client, [badge("b1", 0, "EMP-1"), face("f1", 1, "EMP-2")])
    analyze(client)
    found = of_type(client, "conflicting_sensor_results", "identity")
    assert len(found) == 1 and found[0]["severity"] == "high"
    assert evidence_ids(client, found[0]) == {"b1", "f1"}


def test_identity_negatives(client):
    ingest(client, [
        badge("b1", 0, "EMP-1"), face("f1", 1, "EMP-1"),  # same person: consistent
        badge("b2", 1000, "EMP-3"), badge("b3", 1001, "EMP-4"), face("f2", 1001.5, "EMP-4"),  # two badges: ambiguous pairing
        badge("b4", 2000, "EMP-5"), face("f3", 2030, "EMP-6"),  # 30 s apart: different moments
    ])
    analyze(client)
    assert of_type(client, "conflicting_sensor_results") == []


def test_weak_face_candidate_is_unresolved_not_a_conflict(client):
    ingest(client, [face("f1", 0, None, status="uncertain", score=0.55, candidate="EMP-7"), badge("b1", 40, "EMP-8")])
    analyze(client)
    assert of_type(client, "conflicting_sensor_results") == []
    found = of_type(client, "uncorrelatable_event", "contradictory_identifiers")
    assert len(found) == 1 and evidence_ids(client, found[0]) == {"f1", "b1"}


def test_perimeter_alarm_contradicted_by_camera(client):
    zone = {"zone": "Perimeter-North", "latitude": 25.2222, "longitude": 55.3305}
    ingest(client, [
        ev(event_id="alarm", source="perimeter_intrusion", device="pids-n1", event_type="fence_cut", severity="high", location=zone),
        ev(event_id="clear", source="cctv", device="cctv-n", event_type="scene_clear", at=6, severity="informational", location=zone,
           payload={"observation_type": "alarm_verification", "verification_result": "no_activity", "track_id": None}),
        ev(event_id="alarm2", source="perimeter_intrusion", device="pids-n1", event_type="fence_cut", at=5000, severity="high", location=zone),
        ev(event_id="person", source="cctv", device="cctv-n", event_type="person_detected", at=5010, location=zone, payload={"track_id": "x"}),
    ])
    analyze(client)
    found = of_type(client, "conflicting_sensor_results", "sensor_outcome")
    assert len(found) == 1 and evidence_ids(client, found[0]) == {"alarm", "clear"}


# --------------------------------------------------------------------------- H / I. repetition and spikes


def test_repeated_alerts_vs_routine_telemetry(client):
    events = [ev(device="cam-rep", event_type="motion_detected", at=i * 60) for i in range(12)]
    events += [ev(source="device_health", device="cam-hb", event_type="heartbeat", at=i * 30, severity="informational",
                  payload={"status": "online"}) for i in range(30)]
    events += [plate_read(f"p{i}", i * 20, "anpr-int01", INT01, plate=f"Q {10000 + i}") for i in range(20)]
    ingest(client, events)
    analyze(client)
    found = of_type(client, "repeated_device_events")
    assert len(found) == 1 and found[0]["devices"] == ["cam-rep"] and found[0]["event_count"] == 12


def test_volume_spike_against_baseline(client):
    background = [ev(source="network_security", device="dc-auth", event_type="auth_failure", at=i * 600, location={"zone": "SOC-Network"})
                  for i in range(300)]  # one per 10 minutes for ~2 days
    burst = [ev(source="network_security", device="dc-auth", event_type="auth_failure", at=86400 + 5 + i * 6, location={"zone": "SOC-Network"})
             for i in range(30)]
    ingest(client, background + burst)
    analyze(client)
    found = of_type(client, "event_spike")
    assert found, "burst not detected"
    d = detail(client, found[0]["id"])
    assert d["details"]["peak_in_window"] >= 25 and d["details"]["expected_in_window"] < 5
    assert "not by itself evidence of an incident" in d["description"]


def test_steady_traffic_is_not_a_spike(client):
    ingest(client, [ev(source="network_security", device="dc-auth", event_type="auth_failure", at=i * 600, location={"zone": "SOC-Network"})
                    for i in range(300)])
    analyze(client)
    assert of_type(client, "event_spike") == []


# --------------------------------------------------------------------------- J. device health


def health(eid, at, event_type, status, code=None, device="tcam-int02"):
    return ev(event_id=eid, source="device_health", device=device, event_type=event_type, at=at, severity="medium", confidence=None,
              location=INT01, payload={"status": status, "monitor": "nms-01", "diagnostic_code": code})


def test_device_lifecycle_degraded_failed_offline_recovered(client):
    ingest(client, [
        health("h1", 0, "device_degraded", "degraded", "W-TEMP-HIGH"),
        health("h2", 600, "sensor_failure", "fault", "E-CAM-VIDEO-LOSS"),
        health("h3", 1200, "device_offline", "offline", "E-HEARTBEAT-LOST"),
        health("h4", 5000, "device_recovered", "online"),
    ])
    analyze(client)
    for issue_type in ("device_degraded", "sensor_failure", "device_offline", "device_recovered"):
        assert len(of_type(client, issue_type)) == 1, issue_type
    offline = detail(client, of_type(client, "device_offline")[0]["id"])
    assert offline["severity"] == "high" and offline["details"]["recovered"] is True and offline["details"]["downtime_seconds"] == 3800
    assert offline["details"]["affected_device_id"] == "tcam-int02" and offline["details"]["reporting_monitor"] == "nms-01"
    assert offline["details"]["current_device_state"]["latest_status"] == "recovered"
    assert {e["role"] for e in offline["evidence"]} == {"offline_report", "recovery_report"}


def test_offline_without_recovery_is_critical(client):
    ingest(client, [health("h1", 0, "device_offline", "offline")])
    analyze(client)
    found = of_type(client, "device_offline")
    assert found[0]["severity"] == "critical"
    assert detail(client, found[0]["id"])["missing_evidence"] == "No recovery report after the outage."


def test_health_report_about_aliased_camera_stays_device_health(client):
    """Regression: the camera firmware reports 'red_light_violation'; the health record about it is device_health."""
    ingest(client, [ev(source="red_light_violation", device="tcam-int02", event_type="red_light_violation", location=INT01),
                    health("h1", 60, "device_offline", "offline")])
    analyze(client)
    issue = of_type(client, "device_offline")[0]
    assert issue["sources"] == ["device_health"] and issue["devices"] == ["tcam-int02"]
    event = detail(client, issue["id"])["evidence"][0]["event"]
    assert event["source_type"] == "device_health"


def test_silence_is_not_an_outage(client):
    ingest(client, [ev(device="cam-quiet", at=0), ev(device="cam-quiet", at=2 * 86400)])  # two days without events
    analyze(client)
    assert of_type(client, "device_offline") == []


# --------------------------------------------------------------------------- K. low confidence


def test_low_confidence_thresholds_per_source(client):
    ingest(client, [
        face("face-low", 0, "EMP-1", score=0.55),  # below 0.6
        plate_read("anpr-ok", 0, "anpr-int01", INT01, confidence=0.62),  # above 0.6
        plate_read("anpr-partial", 10, "anpr-int01", INT01, plate="Q 1??45", status="partial", confidence=0.9),
        ev(event_id="cctv-ok", source="cctv", confidence=0.55),  # cctv threshold 0.5
        ev(event_id="cctv-low", source="cctv", confidence=0.2, severity="high"),
    ])
    analyze(client)
    found = {next(iter(evidence_ids(client, i))): i for i in of_type(client, "low_confidence_detection")}
    assert set(found) == {"face-low", "anpr-partial", "cctv-low"}
    assert found["cctv-low"]["severity"] == "medium"  # high-severity alert on weak evidence
    assert "not that the detection is false" in found["face-low"]["description"]


# --------------------------------------------------------------------------- L. linkage


def test_uncorrelatable_criteria(client):
    ingest(client, [
        plate_read("no-plate", 0, "anpr-int01", INT01, plate=None, status="unreadable", confidence=0.1),
        ev(event_id="no-track", source="cctv", event_type="person_detected", payload={"track_id": None}),
        ev(event_id="no-location", source="network_security", device="ids-1", event_type="ids_alert", location=None, severity="medium"),
        ev(event_id="lonely-report", source="operator_report", device="console", event_type="incident_report", at=10000,
           location={"zone": "Perimeter-East"}, payload={"report_category": "suspicious_activity", "summary": "vague"}),
        ev(event_id="backed-report", source="operator_report", device="console", event_type="incident_report", at=20000,
           location={"zone": "Gate-B"}, payload={"report_category": "access_violation"}),
        ev(source="access_control", device="acs-b", event_type="access_denied", at=19800, location={"zone": "Gate-B"}, severity="medium"),
        ev(event_id="shift", source="operator_report", device="console", event_type="shift_note", at=30000, location={"zone": "Gate-A"}),
        plate_read("normal", 40000, "anpr-int01", INT01),
    ])
    analyze(client)
    found = {}
    for issue in of_type(client, "uncorrelatable_event"):
        for eid in evidence_ids(client, issue):
            found[eid] = issue["subtype"]
    assert found == {"no-plate": "missing_plate", "no-track": "missing_track", "no-location": "missing_location",
                     "lonely-report": "uncorroborated_report"}


# --------------------------------------------------------------------------- clean data


def test_ordinary_activity_produces_no_issues(client):
    events = []
    for i in range(40):
        events.append(plate_read(f"p{i}", i * 400, "anpr-int01", INT01, plate=f"Q {20000 + i}"))
        events.append(badge(f"b{i}", i * 400 + 100, f"EMP-{i}"))
        events.append(face(f"f{i}", i * 400 + 101, f"EMP-{i}"))
        events.append(ev(source="device_health", device="anpr-int01", event_type="heartbeat", at=i * 400 + 50, severity="informational",
                         payload={"status": "online", "cpu_temp_c": 50, "packet_loss_pct": 0.2}))
    ingest(client, events)
    run = analyze(client)
    assert run["issues_total"] == 0, issues(client)


@pytest.mark.parametrize("rule_id", ["DUP-001", "VAL-001", "DLY-001", "ORD-001", "TSC-001", "TSC-002", "LOC-001", "SNS-001", "SNS-002",
                                     "SNS-003", "REP-001", "SPK-001", "HLT-001", "LCF-001", "UNC-001", "UNC-002", "UNC-003"])
def test_every_rule_runs_and_reports_timing(client, rule_id):
    ingest(client, [ev()])
    run = analyze(client)
    assert rule_id in run["details"]["rules"]
