"""Ground-truth integrity checks (evaluation tooling; never imported by the app).

``check_ground_truth(lines, truth)`` verifies that a generated dataset and its
ground-truth manifest agree:

* one manifest entry per submission, in file order;
* every referenced event id exists as an *accepted* submission, every referenced
  submission index exists, and duplicate links point at the accepted original;
* expected outcome counts equal the per-submission expectations;
* related / unrelated / must-remain-separate groups do not overlap;
* expected absences (device outages) contain no observations from that device;
* production records leak no labels (scenario ids, expected answers).

It returns a list of human-readable problems (empty when everything is consistent).
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from typing import Any

# Keys / markers that must never appear in production records.
LEAK_KEYS = {"scenario", "scenario_id", "scenario_ids", "expected", "expected_status", "ground_truth", "label", "is_anomaly", "incident_id"}
LEAK_MARKERS = ("SCN-", "ground_truth", "expected_incident")


def _parse(line: str) -> Any:
    try:
        return json.loads(line)
    except ValueError:
        return None


def _walk_keys(value: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            keys.append(k)
            keys.extend(_walk_keys(v))
    elif isinstance(value, list):
        for v in value:
            keys.extend(_walk_keys(v))
    return keys


def _ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def check_ground_truth(lines: list[str], truth: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    subs = truth.get("submissions", [])
    n = len(lines)

    # --- per-submission expectations -------------------------------------------------
    if len(subs) != n:
        problems.append(f"manifest has {len(subs)} submission entries for {n} lines")
    parsed = [_parse(line) for line in lines]
    accepted: dict[str, int] = {}
    for i, entry in enumerate(subs[:n]):
        if entry.get("index") != i or entry.get("line") != i + 1:
            problems.append(f"submission entry {i} has index/line {entry.get('index')}/{entry.get('line')}")
            continue
        status = entry.get("expected_status")
        record = parsed[i]
        if status in ("accepted", "duplicate"):
            if not isinstance(record, dict) or record.get("event_id") != entry.get("event_id"):
                problems.append(f"line {i + 1}: expected {status} event {entry.get('event_id')} but the record does not carry it")
        if status == "accepted":
            if entry["event_id"] in accepted:
                problems.append(f"event_id {entry['event_id']} expected to be accepted twice")
            accepted[entry["event_id"]] = i
        elif status == "duplicate":
            original = entry.get("duplicate_of_index")
            if original is None or not 0 <= original < i:
                problems.append(f"line {i + 1}: duplicate_of_index {original} must point at an earlier submission")
            elif accepted.get(entry["event_id"]) != original:
                problems.append(f"line {i + 1}: duplicate_of_index {original} is not the accepted submission of {entry['event_id']}")
            elif entry.get("duplicate_kind") not in ("exact", "conflicting"):
                problems.append(f"line {i + 1}: unknown duplicate_kind {entry.get('duplicate_kind')}")
            elif entry["duplicate_kind"] == "exact" and record != parsed[original]:
                problems.append(f"line {i + 1}: 'exact' duplicate differs from line {original + 1}")
            elif entry["duplicate_kind"] == "conflicting" and record == parsed[original]:
                problems.append(f"line {i + 1}: 'conflicting' duplicate is identical to line {original + 1}")
        elif status == "invalid":
            if not entry.get("invalid_reasons"):
                problems.append(f"line {i + 1}: invalid submission without invalid_reasons")
        else:
            problems.append(f"line {i + 1}: unknown expected_status {status}")

    # --- outcome totals --------------------------------------------------------------
    tally = Counter(e.get("expected_status") for e in subs)
    kinds = Counter(e.get("duplicate_kind") for e in subs if e.get("expected_status") == "duplicate")
    expected = {
        "total_submissions": n,
        "accepted": tally["accepted"],
        "invalid": tally["invalid"],
        "duplicate": tally["duplicate"],
        "exact_duplicate": kinds["exact"],
        "conflicting_duplicate": kinds["conflicting"],
    }
    if truth.get("expected_outcomes") != expected:
        problems.append(f"expected_outcomes {truth.get('expected_outcomes')} != tallied {expected}")

    # --- scenario references ---------------------------------------------------------
    def check_ids(where: str, ids: list[str]) -> None:
        for eid in ids:
            if eid not in accepted:
                problems.append(f"{where}: event_id {eid} is not an accepted submission")

    def check_indexes(where: str, indexes: list[int]) -> None:
        for idx in indexes:
            if not isinstance(idx, int) or not 0 <= idx < n:
                problems.append(f"{where}: submission index {idx} out of range")

    seen_ids: set[str] = set()
    for sc in truth.get("scenarios", []):
        sid = sc.get("scenario_id", "?")
        if sid in seen_ids:
            problems.append(f"duplicate scenario_id {sid}")
        seen_ids.add(sid)
        related, unrelated = sc.get("related_event_ids", []), sc.get("unrelated_event_ids", [])
        check_ids(f"{sid}.related", related)
        check_ids(f"{sid}.unrelated", unrelated)
        if set(related) & set(unrelated):
            problems.append(f"{sid}: events both related and unrelated: {sorted(set(related) & set(unrelated))}")
        check_indexes(f"{sid}.submission_indexes", sc.get("submission_indexes", []))
        for group in ("expected_anomalies", "expected_conflicts", "expected_uncertainty"):
            for item in sc.get(group, []):
                check_ids(f"{sid}.{group}", item.get("event_ids", []))
                check_indexes(f"{sid}.{group}", item.get("submission_indexes", []))
                if not item.get("event_ids") and not item.get("submission_indexes"):
                    problems.append(f"{sid}.{group}: {item.get('kind')} references nothing")
        for absence in sc.get("expected_absences", []):
            start, end = _ts(absence["from"]), _ts(absence["to"])
            if end <= start:
                problems.append(f"{sid}: absence window ends before it starts")
            for i, record in enumerate(parsed):
                if (
                    isinstance(record, dict)
                    and record.get("device_id") == absence["device_id"]
                    and record.get("event_type") not in ("device_offline", "device_recovered")
                    and isinstance(record.get("occurred_at"), str)
                    and subs[i].get("expected_status") != "invalid"
                ):
                    try:
                        t = _ts(record["occurred_at"])
                    except ValueError:
                        continue
                    if start < t < end:
                        problems.append(f"{sid}: line {i + 1} is an observation from {absence['device_id']} inside its expected absence")

    for sep in truth.get("must_remain_separate", []):
        groups = [set(g) for g in sep.get("groups", [])]
        for g in groups:
            check_ids(f"{sep.get('scenario_id')}.must_remain_separate", sorted(g))
        for a in range(len(groups)):
            for b in range(a + 1, len(groups)):
                if groups[a] & groups[b]:
                    problems.append(f"{sep.get('scenario_id')}: separate groups overlap: {sorted(groups[a] & groups[b])}")

    # --- label leakage -----------------------------------------------------------------
    for i, line in enumerate(lines):
        if any(marker in line for marker in LEAK_MARKERS):
            problems.append(f"line {i + 1}: contains a ground-truth marker")
        record = parsed[i]
        if record is not None:
            leaked = LEAK_KEYS.intersection(_walk_keys(record))
            if leaked:
                problems.append(f"line {i + 1}: contains label keys {sorted(leaked)}")

    return problems
