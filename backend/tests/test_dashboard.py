"""GET /dashboard/summary: same numbers as the briefing for the same period, labelled all-time totals, empty database."""

import pytest

from tests.test_briefing import DAY_SHIFT, brief, scenario  # noqa: F401 (fixture)


def dash(client, **params):
    response = client.get("/dashboard/summary", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_period_metrics_match_the_briefing_and_the_events_api(scenario):  # noqa: F811
    d, b = dash(scenario, **DAY_SHIFT), brief(scenario, **DAY_SHIFT)
    assert d["period"] == b["period"]
    m, bm = d["period_metrics"], b["key_metrics"]
    for key in ("events_total", "devices_reporting", "incidents_active", "incidents_unresolved", "incidents_new", "quality_issues_total"):
        assert m[key] == bm[key], key
    events = scenario.get("/events", params={"occurred_from": d["period"]["start"], "occurred_to": d["period"]["end"], "page_size": 1}).json()["total"]
    assert m["events_total"] == events
    assert sum(x["count"] for x in d["events_by_source"]) == events
    assert sum(h["count"] for h in d["events_per_hour"]) == events
    sev = m["incidents_by_severity"]
    assert m["incidents_high_critical"] == sev.get("critical", 0) + sev.get("high", 0)
    assert [x["incident_id"] for x in d["priority_incidents"]] == [x["incident_id"] for x in b["priority_incidents"]][:5]
    assert d["briefing"]["executive_summary"]["text"] == b["executive_summary"]["text"]
    assert d["briefing"]["recommendations"] == b["recommended_actions"][:3]


def test_stored_totals_are_all_time_and_labelled(scenario):  # noqa: F811
    d = dash(scenario, **DAY_SHIFT)
    t = d["stored_totals"]
    assert t["events_stored"] == scenario.get("/events", params={"page_size": 1}).json()["total"]
    summary = scenario.get("/incidents/summary").json()
    assert t["incidents_active"] == summary["total"] and t["incidents_unresolved"] == summary["unresolved"]
    assert t["quality_issues"] == scenario.get("/quality-issues", params={"page_size": 1}).json()["total"]
    assert t["submissions_duplicate"] == 1
    assert "ignore the period" in d["scope"]["stored_totals"] and "not probabilities" in d["scope"]["scores"]
    # all-time includes the previous day's event; the period does not
    assert t["events_stored"] > d["period_metrics"]["events_total"]


def test_unresolved_counts_follow_operator_status(scenario):  # noqa: F811
    before = dash(scenario, **DAY_SHIFT)
    top = before["priority_incidents"][0]
    assert top["severity"] in ("critical", "high")
    scenario.patch(f"/incidents/{top['incident_id']}", json={"status": "false_positive"})
    after = dash(scenario, **DAY_SHIFT)
    assert after["period_metrics"]["incidents_unresolved"] == before["period_metrics"]["incidents_unresolved"] - 1
    assert after["period_metrics"]["incidents_unresolved_high_critical"] == before["period_metrics"]["incidents_unresolved_high_critical"] - 1
    assert after["stored_totals"]["incidents_unresolved"] == before["stored_totals"]["incidents_unresolved"] - 1
    assert top["incident_id"] not in [x["incident_id"] for x in after["priority_incidents"]]
    assert after["period_metrics"]["incidents_active"] == before["period_metrics"]["incidents_active"]  # still counted, not unresolved


def test_quality_and_device_health_are_grounded(scenario):  # noqa: F811
    d = dash(scenario, **DAY_SHIFT)
    by_type = {x["value"]: x["count"] for x in d["quality_by_type"]}
    assert sum(by_type.values()) == d["period_metrics"]["quality_issues_total"]
    for q in d["quality_high_severity"]:
        stored = scenario.get(f"/quality-issues/{q['id']}").json()
        assert stored["severity"] in ("high", "critical") and stored["title"] == q["title"]
    assert d["quality_high_severity_total"] >= len(d["quality_high_severity"])
    assert d["period_metrics"]["devices_without_reported_recovery"] == 1  # radar-int05 offline, no recovery
    assert d["period_metrics"]["device_health_concerns"] >= 1
    finding = next(f for f in d["device_health"] if f["statement"].startswith("radar-int05"))
    assert finding["basis"] == "observed" and finding["quality_issue_ids"]
    assert "does not mean a device is healthy" in d["device_health_note"]
    assert d["uncertainty"]["delayed_events"] == by_type.get("delayed_event", 0)
    assert d["grounding"]["all_references_verified"]


def test_zone_filter_and_time_zone(scenario):  # noqa: F811
    d = dash(scenario, zone="Gate-A", tz="Asia/Dubai", mode="day", date="2026-01-15")
    assert d["filters"]["zones"] == ["Gate-A"] and d["period"]["timezone"] == "Asia/Dubai"
    assert d["period"]["start"] == "2026-01-14T20:00:00Z" and "(Asia/Dubai)" in d["period"]["label"]
    events = scenario.get("/events", params={"zone": "Gate-A", "occurred_from": d["period"]["start"], "occurred_to": d["period"]["end"],
                                             "page_size": 1}).json()["total"]
    assert d["period_metrics"]["events_total"] == events
    for x in d["priority_incidents"]:
        assert "Gate-A" in x["zones"]


def test_default_period_and_reproducible_query(scenario):  # noqa: F811
    d = dash(scenario)
    assert d["period"]["default_reason"] and d["period"]["query"]["mode"] == "shift"
    again = brief(scenario, **d["period"]["query"])
    assert again["period"]["start"] == d["period"]["start"] and again["period"]["end"] == d["period"]["end"]
    assert again["key_metrics"]["events_total"] == d["period_metrics"]["events_total"]


def test_empty_database(client):
    d = dash(client)
    assert d["stored_totals"]["events_stored"] == 0 and d["stored_totals"]["data_start"] is None
    assert d["period_metrics"]["events_total"] == 0 and d["priority_incidents"] == [] and d["events_by_source"] == []
    assert d["quality_by_type"] == [] and d["device_health"] == [] and d["spikes"] == []
    assert "not confirmation that operations were normal" in d["briefing"]["executive_summary"]["text"]
    assert d["briefing"]["confidence"]["level"] == "low"
    assert "current time" in d["period"]["default_reason"]


@pytest.mark.parametrize("params", [{"tz": "Nowhere/City"}, {"mode": "custom", "start": "2026-01-15T10:00:00Z"}, {"mode": "day", "date": "x"}])
def test_validation_matches_the_briefing(client, params):
    response = client.get("/dashboard/summary", params=params)
    assert response.status_code == 422 and response.json()["error"]["details"][0]["field"]


def test_dashboard_reads_do_not_modify_events(scenario):  # noqa: F811
    before = scenario.get("/events", params={"page_size": 500}).json()["items"]
    dash(scenario)
    assert scenario.get("/events", params={"page_size": 500}).json()["items"] == before


def test_linked_lists_reproduce_the_dashboard_counts(scenario):  # noqa: F811
    """The dashboard links to /incidents, /data-quality and /events with the period bounds; those lists must agree."""
    d = dash(scenario, **DAY_SHIFT)
    rng = {"occurred_from": d["period"]["start"], "occurred_to": d["period"]["end"], "page_size": 1}
    total = lambda path, **extra: scenario.get(path, params={**rng, **extra}).json()["total"]  # noqa: E731
    m = d["period_metrics"]
    assert total("/incidents") == m["incidents_active"]
    assert total("/incidents", status="open,acknowledged,investigating") == m["incidents_unresolved"]
    assert total("/incidents", severity="critical,high") == m["incidents_high_critical"]
    assert total("/quality-issues") == m["quality_issues_total"]
    assert total("/quality-issues", severity="critical,high") == d["quality_high_severity_total"]
    assert total("/events") == m["events_total"]
    for source in d["events_by_source"]:
        assert total("/events", source_type=source["value"]) == source["count"]
