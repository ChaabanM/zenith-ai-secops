"""Incident correlation: grouping, separation, conflicts, confidence and reprocessing."""

from datetime import datetime

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import Event
from tests.test_quality_rules import GATE, INT01, INT06, analyze, badge, ev, face, health, ingest, plate_read, radar, violation

NORTH = {"zone": "Perimeter-North", "latitude": 25.2222, "longitude": 55.3305}


def correlate(client):
    response = client.post("/incidents/correlate")
    assert response.status_code == 200, response.text
    return response.json()


def incidents(client, **params):
    return client.get("/incidents", params={"page_size": 500, **params}).json()["items"]


def detail(client, incident_id):
    return client.get(f"/incidents/{incident_id}").json()


def members(client, incident) -> set[str]:
    return {t["event_id"] for t in detail(client, incident["id"])["timeline"]}


def phone(eid, at, plate="Q 11111", zone=INT01):
    return ev(event_id=eid, source="mobile_phone_detection", device="tcam-int01", event_type="phone_use_detected", at=at, location=zone,
              severity="medium", confidence=0.9, payload={"plate_number": plate, "vehicle_make": "Toyota", "vehicle_colour": "white"})


def camera(eid, at, colour="white", category="sedan", confidence=0.85, zone=INT01, device="cctv-int01"):
    return ev(event_id=eid, source="cctv", device=device, event_type="vehicle_detected", at=at, location=zone, severity="informational",
              confidence=confidence, payload={"observation_type": "vehicle_detected", "object_category": category, "track_id": "x",
                                              "attributes": {"colour": colour}})


def denial(eid, at, credential="CRD-1", zone=GATE, device="acs-gate-a"):
    return ev(event_id=eid, source="access_control", device=device, event_type="access_denied", at=at, location=zone, severity="medium",
              confidence=None, payload={"credential_id": credential, "holder_id": None, "door_id": f"{zone['zone']}-D1", "result": "denied",
                                        "reason": "credential_revoked"})


def alarm(eid, at, segment="N-05", event_type="fence_cut", zone=NORTH):
    return ev(event_id=eid, source="perimeter_intrusion", device="pids-n1", event_type=event_type, at=at, location=zone, severity="high",
              confidence=0.9, payload={"sensor_segment": segment, "alarm_type": event_type})


def person(eid, at, confidence=0.85, zone=NORTH):
    return ev(event_id=eid, source="cctv", device="cctv-n", event_type="person_detected", at=at, location=zone, confidence=confidence,
              payload={"observation_type": "person_detected", "object_category": "person", "track_id": "t1"})


def report(eid, at, category, zone, refs=None, priority="high"):
    payload = {"report_category": category, "summary": "note", "priority": priority}
    if refs:
        payload["referenced_event_ids"] = refs
    return ev(event_id=eid, source="operator_report", device="soc-console-01", event_type="incident_report", at=at, location=zone,
              severity="medium", confidence=None, payload=payload)


def pass_events(prefix="p", at=0.0, plate="Q 11111", track="T1"):
    return [radar(f"{prefix}-radar", at, track), violation(f"{prefix}-viol", at + 0.8, track, plate=plate),
            phone(f"{prefix}-phone", at + 1.2, plate), plate_read(f"{prefix}-anpr", at + 2.1, "anpr-int01", INT01, plate=plate, make="Toyota"),
            camera(f"{prefix}-cam", at + 3.0)]


# --------------------------------------------------------------------------- grouping


def test_multi_sensor_traffic_pass_is_one_incident(client):
    ingest(client, pass_events())
    correlate(client)
    found = incidents(client)
    assert len(found) == 1 and found[0]["category"] == "traffic_enforcement"
    d = detail(client, found[0]["id"])
    roles = {t["event_id"]: (t["link_type"], t["core"]) for t in d["timeline"]}
    assert roles == {"p-radar": ("explicit_reference", True), "p-viol": ("anchor", True), "p-phone": ("same_plate_same_site", True),
                     "p-anpr": ("same_plate_same_site", True), "p-cam": ("appearance_match", False)}
    times = [datetime.fromisoformat(t["occurred_at"].replace("Z", "+00:00")) for t in d["timeline"]]
    assert times == sorted(times)
    assert "Q 11111" in d["title"] and d["severity"] == "medium"  # phone detection (medium) is the highest strongly linked severity
    assert all(t["linkage_explanation"] for t in d["timeline"])
    assert any("no plate" in t["linkage_explanation"] for t in d["timeline"] if t["event_id"] == "p-cam")
    for section in ("observations", "inferences", "missing_information", "recommended_actions"):
        assert isinstance(d[section], list)
    assert d["confidence_reason"].startswith("Heuristic score (not a probability)")


def test_different_vehicles_at_the_same_site_stay_separate(client):
    ingest(client, pass_events() + [plate_read("bystander", 8, "anpr-int01", INT01, plate="Q 22222", make="Nissan", colour="black")])
    correlate(client)
    found = incidents(client)
    assert len(found) == 1 and "bystander" not in members(client, found[0])


def test_single_uncorroborated_violation_is_not_an_incident(client):
    ingest(client, [violation("v", 0, "T9", plate="Q 33333"), plate_read("other", 5, "anpr-int01", INT01, plate="Q 44444")])
    correlate(client)
    assert incidents(client) == []


def test_same_plate_at_different_times_are_separate_incidents(client):
    ingest(client, pass_events("a", 0, track="T1") + pass_events("b", 7200, track="T2"))
    correlate(client)
    found = incidents(client)
    assert len(found) == 2
    assert {e for i in found for e in members(client, i)} == {f"{p}-{k}" for p in "ab" for k in ("radar", "viol", "phone", "anpr", "cam")}
    assert all(len(members(client, i)) == 5 for i in found)


def test_same_device_distinct_vehicles(client):
    ingest(client, [radar("r1", 0, "T1"), violation("v1", 0.8, "T1", plate="Q 11111"),
                    radar("r2", 180, "T2"), violation("v2", 180.8, "T2", plate="Q 22222")])
    correlate(client)
    found = incidents(client)
    assert sorted(sorted(members(client, i)) for i in found) == [["r1", "v1"], ["r2", "v2"]]


def test_conflicting_radar_reference_is_kept_with_conflict(client):
    ingest(client, [radar("r1", 1000, "T1"), violation("v1", 1000 - 420, "T1")])
    correlate(client)
    d = detail(client, incidents(client)[0]["id"])
    assert d["title"].startswith("Conflicting records")
    assert any("disagree on the time" in c for c in d["conflicts"])
    link = next(t for t in d["timeline"] if t["event_id"] == "r1")
    assert link["conflict"] and link["linkage_confidence"] < 0.95


def test_impossible_travel_never_merges_and_is_surfaced(client):
    ingest(client, pass_events("a", 0) + [plate_read("far", 60, "anpr-int06", INT06, plate="Q 11111", make="Toyota")])
    correlate(client)
    found = incidents(client)
    assert len(found) == 1
    d = detail(client, found[0]["id"])
    far = next(t for t in d["timeline"] if t["event_id"] == "far")
    assert far["role"] == "contradicting_observation" and "km apart" in far["conflict"]
    assert any("km apart" in c for c in d["conflicts"])


def test_plausible_onward_movement_is_linked(client):
    ingest(client, pass_events("a", 0) + [plate_read("later", 25 * 60, "anpr-int06", INT06, plate="Q 11111", make="Toyota")])
    correlate(client)
    d = detail(client, incidents(client)[0]["id"])
    assert next(t for t in d["timeline"] if t["event_id"] == "later")["link_type"] == "plausible_movement"


# --------------------------------------------------------------------------- weak chains / ambiguity / low confidence


def test_shared_context_does_not_chain_two_incidents(client):
    loiter = ev(event_id="loiter", source="cctv", device="cctv-gate-a", event_type="loitering_detected", at=200, location=GATE, severity="medium",
                payload={"observation_type": "loitering_detected", "object_category": "person", "track_id": "t"})
    ingest(client, [denial(f"a{i}", i * 20, "CRD-A") for i in range(3)] + [denial(f"b{i}", 30 + i * 20, "CRD-B") for i in range(3)] + [loiter])
    correlate(client)
    found = incidents(client, category="unauthorized_access")
    assert sorted(sorted(members(client, i)) for i in found) == [["a0", "a1", "a2"], ["b0", "b1", "b2"]]  # loitering ambiguous: unattached


def test_two_alarms_sharing_a_camera_event_are_not_merged(client):
    ingest(client, [alarm("al1", 0, "N-01"), alarm("al2", 50, "N-09"), person("cam", 55)])  # camera inside both alarms' windows
    correlate(client)
    found = incidents(client, category="perimeter_intrusion")
    assert sorted(sorted(members(client, i)) for i in found) == [["al1"], ["al2"]]


def test_vague_report_is_not_forced_into_an_incident(client):
    ingest(client, [alarm("al", 0), report("vague", 300, "suspicious_activity", NORTH, priority="medium")])
    correlate(client)
    found = incidents(client)
    assert len(found) == 1 and members(client, found[0]) == {"al"}


def test_low_confidence_detection_does_not_become_context(client):
    ingest(client, [alarm("al", 0), person("weak", 20, confidence=0.3), person("strong", 25, confidence=0.9)])
    correlate(client)
    d = detail(client, incidents(client)[0]["id"])
    assert {t["event_id"] for t in d["timeline"]} == {"al", "strong"}


def test_partial_plate_is_never_an_identifier(client):
    ingest(client, pass_events() + [plate_read("partial", 4, "anpr-int01", INT01, plate="Q 1??11", status="partial", confidence=0.4)])
    correlate(client)
    assert "partial" not in members(client, incidents(client)[0])


def test_identity_conflict_requires_unambiguous_pairing(client):
    ingest(client, [badge("b1", 0, "EMP-1"), face("f1", 1, "EMP-2"),
                    badge("b2", 1000, "EMP-3"), badge("b3", 1001, "EMP-4"), face("f2", 1001.5, "EMP-5")])
    correlate(client)
    found = incidents(client)
    assert len(found) == 1 and members(client, found[0]) == {"b1", "f1"}
    d = detail(client, found[0]["id"])
    assert d["severity"] == "medium" and d["conflicts"] and "Which identity is correct" in d["missing_information"][0]


# --------------------------------------------------------------------------- patterns


def test_access_attempts_door_and_referenced_report(client):
    ingest(client, [denial(f"d{i}", i * 25) for i in range(4)]
           + [ev(event_id="door", source="access_control", device="acs-gate-a", event_type="door_forced_open", at=150, location=GATE,
                 severity="critical", confidence=None, payload={"door_id": "Gate-A-D1", "result": "alarm"}),
              report("rep", 400, "access_violation", GATE, refs=["d0"])])
    correlate(client)
    found = incidents(client)
    assert len(found) == 1
    d = detail(client, found[0]["id"])
    assert members(client, found[0]) == {"d0", "d1", "d2", "d3", "door", "rep"}
    assert d["severity"] == "critical" and "door forced open" in d["title"]
    assert next(t for t in d["timeline"] if t["event_id"] == "rep")["link_type"] == "explicit_reference"


def test_perimeter_alarm_with_panel_camera_and_report(client):
    panel = ev(event_id="panel", source="emergency_alarm", device="alarm-panel-main", event_type="intrusion_alarm", at=20, location=NORTH,
               severity="critical", confidence=None, payload={"alarm_type": "intrusion", "input_point": "PIDS-N-05", "state": "active"})
    ingest(client, [alarm("al", 0), person("cam", 15), panel, report("rep", 240, "perimeter", NORTH)])
    correlate(client)
    found = incidents(client)
    assert len(found) == 1 and members(client, found[0]) == {"al", "cam", "panel", "rep"}
    assert found[0]["severity"] == "critical"


def test_device_failure_episode(client):
    low = ev(event_id="weak-out", source="radar", device="tcam-int02", event_type="speed_measurement", at=120, confidence=0.35, location=INT01)
    ingest(client, [health("h1", 0, "device_degraded", "degraded"), low, health("h2", 600, "sensor_failure", "fault"),
                    health("h3", 1200, "device_offline", "offline")])
    correlate(client)
    found = incidents(client)
    assert len(found) == 1 and found[0]["category"] == "device_failure" and found[0]["severity"] == "critical"
    d = detail(client, found[0]["id"])
    assert next(t for t in d["timeline"] if t["event_id"] == "weak-out")["link_type"] == "affected_output"
    assert "No recovery report has been received." in d["missing_information"]
    ingest(client, [health("h4", 5000, "device_recovered", "online")])
    correlate(client)
    again = incidents(client)
    assert [i["id"] for i in again] == [found[0]["id"]] and again[0]["severity"] == "high"


def test_auth_burst_with_success_is_cyber_incident(client):
    zone = {"zone": "SOC-Network"}
    fails = [ev(event_id=f"f{i}", source="network_security", device="dc-auth", event_type="auth_failure", at=i * 5, location=zone,
                payload={"src_ip": "203.0.113.9", "username": "emp1"}) for i in range(12)]
    ok = ev(event_id="ok", source="network_security", device="dc-auth", event_type="auth_success", at=80, location=zone, severity="high",
            payload={"src_ip": "203.0.113.9", "username": "emp1"})
    ingest(client, fails + [ok])
    correlate(client)
    found = incidents(client)
    assert len(found) == 1 and found[0]["category"] == "cyber_security" and found[0]["severity"] == "high" and found[0]["event_count"] == 13


def test_routine_activity_creates_no_incidents(client):
    events = [plate_read(f"p{i}", i * 300, "anpr-int01", INT01, plate=f"Q {10000 + i}") for i in range(20)]
    events += [badge(f"b{i}", i * 300 + 50, f"EMP-{i}") for i in range(20)] + [face(f"f{i}", i * 300 + 51, f"EMP-{i}") for i in range(20)]
    events += [denial("single", 9000)]
    ingest(client, events)
    run = correlate(client)
    assert run["incidents_active"] == 0


# --------------------------------------------------------------------------- duplicates, ordering, confidence


def test_duplicates_are_not_counted_as_corroboration(client):
    events = pass_events()
    ingest(client, events)
    correlate(client)
    before = detail(client, incidents(client)[0]["id"])
    for _ in range(3):
        ingest(client, [events[1]])
    correlate(client)
    after = detail(client, incidents(client)[0]["id"])
    assert after["event_count"] == before["event_count"] == 5
    assert after["details"]["duplicate_submissions_ignored"] == 3
    assert (after["severity"], after["confidence"]) == (before["severity"], before["confidence"])


def test_out_of_order_and_late_evidence_preserve_identity_and_status(client):
    events = pass_events()
    ingest(client, [events[3], events[1]])  # anpr first, then the earlier violation
    correlate(client)
    first = incidents(client)[0]
    client.patch(f"/incidents/{first['id']}", json={"status": "investigating", "note": "checking images"})
    ingest(client, [events[4], events[0], events[2]])  # late camera, radar and phone evidence
    run = correlate(client)
    assert (run["created"], run["updated"]) == (0, 1)
    after = detail(client, first["id"])
    assert after["event_count"] == 5 and after["status"] == "investigating" and after["status_note"] == "checking images"
    assert after["detected_at"] == first["detected_at"] and after["updated_at"] >= first["updated_at"]
    assert [t["event_id"] for t in after["timeline"]] == ["p-radar", "p-viol", "p-phone", "p-anpr", "p-cam"]


def test_merge_policy_keeps_history(client):
    ingest(client, [violation("v", 0, "T1", plate="Q 11111"), plate_read("a1", 10, "anpr-int01", INT01, plate="Q 11111", make="Toyota"),
                    phone("ph", 200), plate_read("a2", 210, "anpr-int01", INT01, plate="Q 11111", make="Toyota")])
    correlate(client)
    before = sorted(incidents(client), key=lambda i: i["first_occurred_at"])
    assert len(before) == 2
    client.patch(f"/incidents/{before[1]['id']}", json={"status": "acknowledged"})
    ingest(client, [plate_read("bridge", 110, "anpr-int02", INT01, plate="Q 11111", make="Toyota")])  # links both passes
    run = correlate(client)
    assert run["merged"] == 1
    after = incidents(client)
    assert [i["id"] for i in after] == [before[0]["id"]] and after[0]["event_count"] == 5
    absorbed = detail(client, before[1]["id"])
    assert absorbed["correlation_state"] == "merged" and absorbed["superseded_by_id"] == before[0]["id"]
    assert absorbed["status"] == "acknowledged"
    assert detail(client, before[0]["id"])["merged_incident_ids"] == [before[1]["id"]]
    assert len(incidents(client, include_inactive=True)) == 2


def test_conflicts_lower_confidence(client):
    ingest(client, [radar("r1", 0, "T1"), violation("v1", 0.8, "T1", plate="Q 11111"),
                    radar("r2", 9000, "T2"), violation("v2", 9000 - 400, "T2", plate="Q 22222")])
    correlate(client)
    by_plate = {("Q 11111" in i["title"]): i for i in incidents(client)}
    assert by_plate[False]["confidence"] < by_plate[True]["confidence"]
    assert by_plate[False]["conflict_count"] == 1 and by_plate[True]["conflict_count"] == 0


def test_quality_findings_annotate_but_do_not_drive_incidents(client):
    ingest(client, pass_events() + [ev(event_id="late-alone", delay=3600)])
    analyze(client)
    correlate(client)
    found = incidents(client)
    assert len(found) == 1  # the delayed event is a quality issue, not an incident
    assert isinstance(detail(client, found[0]["id"])["quality_issues"], list)


# --------------------------------------------------------------------------- reprocessing and evidence integrity


def test_reprocessing_is_idempotent_and_source_events_are_untouched(client):
    ingest(client, pass_events() + [alarm("al", 500)] + [denial(f"d{i}", 900 + i * 20) for i in range(3)])
    with SessionLocal() as s:
        snapshot = [tuple(r) for r in s.execute(select(Event.id, Event.event_id, Event.occurred_at, Event.payload, Event.content_sha256))]
    first = correlate(client)
    ids = sorted(i["id"] for i in incidents(client))
    second = correlate(client)
    assert (second["created"], second["updated"], second["merged"], second["obsoleted"]) == (0, 0, 0, 0)
    assert second["unchanged"] == first["incidents_active"] == 3
    assert sorted(i["id"] for i in incidents(client)) == ids
    with SessionLocal() as s:
        assert [tuple(r) for r in s.execute(select(Event.id, Event.event_id, Event.occurred_at, Event.payload, Event.content_sha256))] == snapshot
    for incident in incidents(client):
        for t in detail(client, incident["id"])["timeline"]:
            event = client.get(f"/events/{t['event_pk']}").json()
            assert event["event_id"] == t["event_id"] and event["submission"]["id"] == t["submission_id"]
