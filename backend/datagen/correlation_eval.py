"""Compare correlated incidents with the generator's ground truth (evaluation only).

Pairwise evaluation over labelled events:

* **positive pairs**: events that must be grouped. Taken from scenarios whose
  ``expected_incident`` is true: all pairs inside ``related_event_ids``, or, when
  the scenario declares ``must_remain_separate`` groups, all pairs inside each group.
  The G "reverse-order GPS trail" scenarios are excluded from positives: they are
  routine positions delivered late (a data-quality matter), not a security incident;
  they are reported separately.
* **negative pairs**: events that must stay apart: pairs across
  ``must_remain_separate`` groups (unrelated bystanders, separate vehicles in a
  cluster, scenario K), pairs inside scenario L (ambiguous: grouping them is forcing),
  and any pair of events from two *different* scenarios that ends up in one incident.

* correct relationship = positive pair in the same incident
* false split  = positive pair not in the same incident
* false merge  = negative pair in the same incident

Labelled events merged with unlabelled background events are reported separately
("background attachments"): they are not known to be wrong.
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from itertools import combinations
from typing import Any


def incident_membership(con: sqlite3.Connection) -> dict[str, int]:
    rows = con.execute(
        "SELECT e.external_event_id, e.incident_id FROM incident_evidence e JOIN incidents i ON i.id = e.incident_id "
        "WHERE i.correlation_state = 'active'"
    )
    return {eid: iid for eid, iid in rows}


def _pairs(ids: list[str]) -> list[tuple[str, str]]:
    return [tuple(sorted(p)) for p in combinations(sorted(set(ids)), 2)]


def evaluate(con: sqlite3.Connection, truth: dict[str, Any]) -> dict[str, Any]:
    member = incident_membership(con)
    scenario_of: dict[str, str] = {}
    variant_of: dict[str, str] = {}
    positives: dict[tuple[str, str], str] = {}
    negatives: dict[tuple[str, str], str] = {}
    expected: dict[str, Any] = {}
    related: dict[str, list[str]] = {}
    for sc in truth["scenarios"]:
        sid, key = sc["scenario_id"], f"{sc['type'][0]}:{sc['variant']}"
        variant_of[sid] = key
        expected[sid] = sc["expected_incident"]
        events = sc["related_event_ids"] + sc["unrelated_event_ids"]
        related[sid] = sc["related_event_ids"]
        for e in events:
            scenario_of.setdefault(e, sid)
        if sc["expected_incident"] is True and key != "G:reverse_order_arrival":
            groups = next((s["groups"] for s in truth["must_remain_separate"] if s["scenario_id"] == sid), None)
            for group in (groups or [sc["related_event_ids"]]):
                if groups and set(group) & set(sc["unrelated_event_ids"]):
                    continue  # the bystander group is not a related group
                for p in _pairs(group):
                    positives[p] = key
        if sc["expected_incident"] == "uncertain":
            for p in _pairs(sc["related_event_ids"]):
                negatives[p] = key
    for sep in truth["must_remain_separate"]:
        key = variant_of[sep["scenario_id"]]
        for g1, g2 in combinations(sep["groups"], 2):
            for a in g1:
                for b in g2:
                    negatives[tuple(sorted((a, b)))] = key

    by_type: dict[str, Counter] = defaultdict(Counter)
    for (a, b), key in positives.items():
        hit = a in member and member.get(a) == member.get(b)
        by_type[key]["positive_pairs"] += 1
        by_type[key]["correct" if hit else "false_split"] += 1
    for (a, b), key in negatives.items():
        if a in member and member.get(a) == member.get(b):
            by_type[key]["false_merge"] += 1
        by_type[key]["negative_pairs"] += 1

    # cross-scenario merges and background attachments, from the incident side
    incidents: dict[int, list[str]] = defaultdict(list)
    for eid, iid in member.items():
        incidents[iid].append(eid)
    cross_pairs = 0
    background = 0
    cross_examples = []
    for iid, eids in incidents.items():
        labelled = [e for e in eids if e in scenario_of]
        if labelled:
            background += len(eids) - len(labelled)
        for a, b in combinations(sorted(labelled), 2):
            if scenario_of[a] != scenario_of[b] and tuple(sorted((a, b))) not in negatives:
                cross_pairs += 1
                if len(cross_examples) < 5:
                    cross_examples.append({"incident_id": iid, "scenarios": sorted({scenario_of[a], scenario_of[b]})})

    tp = sum(c["correct"] for c in by_type.values())
    fn = sum(c["false_split"] for c in by_type.values())
    fm = sum(c["false_merge"] for c in by_type.values()) + cross_pairs

    # size-insensitive event-level view: share of each scenario's related events that sit in its main incident
    shares = []
    for sid, exp in expected.items():
        if exp is not True or variant_of[sid] == "G:reverse_order_arrival" or len(related[sid]) < 2:
            continue
        counts = Counter(member[e] for e in related[sid] if e in member)
        shares.append((counts.most_common(1)[0][1] if counts else 0) / len(related[sid]))

    coverage: dict[str, Counter] = defaultdict(Counter)
    for sid, exp in expected.items():
        key = variant_of[sid]
        ids = related[sid]
        incident_counts = Counter(member[e] for e in ids if e in member)
        largest = incident_counts.most_common(1)[0][1] if incident_counts else 0
        if exp is True:
            if ids and largest == len(ids):
                coverage[key]["fully_grouped"] += 1
            elif largest >= max(2, len(ids) / 2):
                coverage[key]["mostly_grouped"] += 1
            elif incident_counts:
                coverage[key]["fragmented"] += 1
            else:
                coverage[key]["no_incident"] += 1
        else:
            all_ids = ids + [e for sc in truth["scenarios"] if sc["scenario_id"] == sid for e in sc["unrelated_event_ids"]]
            coverage[key]["incident_raised" if any(e in member for e in all_ids) else "no_incident"] += 1
    return {
        "pairwise": {
            "positive_pairs": len(positives),
            "correct_relationships": tp,
            "false_splits": fn,
            "negative_pairs_checked": len(negatives),
            "false_merges_labelled": sum(c["false_merge"] for c in by_type.values()),
            "false_merges_cross_scenario": cross_pairs,
            "precision": round(tp / (tp + fm), 4) if tp + fm else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        },
        "event_grouping": {
            "scenarios": len(shares),
            "mean_share_in_main_incident": round(sum(shares) / len(shares), 4) if shares else None,
            "fully_grouped_scenarios": sum(1 for x in shares if x == 1.0),
        },
        "background_attachments": background,
        "cross_scenario_examples": cross_examples,
        "by_variant": {k: dict(v) for k, v in sorted(by_type.items())},
        "coverage": {k: dict(v) for k, v in sorted(coverage.items())},
        "incidents": len(incidents),
    }


def format_report(r: dict[str, Any]) -> str:
    p = r["pairwise"]
    lines = [
        f"incidents with evidence: {r['incidents']}",
        f"pairwise: precision {p['precision']}  recall {p['recall']}  correct {p['correct_relationships']}/{p['positive_pairs']}  "
        f"false splits {p['false_splits']}  false merges {p['false_merges_labelled']} labelled + {p['false_merges_cross_scenario']} cross-scenario "
        f"(of {p['negative_pairs_checked']} negative pairs checked); background attachments {r['background_attachments']}",
        f"event grouping: {r['event_grouping']['fully_grouped_scenarios']}/{r['event_grouping']['scenarios']} scenarios fully grouped, "
        f"mean share of related events in the main incident {r['event_grouping']['mean_share_in_main_incident']}",
        f"{'variant':42} {'pos':>5} {'ok':>5} {'split':>5} {'neg':>5} {'merge':>5} | coverage",
    ]
    keys = sorted(set(r["by_variant"]) | set(r["coverage"]))
    for k in keys:
        v = r["by_variant"].get(k, {})
        cov = ", ".join(f"{a}={b}" for a, b in sorted(r["coverage"].get(k, {}).items()))
        lines.append(f"{k:42} {v.get('positive_pairs', 0):5} {v.get('correct', 0):5} {v.get('false_split', 0):5} "
                     f"{v.get('negative_pairs', 0):5} {v.get('false_merge', 0):5} | {cov}")
    return "\n".join(lines)
