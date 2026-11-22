"""Compare quality-engine findings with the generator's ground truth (evaluation only).

Units
-----
* A ground-truth *finding* is one labelled anomaly / conflict / uncertainty note
  (``expected_anomalies``, ``expected_conflicts``, ``expected_uncertainty``), with its
  event ids and submission indexes.
* An issue *matches* a finding when they share an event id or a submission.

Per issue type:

* precision = issues matching a finding of the mapped kinds / all issues of the type
* recall    = findings of the mapped kinds matched by at least one issue / all such findings
* false positives are split into ``fp_explained`` (the issue's evidence belongs to some
  other labelled finding, i.e. a real but differently-labelled anomaly) and
  ``fp_unexplained`` (only unlabelled background events).

The production engine never reads ground truth; this module only reads its output.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any

# issue type (optionally ":subtype") -> ground-truth kinds it should find
KIND_MAP: dict[str, tuple[str, ...]] = {
    "duplicate_submission:exact": ("duplicate_exact",),
    "duplicate_submission:conflicting": ("duplicate_conflicting",),
    "invalid_record": ("invalid_record",),
    "delayed_event": ("delayed_event",),
    "out_of_order_arrival": ("out_of_order_arrival",),
    "timestamp_conflict": ("timestamp_inconsistency", "conflicting_timestamps"),
    "location_conflict": ("conflicting_locations",),
    "conflicting_sensor_results": ("contradictory_vehicle_details", "conflicting_identity", "conflicting_sensor_results"),
    # one device repeating an alert is also what the access-failure and auth-failure bursts look like per device
    "repeated_device_events": ("repeated_device_events", "access_failure_spike", "event_spike"),
    "event_spike": ("event_spike", "access_failure_spike", "localized_cluster", "repeated_device_events"),
    "device_offline": ("device_offline",),
    "device_degraded": ("device_degraded",),
    "sensor_failure": ("sensor_failure",),
    "device_recovered": ("device_recovered",),
    "low_confidence_detection": ("low_confidence_detection",),
    "uncorrelatable_event": ("missing_entity_identifier", "insufficient_linking_evidence", "unresolved_conflict", "uncorrelatable_event"),
}


def ground_truth_findings(truth: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    for sc in truth["scenarios"]:
        for group in ("expected_anomalies", "expected_conflicts", "expected_uncertainty"):
            for item in sc[group]:
                findings.append({"scenario_id": sc["scenario_id"], "kind": item["kind"], "events": set(item["event_ids"]),
                                 "submissions": set(item["submission_indexes"])})
    return findings


def load_issues(con: sqlite3.Connection, batch_id: str | None) -> list[dict[str, Any]]:
    """Issues with their evidence as external event ids and 0-based submission indexes (positions in ``batch_id``)."""
    index_of: dict[int, int] = {}
    query = "SELECT id, position FROM raw_submissions" + (" WHERE batch_id = ?" if batch_id else "")
    for sid, position in con.execute(query, (batch_id,) if batch_id else ()):
        index_of[sid] = position - 1
    issues: dict[int, dict[str, Any]] = {}
    for iid, itype, subtype in con.execute("SELECT id, issue_type, subtype FROM quality_issues"):
        issues[iid] = {"id": iid, "type": itype, "subtype": subtype, "events": set(), "submissions": set()}
    for iid, ext, sub, event_sub in con.execute(
        "SELECT q.issue_id, q.external_event_id, q.submission_id, e.submission_id FROM quality_issue_evidence q "
        "LEFT JOIN events e ON e.id = q.event_pk"
    ):
        if ext:
            issues[iid]["events"].add(ext)
        for s in (sub, event_sub):
            if s is not None and s in index_of:
                issues[iid]["submissions"].add(index_of[s])
    return list(issues.values())


def evaluate(con: sqlite3.Connection, truth: dict[str, Any], batch_id: str | None = None) -> dict[str, Any]:
    findings = ground_truth_findings(truth)
    by_event: dict[str, list[int]] = defaultdict(list)
    by_sub: dict[int, list[int]] = defaultdict(list)
    for i, f in enumerate(findings):
        for e in f["events"]:
            by_event[e].append(i)
        for s in f["submissions"]:
            by_sub[s].append(i)

    issues = load_issues(con, batch_id)
    results: dict[str, Any] = {}
    for key, kinds in KIND_MAP.items():
        itype, _, subtype = key.partition(":")
        selected = [i for i in issues if i["type"] == itype and (not subtype or i["subtype"] == subtype)]
        target = {i for i, f in enumerate(findings) if f["kind"] in kinds}
        tp = fp_explained = fp_unexplained = 0
        found: set[int] = set()
        for issue in selected:
            touched = {fi for e in issue["events"] for fi in by_event.get(e, ())} | {fi for s in issue["submissions"] for fi in by_sub.get(s, ())}
            hits = touched & target
            if hits:
                tp += 1
                found |= hits
            elif touched:
                fp_explained += 1
            else:
                fp_unexplained += 1
        n = len(selected)
        results[key] = {
            "issues": n,
            "true_positive_issues": tp,
            "fp_explained": fp_explained,
            "fp_unexplained": fp_unexplained,
            "precision": round(tp / n, 4) if n else None,
            "ground_truth_findings": len(target),
            "findings_detected": len(found),
            "false_negatives": len(target) - len(found),
            "recall": round(len(found) / len(target), 4) if target else None,
        }
    return {"by_type": results, "total_issues": len(issues), "ground_truth_findings": len(findings)}


def format_table(report: dict[str, Any]) -> str:
    lines = [f"{'issue type':36} {'issues':>7} {'TP':>6} {'FP-expl':>7} {'FP-bg':>6} {'prec':>6} {'GT':>6} {'found':>6} {'FN':>5} {'recall':>6}"]
    for key, r in report["by_type"].items():
        def pct(v: float | None) -> str:
            return "  n/a" if v is None else f"{v:6.3f}"

        lines.append(f"{key:36} {r['issues']:7} {r['true_positive_issues']:6} {r['fp_explained']:7} {r['fp_unexplained']:6} {pct(r['precision'])} "
                     f"{r['ground_truth_findings']:6} {r['findings_detected']:6} {r['false_negatives']:5} {pct(r['recall'])}")
    return "\n".join(lines)
