"""GET /briefing: periods, time boundaries, grounding, prioritization, sections and validation.

T0 is 2026-01-15T10:00Z, inside the default UTC day shift (06:00-14:00).
"""

from datetime import UTC, datetime

import pytest

from tests.test_correlation import alarm, correlate, denial, pass_events, radar, violation
from tests.test_quality_rules import GATE, analyze, ev, health, ingest

DAY_SHIFT = {"mode": "shift", "shift": "day", "date": "2026-01-15"}
BOUNDARY = {"latitude": 25.3, "longitude": 55.3, "zone": "Boundary-Zone"}


def brief(client, **params):
    response = client.get("/briefing", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def section(b, key):
    return next(s for s in b["sections"] if s["key"] == key)


def events_between(client, start, end, **filters) -> int:
    return client.get("/events", params={"occurred_from": start, "occurred_to": end, "page_size": 1, **filters}).json()["total"]


@pytest.fixture
def scenario(client):
    first = (
        pass_events("a", 0)  # 10:00 traffic incident (5 sensors)
        + [alarm("al", 1800)]  # 10:30 perimeter alarm
        + [denial(f"d{i}", 9000 + i * 20) for i in range(3)]  # 12:30 access attempts
        + [health("h1", 10000, "device_offline", "offline", device="radar-int05")]  # device failure, no recovery
        + [ev(event_id="prev-evening", at=-13 * 3600)]  # 21:00 the previous day
        + [ev(event_id="edge-in", at=4 * 3600 - 1, location=BOUNDARY), ev(event_id="edge-out", at=4 * 3600, location=BOUNDARY)]
    )
    ingest(client, first)
    # a repeated submission of an existing event, and a late event that arrives in a later batch
    ingest(client, [next(e for e in first if e["event_id"] == "al"),
                    ev(event_id="late-1", at=600, delay=7200, device="cam-late")])
    analyze(client)
    correlate(client)
    return client


# --------------------------------------------------------------------------- structure and grounding


def test_shift_briefing_has_every_required_part_and_is_grounded(scenario):
    b = brief(scenario, **DAY_SHIFT)
    assert b["period"]["start"] == "2026-01-15T06:00:00Z" and b["period"]["end"] == "2026-01-15T14:00:00Z"
    assert "occurrence time" in b["period"]["time_basis"]
    assert [s["key"] for s in b["sections"]] == ["incident_activity", "repeated_by_device", "repeated_by_location", "device_health",
                                                 "trends", "data_quality", "conflicts_uncertainty"]
    for key in ("executive_summary", "priority_incidents", "unresolved_incidents", "new_incidents", "recommended_actions", "confidence",
                "evidence_index", "incident_ids", "quality_issue_ids", "grounding", "generated_at"):
        assert key in b
    assert b["grounding"]["all_references_verified"] and b["grounding"]["unverified_references"] == []
    # every referenced event exists with exactly that external id
    for ref in b["evidence_index"]:
        assert scenario.get(f"/events/{ref['event_pk']}").json()["event_id"] == ref["event_id"]
    for iid in b["incident_ids"]:
        assert scenario.get(f"/incidents/{iid}").status_code == 200
    for qid in b["quality_issue_ids"]:
        assert scenario.get(f"/quality-issues/{qid}").status_code == 200
    # findings reference only ids that are also in the index
    index = {r["event_id"] for r in b["evidence_index"]}
    for s in b["sections"]:
        for f in s["findings"]:
            assert f["basis"] in ("database_fact", "observed", "inference", "conflict", "missing", "recommendation")
            assert {r["event_id"] for r in f["evidence"]} <= index
            assert set(f["incident_ids"]) <= set(b["incident_ids"]) and set(f["quality_issue_ids"]) <= set(b["quality_issue_ids"])


def test_counts_come_from_the_database(scenario):
    b = brief(scenario, **DAY_SHIFT)
    assert b["key_metrics"]["events_total"] == events_between(scenario, "2026-01-15T06:00:00Z", "2026-01-15T14:00:00Z")
    by_source = b["key_metrics"]["events_by_source"]
    assert by_source["traffic_violation"] == 1 and by_source["radar"] == 1
    assert b["key_metrics"]["incidents_active"] == len({*b["incident_ids"]} & {i["id"] for i in scenario.get("/incidents", params={"page_size": 100}).json()["items"]})
    assert b["executive_summary"]["generated_by"] == "deterministic_template"
    assert f"{b['key_metrics']['events_total']} events" in b["executive_summary"]["text"]


# --------------------------------------------------------------------------- time windows


def test_shift_boundaries_are_half_open(scenario):
    day = brief(scenario, zone="Boundary-Zone", **DAY_SHIFT)
    evening = brief(scenario, mode="shift", shift="evening", date="2026-01-15", zone="Boundary-Zone")
    assert day["key_metrics"]["events_total"] == 1 and evening["key_metrics"]["events_total"] == 1
    assert [r["event_id"] for r in day["evidence_index"]] == ["edge-in"]
    assert [r["event_id"] for r in evening["evidence_index"]] == ["edge-out"]


def test_adjacent_windows_add_up_and_overlaps_are_consistent(scenario):
    total = lambda **p: brief(scenario, **p)["key_metrics"]["events_total"]  # noqa: E731
    day, evening = total(**DAY_SHIFT), total(mode="shift", shift="evening", date="2026-01-15")
    assert day + evening == total(mode="custom", start="2026-01-15T06:00:00Z", end="2026-01-15T22:00:00Z")
    a = total(mode="custom", start="2026-01-15T09:00:00Z", end="2026-01-15T11:00:00Z")
    b = total(mode="custom", start="2026-01-15T11:00:00Z", end="2026-01-15T14:00:00Z")
    assert a + b == total(mode="custom", start="2026-01-15T09:00:00Z", end="2026-01-15T14:00:00Z")
    overlap = total(mode="custom", start="2026-01-15T10:00:00Z", end="2026-01-15T11:00:00Z")
    assert overlap <= a and overlap == events_between(scenario, "2026-01-15T10:00:00Z", "2026-01-15T11:00:00Z")


def test_daily_briefing_and_previous_shift_separation(scenario):
    day = brief(scenario, mode="day", date="2026-01-15")
    assert day["period"]["start"] == "2026-01-15T00:00:00Z" and day["period"]["end"] == "2026-01-16T00:00:00Z"
    assert "prev-evening" not in {r["event_id"] for r in day["evidence_index"]}
    prev = brief(scenario, mode="shift", shift="evening", date="2026-01-14")
    assert prev["key_metrics"]["events_total"] == 1


def test_night_shift_crosses_midnight_and_time_zone_is_applied(scenario):
    night = brief(scenario, mode="shift", shift="night", date="2026-01-14")
    assert night["period"]["start"] == "2026-01-14T22:00:00Z" and night["period"]["end"] == "2026-01-15T06:00:00Z"
    dubai = brief(scenario, mode="day", date="2026-01-15", tz="Asia/Dubai")
    assert dubai["period"]["start"] == "2026-01-14T20:00:00Z" and dubai["period"]["local_start"].endswith("+04:00")
    assert dubai["period"]["timezone"] == "Asia/Dubai"


def test_late_and_out_of_order_events_count_by_occurrence_time(scenario):
    b = brief(scenario, **DAY_SHIFT)
    assert scenario.get("/events", params={"event_id": "late-1"}).json()["total"] == 1
    window = brief(scenario, mode="custom", start="2026-01-15T10:09:00Z", end="2026-01-15T10:11:00Z")
    assert window["key_metrics"]["events_total"] == 1  # counted in the window it occurred in, though it arrived 2 h later
    assert "late-1" in {r["event_id"] for r in window["evidence_index"]}
    assert window["key_metrics"]["events_received_after_period_end"] == 1
    delayed = [f for f in section(b, "data_quality")["findings"] if f["statement"].startswith("Delayed events")]
    assert delayed and delayed[0]["quality_issue_ids"]
    for qid in delayed[0]["quality_issue_ids"]:
        assert scenario.get(f"/quality-issues/{qid}").json()["issue_type"] == "delayed_event"


def test_repeated_submissions_are_not_counted_twice(scenario):
    b = brief(scenario, **DAY_SHIFT)
    assert b["key_metrics"]["duplicate_submissions_ignored"] == 1
    assert b["key_metrics"]["events_total"] == events_between(scenario, "2026-01-15T06:00:00Z", "2026-01-15T14:00:00Z")
    dup = [f for f in section(b, "data_quality")["findings"] if "repeated submission" in f["statement"]]
    assert dup and "counted once" in dup[0]["statement"]


def test_empty_period_says_so_without_filler(scenario):
    b = brief(scenario, mode="day", date="2025-06-01")
    assert b["key_metrics"]["events_total"] == 0 and b["priority_incidents"] == [] and b["evidence_index"] == []
    assert "not confirmation that operations were normal" in b["executive_summary"]["text"]
    assert "not that the period was confirmed safe" in section(b, "incident_activity")["empty_reason"]
    assert b["confidence"]["level"] == "low"
    assert b["recommended_actions"][0]["statement"].startswith("No management action is indicated")


def test_default_period_is_the_latest_mostly_covered_shift(scenario):
    b = brief(scenario)
    assert b["period"]["mode"] == "shift" and b["period"]["default_reason"]
    latest = datetime.fromisoformat(b["key_metrics"]["latest_stored_event_at"])
    start = datetime.fromisoformat(b["period"]["start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(b["period"]["end"].replace("Z", "+00:00"))
    assert start <= latest and (latest - start) >= (end - start) / 2 or latest >= end


# --------------------------------------------------------------------------- incidents and prioritization


def test_priority_ranking_is_explained_deterministic_and_never_changes_severity(scenario):
    b1, b2 = brief(scenario, **DAY_SHIFT), brief(scenario, **DAY_SHIFT)
    assert [x["incident_id"] for x in b1["priority_incidents"]] == [x["incident_id"] for x in b2["priority_incidents"]]
    scores = [x["priority_score"] for x in b1["priority_incidents"]]
    assert scores == sorted(scores, reverse=True)
    for x in b1["priority_incidents"]:
        stored = scenario.get(f"/incidents/{x['incident_id']}").json()
        assert x["severity"] == stored["severity"] and x["status"] == stored["status"]
        assert x["priority_score"] == pytest.approx(sum(f["points"] for f in x["factors"]), abs=0.11)
        assert x["factors"][0]["factor"] == "severity" and x["reason"]
        assert all(scenario.get(f"/events/{r['event_pk']}").json()["event_id"] == r["event_id"] for r in x["evidence"])
    top = b1["priority_incidents"][0]
    assert top["severity"] == "critical" and top["immediate_attention"]


def test_resolved_incidents_leave_the_priority_and_unresolved_lists(scenario):
    b = brief(scenario, **DAY_SHIFT)
    top = b["priority_incidents"][0]["incident_id"]
    scenario.patch(f"/incidents/{top}", json={"status": "resolved", "note": "handled"})
    after = brief(scenario, **DAY_SHIFT)
    assert top not in [x["incident_id"] for x in after["priority_incidents"]]
    assert top not in [x["incident_id"] for x in after["unresolved_incidents"]]
    assert after["unresolved_total"] == b["unresolved_total"] - 1
    assert after["key_metrics"]["incidents_by_status"].get("resolved") == 1


def test_new_versus_carried_over_incidents(scenario):
    whole = brief(scenario, **DAY_SHIFT)
    traffic = next(x for x in whole["new_incidents"] if x["category"] == "traffic_enforcement")
    assert traffic["new_in_period"]
    late_start = brief(scenario, mode="custom", start="2026-01-15T10:00:01Z", end="2026-01-15T14:00:00Z")
    carried = next(x for x in late_start["unresolved_incidents"] if x["incident_id"] == traffic["incident_id"])
    assert not carried["new_in_period"] and traffic["incident_id"] not in [x["incident_id"] for x in late_start["new_incidents"]]


def test_device_health_finding_and_grounded_recommendation(scenario):
    b = brief(scenario, **DAY_SHIFT)
    finding = next(f for f in section(b, "device_health")["findings"] if f["statement"].startswith("radar-int05"))
    assert finding["basis"] == "observed" and "No recovery was reported" in finding["statement"]
    assert finding["quality_issue_ids"] and finding["incident_ids"]
    assert scenario.get(f"/quality-issues/{finding['quality_issue_ids'][0]}").json()["issue_type"] == "device_offline"
    assert scenario.get(f"/incidents/{finding['incident_ids'][0]}").json()["category"] == "device_failure"
    assert {r["event_id"] for r in finding["evidence"]} == {"h1"}
    rec = next(a for a in b["recommended_actions"] if "Dispatch maintenance" in a["statement"])
    assert "radar-int05" in rec["statement"] and set(finding["quality_issue_ids"]) <= set(rec["quality_issue_ids"])
    assert any(a["statement"].startswith("Acknowledge and assign") for a in b["recommended_actions"])
    assert all(a["basis"] == "recommendation" for a in b["recommended_actions"])


def test_repeated_device_alerts_are_reported_with_their_events(client):
    ingest(client, [ev(event_id=f"rep-{i}", device="acs-x", source="access_control", event_type="access_denied", at=i * 20,
                       payload={"credential_id": f"C{i}", "door_id": "Gate-A-D1", "result": "denied"}) for i in range(14)])
    analyze(client)
    correlate(client)
    b = brief(client, **DAY_SHIFT)
    repeated = [f for f in section(b, "repeated_by_device")["findings"] if f["quality_issue_ids"]]
    assert repeated, section(b, "repeated_by_device")
    q = client.get(f"/quality-issues/{repeated[0]['quality_issue_ids'][0]}").json()
    assert q["issue_type"] in ("repeated_device_events", "event_spike")
    assert {r["event_id"] for r in repeated[0]["evidence"]} <= {f"rep-{i}" for i in range(14)}
    busiest = next(f for f in section(b, "repeated_by_device")["findings"] if f["basis"] == "database_fact")
    assert busiest["statement"].startswith("Device acs-x: 14 events") and busiest["event_query"]["device_id"] == "acs-x"
    assert client.get("/events", params=busiest["event_query"]).json()["total"] == 14


def test_conflicting_evidence_is_reported_not_resolved(client):
    ingest(client, [radar("r1", 1000, "T1"), violation("v1", 1000 - 420, "T1")])
    analyze(client)
    correlate(client)
    b = brief(client, **DAY_SHIFT)
    conflicts = [f for f in section(b, "conflicts_uncertainty")["findings"] if f["basis"] == "conflict"]
    assert conflicts and any("disagree on the time" in f["statement"] for f in conflicts)
    assert "does not decide which record is correct" in conflicts[0]["reasoning"]
    assert any("Resolve the conflicting evidence" in a["statement"] for a in b["recommended_actions"])
    assert any(f["factor"] == "conflicts" for f in b["confidence"]["factors"])


def test_zone_filter_restricts_events_incidents_and_findings(scenario):
    b = brief(scenario, zone=GATE["zone"], **DAY_SHIFT)
    assert b["filters"]["zones"] == [GATE["zone"]]
    assert b["key_metrics"]["events_total"] == events_between(scenario, "2026-01-15T06:00:00Z", "2026-01-15T14:00:00Z", zone=GATE["zone"])
    for iid in b["incident_ids"]:
        assert GATE["zone"] in scenario.get(f"/incidents/{iid}").json()["zones"]


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "params,field",
    [
        ({"tz": "Mars/Olympus"}, "tz"),
        ({"mode": "custom", "start": "2026-01-15T10:00:00Z"}, "end"),
        ({"mode": "custom", "start": "2026-01-15T10:00:00Z", "end": "2026-01-15T09:00:00Z"}, "end"),
        ({"mode": "custom", "start": "2026-01-01T00:00:00Z", "end": "2026-03-01T00:00:00Z"}, "end"),
        ({"mode": "custom", "start": "2026-01-15T10:00:00", "end": "2026-01-15T11:00:00Z"}, "start"),
        ({"mode": "shift", "start": "2026-01-15T10:00:00Z"}, "start"),
        ({"mode": "shift", "shift": "lunch", "date": "2026-01-15"}, "shift"),
        ({"mode": "day", "date": "15/01/2026"}, "date"),
    ],
)
def test_invalid_parameters_are_rejected_with_the_field(client, params, field):
    response = client.get("/briefing", params=params)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"][0]["field"] == field


def test_unknown_mode_and_limit_are_validated(client):
    assert client.get("/briefing", params={"mode": "week"}).status_code == 422
    assert client.get("/briefing", params={"limit": 0}).status_code == 422


def test_periods_endpoint(scenario):
    body = scenario.get("/briefing/periods").json()
    assert [s["name"] for s in body["shifts"]] == ["day", "evening", "night"]
    assert body["timezone"] == "UTC" and body["data_start"] and body["data_end"]


# --------------------------------------------------------------------------- incident reasoning endpoint


def test_incident_reasoning_classifies_statements(scenario):
    incident = scenario.get("/incidents", params={"category": "traffic_enforcement"}).json()["items"][0]
    r = scenario.get(f"/reasoning/incidents/{incident['id']}").json()
    d = scenario.get(f"/incidents/{incident['id']}").json()
    bases = {f["basis"] for f in r["findings"]}
    assert {"observed", "recommendation"} <= bases
    assert len([f for f in r["findings"] if f["basis"] == "observed"]) == len(d["observations"])
    assert r["priority"]["severity"] == d["severity"]
    timeline_ids = {t["event_id"] for t in d["timeline"]}
    assert all(ref["event_id"] in timeline_ids for f in r["findings"] for ref in f["evidence"])
    assert scenario.get("/reasoning/incidents/999999").status_code == 404


def test_briefing_reads_do_not_modify_source_events(scenario):
    before = scenario.get("/events", params={"page_size": 500}).json()["items"]
    brief(scenario, **DAY_SHIFT)
    brief(scenario, mode="day", date="2026-01-15", tz="Asia/Dubai")
    assert scenario.get("/events", params={"page_size": 500}).json()["items"] == before


def test_generated_at_is_utc(scenario):
    generated = datetime.fromisoformat(brief(scenario, **DAY_SHIFT)["generated_at"].replace("Z", "+00:00"))
    assert generated.tzinfo is not None and generated.utcoffset() == UTC.utcoffset(None)


def test_findings_never_list_the_same_event_twice(scenario):
    b = brief(scenario, mode="day", date="2026-01-15")
    for s in b["sections"]:
        for f in s["findings"]:
            pks = [r["event_pk"] for r in f["evidence"]]
            assert len(pks) == len(set(pks)), f["id"]
    assert len({r["event_pk"] for r in b["evidence_index"]}) == len(b["evidence_index"])


def test_stray_old_event_does_not_create_empty_baselines(scenario):
    """Regression (Phase 7A): one very old event made empty earlier days count as zero-activity baselines,
    so every device looked 'significantly' busier. Comparison windows must actually contain stored data."""
    before = brief(scenario, mode="day", date="2026-01-15")
    ingest(scenario, [ev(event_id="stray-1900", at=-(126 * 365 * 86400))])  # ~1900
    after = brief(scenario, mode="day", date="2026-01-15")
    trend = section(after, "trends")["findings"][0]
    assert trend["basis"] == "missing" and "no comparison is possible" in trend["statement"], trend
    assert after["period"]["baseline"] == before["period"]["baseline"]
    significant = [f for s in after["sections"] for f in s["findings"] if "usual" in f["statement"] and f["basis"] == "inference"]
    assert significant == [], [f["statement"] for f in significant]


def test_volume_summary_names_rule_findings_and_statistical_comparisons_separately(scenario):
    """Regression (Phase 7B): '0 repeated-activity or spike findings; 2 zones significantly above…' read as contradictory."""
    b = brief(scenario, **DAY_SHIFT)
    for key, label in (("repeated_by_device", "device"), ("repeated_by_location", "zone")):
        sec = section(b, key)
        rule_findings = sum(1 for f in sec["findings"] if f["quality_issue_ids"])
        assert sec["summary"].startswith(f"{rule_findings} quality-rule spike/repetition finding(s); "), sec["summary"]
        assert "statistical comparison" in sec["summary"]
        assert "repeated-activity or spike finding" not in sec["summary"]
