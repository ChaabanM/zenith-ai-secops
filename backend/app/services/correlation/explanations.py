"""Deterministic, evidence-grounded incident text.

Everything here is built from the linked events, their payload fields and Phase 3
quality findings. Sections are kept apart: verified observations (what sensors
reported), inferences (what the engine concluded from linkage), conflicts,
missing information, uncertainty, and advisory recommended actions. Nothing is
invented: unknown values are stated as unknown.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.services.correlation.clustering import Group
from app.services.correlation.configuration import CorrelationSettings
from app.services.correlation.constraints import low_confidence
from app.services.correlation.scoring import LINK_TYPES, SEVERITY_RANK, Member, cap, incident_confidence, max_severity, raise_to

RULE_VERSION = "4a.1"
QUALITY_CONFLICT_TYPES = ("conflicting_sensor_results", "location_conflict", "timestamp_conflict")


@dataclass
class QualityNote:
    issue_id: int
    issue_type: str
    subtype: str | None
    title: str


@dataclass
class IncidentDraft:
    fingerprint: str
    category: str
    title: str
    severity: str
    severity_reason: str
    confidence: float
    confidence_level: str
    confidence_reason: str
    summary: str
    linkage_explanation: str
    observations: list[str]
    inferences: list[str]
    conflicts: list[str]
    missing_information: list[str]
    uncertainty: list[str]
    recommended_actions: list[str]
    details: dict[str, Any]
    first_occurred_at: datetime
    last_occurred_at: datetime
    members: list[Member] = field(default_factory=list)

    @property
    def core_pks(self) -> set[int]:
        return {m.obs.pk for m in self.members if m.core}

    @property
    def all_pks(self) -> set[int]:
        return {m.obs.pk for m in self.members}

    def content_hash(self) -> str:
        body = {k: getattr(self, k) for k in (
            "category", "title", "severity", "severity_reason", "confidence", "confidence_level", "confidence_reason", "summary",
            "linkage_explanation", "observations", "inferences", "conflicts", "missing_information", "uncertainty",
            "recommended_actions", "details")}
        body["time"] = [self.first_occurred_at.isoformat(), self.last_occurred_at.isoformat()]
        body["members"] = [[m.obs.pk, m.role, m.link_type, m.core, m.linkage_confidence, m.explanation, m.conflict] for m in self.members]
        return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def _t(o_ts: datetime) -> str:
    return o_ts.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _dur(seconds: float) -> str:
    s = abs(seconds)
    return f"{s:.0f} s" if s < 90 else f"{s / 60:.1f} min" if s < 5400 else f"{s / 3600:.1f} h"


def _label(text: str | None) -> str:
    return (text or "").replace("_", " ")


def describe_member(m: Member) -> str:
    o = m.obs
    bits = []
    if o.get("plate"):
        vehicle = " ".join(str(x) for x in (o.get("colour"), o.get("make"), o.get("model")) if x)
        bits.append(f"plate {o.get('plate')}" + (f" ({vehicle})" if vehicle else ""))
    if o.source == "radar" and o.get("speed") is not None:
        bits.append(f"{o.get('speed')} km/h (limit {o.get('limit')})")
    if o.get("speed") is not None and o.source == "traffic_violation":
        bits.append(f"measured {o.get('speed')} km/h")
    if o.get("credential"):
        bits.append(f"credential {o.get('credential')}" + (f" ({o.get('reason')})" if o.get("reason") else ""))
    if o.get("subject"):
        bits.append(f"matched {o.get('subject')} (score {o.get('score')})")
    if o.get("segment"):
        bits.append(f"segment {o.get('segment')}")
    if o.get("input_point"):
        bits.append(f"input {o.get('input_point')}")
    if o.get("status") and o.source == "device_health":
        bits.append(f"status {o.get('status')}" + (f", code {o.get('code')}" if o.get("code") else ""))
    if o.get("src_ip"):
        bits.append(f"from {o.get('src_ip')} as {o.get('username')}")
    if o.get("verification"):
        bits.append(f"verification: {o.get('verification')}")
    if o.source == "operator_report" and o.get("summary"):
        bits.append(f"\"{o.get('summary')}\"")
    conf = f", confidence {o.confidence:.2f}" if o.confidence is not None else ""
    where = f" at {o.zone}" if o.zone else ""
    extra = f": {'; '.join(bits)}" if bits else ""
    return f"{_t(o.t)}: {o.device} ({_label(o.source)}) reported {_label(o.etype)}{where}{extra}{conf}"


def build_incident(g: Group, quality: dict[int, list[QualityNote]], duplicates: dict[int, int], s: CorrelationSettings) -> IncidentDraft:
    members = sorted(g.members.values(), key=lambda m: (m.obs.t, m.obs.pk))
    core = [m for m in members if m.core]
    leaves = [m for m in members if not m.core]
    f = g.facts
    zones = sorted({m.obs.zone for m in members if m.obs.zone})
    zone = f.get("zone") or (zones[0] if zones else "unknown location")

    # ---- quality findings (annotations, never exclusions) -------------------------------
    conflicts = list(g.conflicts)
    own_conflicts = bool(conflicts)
    corroborating: list[str] = []
    uncertainty: list[str] = []
    notes_used: dict[int, QualityNote] = {}
    for m in members:
        for q in quality.get(m.obs.pk, []):
            if q.issue_id in notes_used:
                continue
            notes_used[q.issue_id] = q
            if q.issue_type in QUALITY_CONFLICT_TYPES:
                # the engine's own contradiction takes precedence; Phase 3 is cited, not double-counted
                text = f"Data-quality finding #{q.issue_id}: {q.title}"
                (corroborating if own_conflicts else conflicts).append(text)
            elif q.issue_type in ("delayed_event", "out_of_order_arrival"):
                uncertainty.append(f"{m.obs.event_id} arrived late or out of order (finding #{q.issue_id}); the timeline uses event time, not arrival order.")
            elif q.issue_type == "uncorrelatable_event":
                uncertainty.append(f"{m.obs.event_id} was flagged as hard to link (finding #{q.issue_id}); it is included only through "
                                   f"'{_label(m.link_type)}' evidence.")
    for m in members:
        if low_confidence(m.obs, s.low_source_confidence):
            uncertainty.append(f"{m.obs.event_id} ({_label(m.obs.etype)}) is a low-confidence detection"
                               + (f" ({m.obs.confidence:.2f})" if m.obs.confidence is not None else "")
                               + ": treat it as uncertain evidence, not a confirmed observation.")
    for m in leaves:
        uncertainty.append(f"{m.obs.event_id} is linked by context only ({LINK_TYPES[m.link_type][2]}); it may be unrelated.")
    dup_total = sum(duplicates.get(m.obs.pk, 0) for m in members)

    # ---- severity -----------------------------------------------------------------------
    severity, who = max_severity(members)
    reasons = [f"Highest severity among strongly linked evidence: {severity}" + (f" ({who.obs.event_id}, {_label(who.obs.etype)})" if who else "")]
    if g.kind == "health":
        if not f.get("recovered"):
            severity = raise_to(severity, "critical")
            reasons.append("raised to critical: the device has not reported recovery")
        else:
            severity = raise_to(severity, "high")
            reasons.append("an outage or sensor failure is at least high while it lasted")
    if g.kind == "auth":
        severity = raise_to(severity, "high" if f.get("success") else "medium")
        reasons.append("a successful login followed the burst (possible compromise)" if f.get("success") else "repeated failures without success")
    if g.kind in ("identity", "travel"):
        severity = "medium"
        reasons.append("set to medium: contradictory evidence that is not verified either way")
    reported = [m for m in core if m.obs.confidence is not None]
    if reported and all(low_confidence(m.obs, s.low_source_confidence) for m in reported) and SEVERITY_RANK[severity] > SEVERITY_RANK["medium"]:
        severity = cap(severity, "medium")
        reasons.append("capped at medium: every strongly linked detection is low-confidence")
    reasons.append("contextual (leaf) evidence and duplicate submissions do not raise severity")
    severity_reason = "; ".join(reasons) + "."

    score, level, confidence_reason = incident_confidence(members, len(conflicts), s.low_source_confidence)

    title, summary, inferences, missing, actions = _category_text(g, members, core, leaves, conflicts, zone)
    for text in corroborating:
        inferences.append(f"The contradiction is independently reported by {text[0].lower()}{text[1:]}.")
    link_counts = Counter(m.link_type for m in members)
    linkage = "Linked by " + "; ".join(f"{LINK_TYPES[t][2]} ({n})" for t, n in sorted(link_counts.items(), key=lambda kv: -LINK_TYPES[kv[0]][0])) + "."
    if leaves:
        linkage += " Contextual items are attached as leaves: they never link further events."

    observations = [describe_member(m) + (" [context]" if not m.core else "") for m in members[:25]]
    if len(members) > 25:
        observations.append(f"... and {len(members) - 25} more linked events (see the evidence list).")
    if dup_total:
        observations.append(f"{dup_total} repeated submission(s) of these events were received; they are the same observations and are not counted as corroboration.")

    details = {
        "builder": g.kind,
        "anchor_event_id": g.anchor.event_id,
        "facts": {k: v for k, v in f.items() if v is not None},
        "link_types": dict(sorted(link_counts.items())),
        "core_events": len(core),
        "contextual_events": len(leaves),
        "sources": sorted({m.obs.source for m in members}),
        "duplicate_submissions_ignored": dup_total,
        "quality_issue_ids": sorted(notes_used)[:200],
    }
    return IncidentDraft(
        fingerprint=f"{g.kind}:{g.anchor.event_id}",
        category=g.category,
        title=title[:300],
        severity=severity,
        severity_reason=severity_reason,
        confidence=score,
        confidence_level=level,
        confidence_reason=confidence_reason,
        summary=summary,
        linkage_explanation=linkage,
        observations=observations,
        inferences=inferences,
        conflicts=conflicts,
        missing_information=missing,
        uncertainty=list(dict.fromkeys(uncertainty))[:50],
        recommended_actions=actions,
        details=details,
        first_occurred_at=members[0].obs.t,
        last_occurred_at=members[-1].obs.t,
        members=members,
    )


def _has(members: list[Member], source: str, etype: str | None = None) -> bool:
    return any(m.obs.source == source and (etype is None or m.obs.etype == etype) for m in members)


def _category_text(g: Group, members: list[Member], core: list[Member], leaves: list[Member], conflicts: list[str],
                   zone: str) -> tuple[str, str, list[str], list[str], list[str]]:
    f = g.facts
    span = _dur((members[-1].obs.t - members[0].obs.t).total_seconds())
    devices = sorted({m.obs.device for m in members})
    inferences: list[str] = []
    missing: list[str] = []
    actions: list[str] = []
    k = g.kind

    if k == "vehicle":
        v = f.get("vehicle") or {}
        vehicle = " ".join(str(x) for x in (v.get("colour"), v.get("make"), v.get("model")) if x) or "vehicle"
        what = ", ".join(_label(x) for x in f.get("violations", []))
        title = f"{what.capitalize()} by {vehicle} {f.get('plate') or ''} at {zone} ({len(devices)} sensors)".replace("  ", " ")
        if conflicts:
            title = "Conflicting records: " + title
        summary = (f"{len(members)} observations from {len(devices)} devices over {span} describe one vehicle pass "
                   f"(plate {f.get('plate') or 'unknown'}) with {what} at {zone}.")
        inferences.append("The linked records are treated as one vehicle pass because they share the fully read plate or an explicit radar track reference within the pass window.")
        if any(m.link_type == "plausible_movement" for m in members):
            inferences.append("A later read of the same fully read plate at another site is linked as onward movement, because the distance and "
                              "time are consistent with normal driving; a cloned or misread plate would also explain it.")
        if not _has(members, "anpr"):
            missing.append("No independent ANPR read confirms the plate (it comes only from the enforcement camera).")
        if not _has(members, "cctv"):
            missing.append("No CCTV footage is linked to this pass.")
        if "speeding" in f.get("violations", []) and not _has(members, "radar"):
            missing.append("No radar measurement is linked to the speeding record.")
        actions.append("Review the evidence images and confirm the plate before any enforcement notice (operator decision).")
        if conflicts:
            actions.append("Resolve the conflicting records first: check the vehicle registration and camera clocks; a cloned plate is possible.")
    elif k == "access":
        title = f"{f.get('attempts')} denied access attempts with credential {f.get('credential')} at {zone}"
        if _has(core, "access_control", "door_forced_open"):
            title += ", then door forced open"
        summary = (f"Credential {f.get('credential')} was denied {f.get('attempts')} times at {zone} ({', '.join(f.get('reasons', []))}) within {span}.")
        inferences.append("The attempts are attributed to one credential, not to a verified person: the holder of the badge may not be the person using it.")
        if f.get("holder") is None:
            missing.append("The credential is not registered to a known person.")
        if not _has(members, "cctv"):
            missing.append("No camera observation of the person at the door is linked.")
        if not _has(members, "operator_report"):
            missing.append("No guard or operator follow-up is recorded.")
        actions += [f"Review camera footage of {zone} for the attempt period.", f"Check the status of credential {f.get('credential')} and contact its holder.",
                    "Consider dispatching a guard to the door (advisory; requires operator decision)."]
    elif k == "identity":
        title = f"Badge of {f.get('holder')} used while face recognition matched {f.get('face_subject')} at {zone}"
        summary = (f"At {zone}, a granted badge and the only face capture within seconds name different people "
                   "(the two records are provisionally paired by time and place; it is not verified that they concern one person).")
        inferences.append("The badge use and the face capture are paired by time and place only (no shared identifier); a second person passing unseen would also explain it.")
        missing.append("Which identity is correct cannot be determined from the data.")
        actions += ["Review the face capture and the camera footage for the door.", f"Confirm with {f.get('holder')} whether the credential was lent or lost."]
    elif k == "perimeter":
        confirmed = any(m.role == "camera_detection" for m in leaves)
        title = f"{_label(f.get('alarm_type')).capitalize()} alarm at {zone}, segment {f.get('segment')}"
        title += " - not confirmed by camera" if conflicts else " - person seen by camera" if confirmed else ""
        summary = f"Perimeter sensor alarm on segment {f.get('segment')} of {zone}; {len(members)} linked events over {span}."
        if confirmed:
            inferences.append("The camera detection is assumed to concern the alarm because it is at the same fence line shortly after; it carries no identifier.")
        if not confirmed and not conflicts:
            missing.append("No camera confirmation of an intruder.")
        if not _has(members, "operator_report"):
            missing.append("No patrol report is linked yet.")
        actions += [f"Dispatch a patrol to segment {f.get('segment')} of {zone} (advisory; requires operator decision).", "Review the fence-line camera recording."]
        if conflicts:
            actions.append("Treat the alarm as unverified until the segment is physically checked: the sensor and the camera disagree.")
    elif k == "health":
        states = f.get("states", [])
        problem = "offline" if "offline" in states else "sensor failure"
        if f.get("recovered"):
            title = f"{f.get('device')} {problem}, recovered after {_dur(f.get('downtime_seconds', 0))}" if f.get("downtime_seconds") else f"{f.get('device')} {problem}, recovered"
        else:
            title = f"{f.get('device')} {problem} - no recovery reported"
        summary = f"Health reports about {f.get('device')}: {' -> '.join(states)} over {span} (reported by {f.get('monitor') or 'the health monitor'})."
        inferences.append(f"Observations from {f.get('device')} during the outage are missing; the absence of its events is not evidence that nothing happened.")
        if leaves:
            inferences.append("Low-confidence output from the device before the failure is linked as possibly affected by the degradation.")
        if not f.get("recovered"):
            missing.append("No recovery report has been received.")
        actions += [f"Open a maintenance ticket for {f.get('device')} (codes: {', '.join(c for c in f.get('codes', []) if c) or 'none'}).",
                    f"Treat coverage of {zone} by {f.get('device')} as unavailable for the outage window."]
    elif k == "auth":
        title = f"{f.get('failures')} failed logins for {f.get('username')} from {f.get('src_ip')}" + (", then a successful login" if f.get("success") else "")
        summary = f"{f.get('failures')} failed authentications from {f.get('src_ip')} against {f.get('username')} on {f.get('device')} within {_dur(f.get('window_seconds', 0))}."
        inferences.append("The burst suggests password guessing" + ("; the following success may mean the account was compromised (unconfirmed)." if f.get("success") else "."))
        missing.append("Whether the activity was legitimate (for example a misconfigured client) is unknown.")
        actions += [f"Check recent activity of account {f.get('username')} and the origin of {f.get('src_ip')}.",
                    "Consider blocking the address or resetting the credentials (requires approval)."]
    elif k == "travel":
        who = f"Fleet vehicle {f.get('vehicle_id')}" if f.get("vehicle_id") else f"Plate {f.get('plate')}"
        title = f"{who} reported {f.get('distance_km')} km apart within {_dur(f.get('seconds', 0))}"
        summary = f"Two observations of plate {f.get('plate')} cannot both be correct: the vehicle could not travel {f.get('distance_km')} km in {_dur(f.get('seconds', 0))}."
        missing.append("Which observation is correct (GPS fault, misread or cloned plate) is unknown.")
        actions += ["Confirm the vehicle's actual position (radio the crew or check another sensor).", "Check the GPS unit and the ANPR read image."]
    else:  # standalone alerts and reports
        o = g.anchor
        title = f"{_label(o.etype).capitalize()} at {zone}"
        summary = f"Single {_label(o.etype)} reported by {o.device}; no other evidence is linked."
        missing.append("No corroborating sensor evidence is linked.")
        actions.append("Verify the alert with the control room or an available camera before acting.")
    if leaves and k not in ("health",):
        inferences.append(f"{len(leaves)} contextual item(s) are linked by time, place and event type only.")
    return title, summary, inferences, missing, actions
