"""Transparent incident prioritization.

The score is a sum of named factors, each with its points and a sentence saying
why; it ranks incidents for attention and never changes stored severity.

* severity: the stored incident severity;
* status: unresolved incidents only (open > acknowledged > investigating);
  resolved and false-positive incidents are never prioritized;
* recency: how close the latest event is to the reference time;
* evidence strength: heuristic incident confidence plus independent source
  types (repeated submissions are not events, so they cannot add points);
* unresolved conflicts: contradictions need a decision before action;
* device failure: loss of sensor coverage;
* anomalies: incident events also flagged as a spike or repeated alerts.

Ties are broken by severity, most recent activity, then incident id.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.models.incidents import Incident
from app.services.reasoning.configuration import ReasoningSettings

UNRESOLVED = ("open", "acknowledged", "investigating")
SEVERITY_RANK = {"informational": 1, "low": 2, "medium": 3, "high": 4, "critical": 5}


@dataclass
class Scored:
    incident: Incident
    score: float
    factors: list[tuple[str, float, str]]
    immediate: bool
    reason: str


def score_incident(i: Incident, sources: list[str], reference: datetime, anomalies: int, s: ReasoningSettings) -> Scored:
    factors: list[tuple[str, float, str]] = []
    if i.status not in UNRESOLVED:
        reason = f"Not prioritized: the operator marked it {i.status.replace('_', ' ')}."
        return Scored(i, 0.0, [("status", 0.0, reason)], False, reason)

    factors.append(("severity", s.severity_points.get(i.severity, 0), f"Stored severity is {i.severity}."))
    factors.append(("status", s.status_points.get(i.status, 0),
                    {"open": "Not yet acknowledged by an operator.", "acknowledged": "Acknowledged but not yet under investigation.",
                     "investigating": "Under investigation."}[i.status]))
    hours = max(0.0, (reference - i.last_occurred_at).total_seconds() / 3600)
    recency = 10 if hours <= 1 else 6 if hours <= 4 else 3 if hours <= 12 else 1
    factors.append(("recency", recency, f"Latest event {hours:.1f} h before the reference time."))
    factors.append(("evidence_strength", round(s.confidence_points * i.confidence, 1),
                    f"Heuristic incident confidence {i.confidence:.2f} ({i.confidence_level})."))
    extra_sources = min(3, max(0, len(set(sources)) - 1))
    if extra_sources:
        factors.append(("corroboration", s.corroboration_points * extra_sources,
                        f"{len(set(sources))} independent source types (repeated submissions are not counted)."))
    conflicts = len(i.conflicts or [])
    if conflicts:
        factors.append(("unresolved_conflicts", s.conflict_points * min(2, conflicts),
                        f"{conflicts} conflict(s) must be resolved before acting on this evidence."))
    if i.category == "device_failure":
        factors.append(("device_failure", s.device_failure_points, "A device failure reduces sensor coverage."))
    if anomalies:
        factors.append(("anomalies", s.anomaly_points, f"{anomalies} of its events are part of a spike or repeated alerts."))

    score = round(sum(p for _, p, _ in factors), 1)
    immediate = i.severity in ("critical", "high") or score >= s.immediate_attention_score
    top = sorted(factors, key=lambda f: -f[1])[:3]
    reason = (("Immediate attention: " if immediate else "Monitor: ")
              + "; ".join(f"{name.replace('_', ' ')} (+{points:g})" for name, points, _ in top) + ".")
    return Scored(i, score, factors, immediate, reason)


def rank(scored: list[Scored]) -> list[Scored]:
    return sorted(
        scored,
        key=lambda x: (-x.score, -SEVERITY_RANK.get(x.incident.severity, 0), -x.incident.last_occurred_at.timestamp(), x.incident.id),
    )
