"""Operational briefing: aggregate stored events, quality findings and incidents for a period.

Time basis (documented in docs/briefing.md):

* events are counted by **occurrence time** (``occurred_at``) in ``[start, end)``;
  one stored event per ``event_id``, so repeated submissions are never counted twice;
* incidents are included when their activity overlaps the period
  (``last_occurred_at >= start`` and ``first_occurred_at < end``); "new" means the
  first event occurred inside the period; status is the operator status *now*;
* quality findings are included when their time span overlaps the period; invalid
  records have no trustworthy occurrence time and are counted by ingestion time.

Every count is a database query. Every statement carries the event, incident and
quality-issue ids it rests on, and all references are verified before returning.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, distinct, exists, func, select
from sqlalchemy.orm import Session, aliased

from app.models.incidents import Incident, IncidentEvidence, IncidentFacet
from app.models.ingestion import Event, RawSubmission
from app.models.quality import QualityIssue, QualityIssueEvidence, QualityIssueFacet
from app.schemas.briefing import (
    Briefing,
    BriefingPeriod,
    BriefingSection,
    ConfidenceFactor,
    EvidenceRef,
    Finding,
    FindingConfidence,
    Grounding,
    OverallConfidence,
    PrioritizedIncident,
    PriorityFactor,
)
from app.services.briefing.configuration import BriefingSettings
from app.services.briefing.periods import Period
from app.services.correlation import repository as incident_repo
from app.services.reasoning import narrative
from app.services.reasoning.configuration import ReasoningSettings
from app.services.reasoning.grounding import verify_references
from app.services.reasoning.prioritization import UNRESOLVED, Scored, rank, score_incident

HEALTH_TYPES = ("device_offline", "device_degraded", "sensor_failure", "device_recovered", "device_health_warning")
HEALTH_PROBLEMS = ("device_offline", "device_degraded", "sensor_failure", "device_health_warning")
CONFLICT_TYPES = ("conflicting_sensor_results", "timestamp_conflict", "location_conflict")
ANOMALY_TYPES = ("event_spike", "repeated_device_events")
SEV_RANK = {"critical": 5, "high": 4, "medium": 3, "low": 2, "informational": 1}
ISSUE_LABELS = {
    "duplicate_submission": "duplicate submissions", "invalid_record": "malformed or incomplete records",
    "delayed_event": "delayed events", "out_of_order_arrival": "out-of-order arrivals", "timestamp_conflict": "conflicting timestamps",
    "location_conflict": "conflicting locations", "conflicting_sensor_results": "conflicting sensor results",
    "repeated_device_events": "repeated device alerts", "event_spike": "event spikes", "device_offline": "devices offline",
    "device_degraded": "degraded devices", "sensor_failure": "sensor failures", "device_recovered": "device recoveries",
    "device_health_warning": "device health warnings", "low_confidence_detection": "low-confidence detections",
    "uncorrelatable_event": "events that cannot be reliably linked",
}

_event_sev = case({k: v for k, v in SEV_RANK.items()}, value=Event.severity, else_=0)


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n:,} {word if n == 1 else (plural or word + 's')}"


class BriefingBuilder:
    def __init__(self, db: Session, period: Period, zones: list[str] | None, s: BriefingSettings, rs: ReasoningSettings,
                 limit: int | None = None, now: datetime | None = None) -> None:
        self.db, self.p, self.zones, self.s, self.rs = db, period, zones or None, s, rs
        self.limit = limit or s.top_n
        self.now = now or datetime.now(UTC)
        self.counter: Counter[str] = Counter()
        self.events: dict[int, str] = {}
        self.incidents: set[int] = set()
        self.quality: set[int] = set()

    # ------------------------------------------------------------------ helpers
    def lt(self, dt: datetime) -> str:
        return dt.astimezone(self.p.tz).strftime("%d %b %H:%M")

    def finding(self, category: str, basis: str, statement: str, reasoning: str, level: str, conf_basis: str,
                score: float | None = None, **kw: Any) -> Finding:
        self.counter[category] += 1
        if kw.get("evidence"):  # one reference per event, first occurrence kept
            seen: set[int] = set()
            kw["evidence"] = [r for r in kw["evidence"] if not (r.event_pk in seen or seen.add(r.event_pk))]
        f = Finding(id=f"{category}-{self.counter[category]}", category=category, basis=basis, statement=statement, reasoning=reasoning,
                    confidence=FindingConfidence(level=level, score=score, basis=conf_basis), **kw)
        for ref in f.evidence:
            self.events[ref.event_pk] = ref.event_id
        self.incidents.update(f.incident_ids)
        self.quality.update(f.quality_issue_ids)
        return f

    def event_window(self, start: datetime | None = None, end: datetime | None = None) -> list[Any]:
        conds = [Event.occurred_at >= (start or self.p.start), Event.occurred_at < (end or self.p.end)]
        if self.zones:
            conds.append(Event.zone.in_(self.zones))
        return conds

    def event_query(self, **extra: str) -> dict[str, str]:
        q = {"occurred_from": self.p.start.isoformat(), "occurred_to": self.p.end.isoformat(), **extra}
        if self.zones and "zone" not in q:
            q["zone"] = ",".join(self.zones)
        return q

    def sample(self, *conds: Any, n: int | None = None) -> list[EvidenceRef]:
        rows = self.db.execute(
            select(Event.id, Event.event_id).where(*self.event_window(), *conds)
            .order_by(_event_sev.desc(), Event.occurred_at, Event.id).limit(n or self.s.sample_events)
        ).all()
        return [EvidenceRef(event_pk=pk, event_id=eid) for pk, eid in rows]

    def issue_window(self) -> list[Any]:
        conds = [QualityIssue.occurred_to >= self.p.start, QualityIssue.occurred_from < self.p.end]
        if self.zones:
            f = aliased(QualityIssueFacet)  # aliased: outer queries may join the facet table themselves
            conds.append(exists().where(f.issue_id == QualityIssue.id, f.kind == "zone", f.value.in_(self.zones)).correlate(QualityIssue))
        return conds

    def issue_evidence(self, ids: list[int], per_issue: int = 3) -> dict[int, list[EvidenceRef]]:
        out: dict[int, list[EvidenceRef]] = defaultdict(list)
        if not ids:
            return out
        for qid, pk, eid in self.db.execute(
            select(QualityIssueEvidence.issue_id, Event.id, Event.event_id)
            .join(Event, Event.id == QualityIssueEvidence.event_pk)
            .where(QualityIssueEvidence.issue_id.in_(ids)).order_by(QualityIssueEvidence.issue_id, QualityIssueEvidence.position)
        ):
            if len(out[qid]) < per_issue and all(r.event_pk != pk for r in out[qid]):
                out[qid].append(EvidenceRef(event_pk=pk, event_id=eid))
        return out

    def counts_by(self, column: Any, start: datetime, end: datetime) -> dict[str, int]:
        return {k: c for k, c in self.db.execute(
            select(column, func.count()).where(*self.event_window(start, end), column.is_not(None)).group_by(column))}

    def available_baselines(self) -> list[tuple[datetime, datetime]]:
        """Comparison windows, cut to the same covered length as the current period.

        If the period runs past the latest stored event, only its covered part is compared
        (and each comparison window is cut to the same length), so missing future data is
        never reported as a drop.
        """
        first, last = self.db.execute(select(func.min(Event.occurred_at), func.max(Event.occurred_at))).one()
        self.cmp_end = self.p.end
        if first is None:
            return []
        if last < self.p.end - self.p.length * 0.01:  # ignore a trailing gap under 1% of the period
            self.cmp_end = max(self.p.start, last + timedelta(microseconds=1))
        length = self.cmp_end - self.p.start
        if length <= timedelta(0):
            return []
        out = []
        for s, _ in self.p.baselines(self.s.baseline_windows):
            e = s + length
            # A window counts only if stored events actually span most of it (any zone): a data gap, or a stray old
            # event that makes the global first event early, must not become a zero-activity baseline.
            lo, hi = self.db.execute(select(func.min(Event.occurred_at), func.max(Event.occurred_at))
                                     .where(Event.occurred_at >= s, Event.occurred_at < e)).one()
            if lo is not None and (hi - lo) >= 0.8 * length and (e - max(s, first)) >= 0.9 * length:
                out.append((s, e))
        return out

    def cmp_note(self) -> str:
        if self.cmp_end < self.p.end:
            return f" Compared over the first {(self.cmp_end - self.p.start).total_seconds() / 3600:.1f} h only (stored data ends there)."
        return ""

    def significance(self, current: int, baseline: list[int]) -> tuple[str | None, float | None, float | None]:
        """Poisson comparison with the baseline mean: 'increase', 'drop' or None, plus mean and z-score."""
        if not baseline:
            return None, None, None
        mean = sum(baseline) / len(baseline)
        z = (current - mean) / math.sqrt(max(mean, 1.0))
        if current >= self.s.significance_min_count and z >= self.s.significance_z and current >= 1.5 * mean:
            return "increase", mean, z
        if mean >= self.s.significance_min_count and current <= mean / self.s.drop_ratio:
            return "drop", mean, z
        return None, mean, z

    # ------------------------------------------------------------------ build
    def build(self) -> Briefing:
        p = self.p
        metrics = self.metrics()
        baselines = self.available_baselines()
        self.baselines = baselines
        scored, evidence = self.incidents_in_period()
        unresolved = [x for x in scored if x.incident.status in UNRESOLVED]
        new = [x for x in scored if x.incident.first_occurred_at >= p.start]
        metrics.update(
            incidents_active=len(scored),
            incidents_unresolved=len(unresolved),
            incidents_new=len(new),
            incidents_immediate=sum(1 for x in unresolved if x.immediate),
            incidents_unresolved_high_critical=sum(1 for x in unresolved if x.incident.severity in ("critical", "high")),
            incidents_with_conflicts=sum(1 for x in scored if x.incident.conflicts),
            incidents_by_severity=dict(Counter(x.incident.severity for x in scored)),
            incidents_by_status=dict(Counter(x.incident.status for x in scored)),
        )
        to_out = {x.incident.id: self.prioritized(x, i + 1, evidence.get(x.incident.id, [])) for i, x in enumerate(scored)}
        priority = [to_out[x.incident.id] for x in unresolved[: self.limit]]
        sections = [
            self.incident_activity(scored, metrics),
            self.by_device(),
            self.by_location(),
            self.device_health(scored),
            self.trends(metrics),
            self.data_quality(metrics),
            self.conflicts_uncertainty(scored),
        ]
        actions = self.recommendations(scored, sections, metrics)
        confidence = self.overall_confidence(metrics, scored)
        summary_text = self.executive_text(metrics, priority, sections, confidence)
        corpus = "\n".join([summary_text, *(f.statement for sec in sections for f in sec.findings), *(a.statement for a in actions),
                            *(f"#{x.incident_id} {x.title}" for x in to_out.values())])
        facts = {"period": p.label, "metrics": {k: v for k, v in metrics.items() if not isinstance(v, list)},
                 "priority_incidents": [{"id": x.incident_id, "title": x.title, "severity": x.severity, "status": x.status} for x in priority],
                 "recommended_actions": [a.statement for a in actions], "overall_confidence": confidence.level}
        executive = narrative.compose(summary_text, corpus, facts, self.rs)

        for x in to_out.values():
            for ref in x.evidence:
                self.events[ref.event_pk] = ref.event_id
        self.incidents.update(to_out)
        refs = [EvidenceRef(event_pk=pk, event_id=eid) for pk, eid in sorted(self.events.items(), key=lambda kv: kv[1])]
        unverified = verify_references(self.db, refs, sorted(self.incidents), sorted(self.quality))
        return Briefing(
            period=BriefingPeriod(
                mode=p.mode, label=p.label, start=p.start, end=p.end, timezone=str(p.tz.key),
                local_start=p.start.astimezone(p.tz).isoformat(), local_end=p.end.astimezone(p.tz).isoformat(),
                time_basis="Events by occurrence time (occurred_at) in [start, end); incidents whose activity overlaps the period, "
                           "with their current operator status; quality findings whose time span overlaps the period "
                           "(invalid records by ingestion time).",
                baseline=(f"{len(baselines)} comparison window(s): "
                          + ("the same hours on previous days" if p.length.total_seconds() <= 86400 else "the preceding equal-length periods")
                          + (" (none available: stored data does not reach back far enough)" if not baselines else "")),
                default_reason=p.default_reason,
                query=p.query or {},
            ),
            generated_at=self.now,
            filters={"zones": self.zones or []},
            executive_summary=executive,
            key_metrics=metrics,
            priority_incidents=priority,
            unresolved_incidents=[to_out[x.incident.id] for x in unresolved[: self.limit * 3]],
            unresolved_total=len(unresolved),
            new_incidents=[to_out[x.incident.id] for x in new[: self.limit * 3]],
            new_total=len(new),
            sections=sections,
            recommended_actions=actions,
            confidence=confidence,
            evidence_index=refs,
            incident_ids=sorted(self.incidents),
            quality_issue_ids=sorted(self.quality),
            grounding=Grounding(events_referenced=len(refs), incidents_referenced=len(self.incidents),
                                quality_issues_referenced=len(self.quality), all_references_verified=not unverified,
                                unverified_references=unverified),
        )

    # ------------------------------------------------------------------ metrics
    def metrics(self) -> dict[str, Any]:
        w = self.event_window()
        total, devices, zones = self.db.execute(
            select(func.count(), func.count(distinct(Event.device_id)), func.count(distinct(Event.zone))).where(*w)).one()
        dup = self.db.scalar(
            select(func.count()).select_from(RawSubmission).join(Event, Event.event_id == RawSubmission.external_event_id)
            .where(RawSubmission.status == "duplicate", *w)) or 0
        received_after = self.db.scalar(select(func.count()).where(*w, Event.received_at >= self.p.end)) or 0
        latest = self.db.scalar(select(func.max(Event.occurred_at)))
        hours = [{"hour_utc": h, "count": c} for h, c in self.db.execute(
            select(func.substr(Event.occurred_at, 1, 13).label("h"), func.count()).where(*w).group_by("h").order_by("h"))]
        return {
            "events_total": total,
            "devices_reporting": devices,
            "zones_reporting": zones,
            "events_by_severity": {k or "unknown": c for k, c in self.db.execute(select(Event.severity, func.count()).where(*w).group_by(Event.severity))},
            "events_by_source": dict(sorted(self.counts_by(Event.source_type, self.p.start, self.p.end).items(), key=lambda kv: -kv[1])),
            "duplicate_submissions_ignored": dup,
            "events_received_after_period_end": received_after,
            "latest_stored_event_at": latest.isoformat() if latest else None,
            "period_extends_past_stored_data": latest is None or latest < self.p.end - (self.p.length * 0.1),
            "events_per_hour": hours,
        }

    # ------------------------------------------------------------------ incidents
    def incidents_in_period(self) -> tuple[list[Scored], dict[int, list[EvidenceRef]]]:
        conds = [Incident.correlation_state == "active", Incident.last_occurred_at >= self.p.start, Incident.first_occurred_at < self.p.end]
        if self.zones:
            f = aliased(IncidentFacet)
            conds.append(exists().where(f.incident_id == Incident.id, f.kind == "zone", f.value.in_(self.zones)).correlate(Incident))
        rows = list(self.db.scalars(select(Incident).where(*conds)))
        ids = [r.id for r in rows]
        self.facets = incident_repo.facets_for(self.db, ids)
        anomalies: Counter[int] = Counter()
        evidence: dict[int, list[EvidenceRef]] = defaultdict(list)
        for start in range(0, len(ids), 500):
            part = ids[start : start + 500]
            for iid, n in self.db.execute(
                select(IncidentEvidence.incident_id, func.count(distinct(IncidentEvidence.event_pk)))
                .join(QualityIssueEvidence, QualityIssueEvidence.event_pk == IncidentEvidence.event_pk)
                .join(QualityIssue, QualityIssue.id == QualityIssueEvidence.issue_id)
                .where(IncidentEvidence.incident_id.in_(part), QualityIssue.issue_type.in_(ANOMALY_TYPES))
                .group_by(IncidentEvidence.incident_id)
            ):
                anomalies[iid] = n
            for iid, pk, eid in self.db.execute(
                select(IncidentEvidence.incident_id, Event.id, Event.event_id).join(Event, Event.id == IncidentEvidence.event_pk)
                .where(IncidentEvidence.incident_id.in_(part), IncidentEvidence.is_core.is_(True))
                .order_by(IncidentEvidence.incident_id, Event.occurred_at, Event.id)
            ):
                if len(evidence[iid]) < 5:
                    evidence[iid].append(EvidenceRef(event_pk=pk, event_id=eid))
        reference = min(self.p.end, self.now)
        scored = rank([score_incident(r, self.facets[r.id]["source"], reference, anomalies[r.id], self.rs) for r in rows])
        return scored, evidence

    def prioritized(self, x: Scored, rank_: int, evidence: list[EvidenceRef]) -> PrioritizedIncident:
        i, f = x.incident, self.facets[x.incident.id]
        return PrioritizedIncident(
            incident_id=i.id, title=i.title, category=i.category, severity=i.severity, status=i.status, confidence=i.confidence,
            confidence_level=i.confidence_level, first_occurred_at=i.first_occurred_at, last_occurred_at=i.last_occurred_at,
            detected_at=i.detected_at, event_count=i.event_count, zones=f["zone"], devices=f["device"], sources=f["source"],
            conflict_count=len(i.conflicts or []), priority_score=x.score, priority_rank=rank_, immediate_attention=x.immediate,
            factors=[PriorityFactor(factor=n, points=pts, explanation=e) for n, pts, e in x.factors], reason=x.reason,
            new_in_period=i.first_occurred_at >= self.p.start, detected_after_period=i.detected_at >= self.p.end,
            evidence=evidence, recommended_actions=list(i.recommended_actions or []),
        )

    def incident_activity(self, scored: list[Scored], metrics: dict[str, Any]) -> BriefingSection:
        findings: list[Finding] = []
        if not scored:
            return BriefingSection(key="incident_activity", title="Significant incident activity", summary="No incidents in this period.",
                                   findings=[], empty_reason="The correlation engine produced no incident whose activity overlaps this period. "
                                   "That means no qualifying pattern was found in the stored events, not that the period was confirmed safe.")
        by_cat: dict[str, list[Scored]] = defaultdict(list)
        for x in scored:
            by_cat[x.incident.category].append(x)
        sev = metrics["incidents_by_severity"]
        findings.append(self.finding(
            "incident_activity", "database_fact",
            f"{_plural(len(scored), 'incident')} had activity in this period ("
            + ", ".join(f"{sev[k]} {k}" for k in ("critical", "high", "medium", "low", "informational") if sev.get(k)) + "); "
            f"{metrics['incidents_new']} started in the period and {metrics['incidents_unresolved']} are unresolved.",
            "Counted from stored incidents whose first-to-last event span overlaps the period.", "high", "exact database count",
            incident_ids=[x.incident.id for x in scored[: self.limit * 2]], metrics={"by_category": {k: len(v) for k, v in by_cat.items()}},
        ))
        if self.baselines:
            base = [self.db.scalar(select(func.count()).where(Incident.correlation_state == "active", Incident.first_occurred_at >= s,
                                                              Incident.first_occurred_at < e)) or 0 for s, e in self.baselines]
            kind, mean, z = self.significance(metrics["incidents_new"], base)
            if self.cmp_end < self.p.end:
                kind = None  # incident counts over a partly empty period are not compared
            if kind:
                findings.append(self.finding(
                    "incident_activity", "inference",
                    f"New incidents are {'well above' if kind == 'increase' else 'well below'} the usual level: {metrics['incidents_new']} "
                    f"compared with an average of {mean:.1f} in the comparison windows.",
                    f"Poisson comparison with {len(base)} comparison window(s) (z = {z:.1f}).", "medium", "statistical comparison",
                    metrics={"baseline": base, "z": round(z, 2)},
                ))
        for cat, items in sorted(by_cat.items(), key=lambda kv: (-max(SEV_RANK[x.incident.severity] for x in kv[1]), -len(kv[1]), kv[0])):
            worst = max(items, key=lambda x: (SEV_RANK[x.incident.severity], x.score))
            open_n = sum(1 for x in items if x.incident.status in UNRESOLVED)
            findings.append(self.finding(
                "incident_activity", "database_fact",
                f"{cat.replace('_', ' ').capitalize()}: {_plural(len(items), 'incident')} ({open_n} unresolved); most serious "
                f"#{worst.incident.id} ({worst.incident.severity}) {worst.incident.title}.",
                "Grouped by the category assigned by the correlation engine.", "high", "exact database count",
                incident_ids=[x.incident.id for x in items[: self.limit]],
            ))
        return BriefingSection(key="incident_activity", title="Significant incident activity",
                               summary=findings[0].statement, findings=findings)

    # ------------------------------------------------------------------ repeated activity
    def _volume_section(self, key: str, title: str, column: Any, label: str, issue_type: str | None, spike_dim: str) -> BriefingSection:
        findings: list[Finding] = []
        current = self.counts_by(column, self.p.start, self.cmp_end)
        base = [self.counts_by(column, s, e) for s, e in self.baselines]
        # quality findings: repeated alerts (device) and spikes on this dimension
        types = [t for t in (issue_type, "event_spike") if t]
        issues = list(self.db.scalars(select(QualityIssue).where(*self.issue_window(), QualityIssue.issue_type.in_(types))
                                      .order_by(QualityIssue.severity_rank.desc(), QualityIssue.occurred_from, QualityIssue.id)))
        issues = [q for q in issues if q.issue_type != "event_spike" or (q.details or {}).get("dimension") == spike_dim]
        ev = self.issue_evidence([q.id for q in issues[: self.limit]])
        for q in issues[: self.limit]:
            findings.append(self.finding(
                key, "inference", f"{q.title} ({self.lt(q.occurred_from)}–{self.lt(q.occurred_to)}).",
                q.detection_reason, "medium" if (q.confidence or 0) < 0.8 else "high", f"data-quality rule {q.rule_id}",
                score=q.confidence, uncertainty=[q.uncertainty] if q.uncertainty else [], evidence=ev.get(q.id, []),
                evidence_total=q.event_count, quality_issue_ids=[q.id],
            ))
        significant = []
        for k, c in current.items():
            kind, mean, z = self.significance(c, [b.get(k, 0) for b in base])
            if kind == "increase":
                significant.append((k, c, mean, z))
        for k, c, mean, z in sorted(significant, key=lambda t: -t[3])[: self.limit]:
            cond = column == k
            findings.append(self.finding(
                key, "inference",
                f"{label} {k} reported {_plural(c, 'event')}, well above its usual {mean:.1f} for this time window.",
                f"Poisson comparison with {len(base)} comparison window(s) (z = {z:.1f}). Higher volume can be genuine activity or a "
                "device fault; the events themselves are listed as evidence." + self.cmp_note(), "medium", "statistical comparison",
                evidence=self.sample(cond), evidence_total=c, event_query=self.event_query(**{column.key: k}),
                metrics={"count": c, "baseline_mean": round(mean, 2), "z": round(z, 2)},
            ))
        top = sorted(current.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        if top:
            types_by = defaultdict(Counter)
            for k, et, n in self.db.execute(select(column, Event.event_type, func.count()).where(
                    *self.event_window(), column.in_([k for k, _ in top])).group_by(column, Event.event_type)):
                types_by[k][et] = n
            for k, c in top:
                mix = ", ".join(f"{t.replace('_', ' ')} {n}" for t, n in types_by[k].most_common(3))
                findings.append(self.finding(
                    key, "database_fact", f"{label} {k}: {_plural(c, 'event')} ({mix}).",
                    "Busiest by stored event count in the period (one event per event_id; repeated submissions excluded).",
                    "high", "exact database count", evidence=self.sample(column == k), evidence_total=c,
                    event_query=self.event_query(**{column.key: k}), metrics={"count": c},
                ))
        empty = None if findings else "No events were stored for this period, so there is no repeated activity to report."
        # Two different measures, named separately so they never read as contradictory:
        # rule findings from the data-quality engine, and statistical comparisons with earlier windows.
        unit = label.lower() + ("s" if len(significant) != 1 else "")
        comparison = (f"{len(significant)} {unit} showed elevated activity in statistical comparisons with the same hours on "
                      "previous days" if self.baselines else "statistical comparison not possible (no comparison baseline available)")
        summary = (f"{len(issues)} quality-rule spike/repetition finding(s); {comparison}.") if findings else "None."
        return BriefingSection(key=key, title=title, summary=summary, findings=findings, empty_reason=empty)

    def by_device(self) -> BriefingSection:
        return self._volume_section("repeated_by_device", "Repeated events by device", Event.device_id, "Device", "repeated_device_events", "device")

    def by_location(self) -> BriefingSection:
        return self._volume_section("repeated_by_location", "Repeated events by location", Event.zone, "Zone", None, "zone")

    # ------------------------------------------------------------------ device health
    def device_health(self, scored: list[Scored]) -> BriefingSection:
        findings: list[Finding] = []
        issues = list(self.db.scalars(select(QualityIssue).where(*self.issue_window(), QualityIssue.issue_type.in_(HEALTH_TYPES))
                                      .order_by(QualityIssue.occurred_from, QualityIssue.id)))
        by_device: dict[str, list[QualityIssue]] = defaultdict(list)
        for q in issues:
            by_device[(q.details or {}).get("affected_device_id") or (q.title.split()[0])].append(q)
        failure_incidents = defaultdict(list)
        for x in scored:
            if x.incident.category == "device_failure":
                for d in self.facets[x.incident.id]["device"]:
                    failure_incidents[d].append(x.incident.id)
        ev = self.issue_evidence([q.id for q in issues], per_issue=2)
        ordered = sorted(by_device.items(), key=lambda kv: (-max(q.severity_rank for q in kv[1]), kv[0]))
        self.health_unrecovered: list[tuple[str, list[int]]] = []
        for device, qs in ordered[: self.limit * 2]:
            latest = qs[-1]
            recovered = latest.issue_type == "device_recovered"
            problems = [q for q in qs if q.issue_type in HEALTH_PROBLEMS]
            seq = "; ".join(f"{q.issue_type.replace('device_', '').replace('_', ' ')} at "
                            f"{self.lt(q.occurred_to if q.issue_type == 'device_recovered' else q.occurred_from)}"
                            + (f" ({q.details.get('diagnostic_code')})" if (q.details or {}).get("diagnostic_code") else "") for q in qs[:6])
            if problems and not recovered:
                self.health_unrecovered.append((device, [q.id for q in qs]))
            findings.append(self.finding(
                "device_health", "observed",
                f"{device}: {seq}." + (" A recovery was reported afterwards." if recovered else
                                       " No recovery was reported in this period." if problems else ""),
                "Health status as reported by the monitoring system (device-health events), summarised by the data-quality engine. "
                "These are reports, not independent verification.", "high" if problems else "medium", "device / monitor reports",
                evidence=[r for q in qs for r in ev.get(q.id, [])][: self.s.sample_events], evidence_total=sum(q.event_count for q in qs),
                quality_issue_ids=[q.id for q in qs], incident_ids=failure_incidents.get(device, []),
                uncertainty=[] if recovered or not problems else ["Whether the device is working now is unknown from this period's data."],
            ))
        # devices that went silent compared with their usual volume
        if self.baselines:
            current = self.counts_by(Event.device_id, self.p.start, self.cmp_end)
            base = [self.counts_by(Event.device_id, s, e) for s, e in self.baselines]
            for device in sorted({d for b in base for d in b}):
                kind, mean, _ = self.significance(current.get(device, 0), [b.get(device, 0) for b in base])
                if kind == "drop":
                    c = current.get(device, 0)
                    findings.append(self.finding(
                        "device_health", "missing",
                        f"{device} reported {_plural(c, 'event')} compared with a usual {mean:.1f} for this time window."
                        + (" No events is not confirmation that the device or its area was normal." if c == 0 else ""),
                        "Large drop against the comparison windows. It may be an outage, a transmission problem, or genuinely quiet "
                        "activity; nothing in the stored data says which." + self.cmp_note(), "medium", "statistical comparison",
                        missing_information=["No health report explains the drop." if device not in by_device else
                                             "Health reports exist for this device; see the finding above."],
                        evidence=self.sample(Event.device_id == device), evidence_total=c, event_query=self.event_query(device_id=device),
                        metrics={"count": c, "baseline_mean": round(mean, 2)},
                    ))
        empty = None if findings else ("No device-health problems were reported and no device fell silent compared with its usual volume. "
                                       "Devices that never report health events cannot be assessed this way.")
        drops = sum(1 for f in findings if f.basis == "missing")
        summary = (f"{len(by_device)} device(s) with health reports ({len(self.health_unrecovered)} without a reported recovery); "
                   f"{drops} device(s) with a sharp drop in reported events." if findings else "No device-health concerns found.")
        return BriefingSection(key="device_health", title="Device-health problems", summary=summary, findings=findings, empty_reason=empty)

    # ------------------------------------------------------------------ trends
    def trends(self, metrics: dict[str, Any]) -> BriefingSection:
        findings: list[Finding] = []
        total = metrics["events_total"]
        if self.baselines:
            covered = self.db.scalar(select(func.count()).where(*self.event_window(self.p.start, self.cmp_end))) or 0
            base_totals = [self.db.scalar(select(func.count()).where(*self.event_window(s, e))) or 0 for s, e in self.baselines]
            kind, mean, z = self.significance(covered, base_totals)
            findings.append(self.finding(
                "trends", "inference" if kind else "database_fact",
                f"{_plural(covered, 'event')} in this period compared with an average of {mean:.1f} in the comparison windows"
                + (f" ({(covered - mean) / mean * 100:+.0f}%)" if mean else "") + (f": a significant {kind}." if kind else "."),
                f"Comparison windows start {', '.join(self.lt(s) for s, _ in self.baselines)}; Poisson z = {z:.1f}." + self.cmp_note(), "medium" if kind else "high",
                "statistical comparison" if kind else "exact database counts", metrics={"baseline": base_totals, "z": round(z, 2)},
            ))
            cur_src = self.counts_by(Event.source_type, self.p.start, self.cmp_end)
            base_src = [self.counts_by(Event.source_type, s, e) for s, e in self.baselines]
            for src in sorted(set(cur_src) | {k for b in base_src for k in b}):
                kind, mean, z = self.significance(cur_src.get(src, 0), [b.get(src, 0) for b in base_src])
                if kind:
                    c = cur_src.get(src, 0)
                    findings.append(self.finding(
                        "trends", "inference",
                        f"{src.replace('_', ' ')} events: {c:,} compared with a usual {mean:.1f} (significant {kind}).",
                        f"Poisson comparison with {len(base_src)} window(s) (z = {z:.1f})." + self.cmp_note(), "medium", "statistical comparison",
                        evidence=self.sample(Event.source_type == src) if c else [], evidence_total=c,
                        event_query=self.event_query(source_type=src), metrics={"count": c, "baseline_mean": round(mean, 2), "z": round(z, 2)},
                    ))
        else:
            findings.append(self.finding(
                "trends", "missing", f"{_plural(total, 'event')} in this period; no comparison is possible.",
                "The stored data does not cover the same time window on earlier days, so trends cannot be assessed.", "low",
                "no baseline", missing_information=["Historical data for the comparison windows."],
            ))
        peak = max(metrics["events_per_hour"], key=lambda h: h["count"], default=None)
        if peak and total:
            findings.append(self.finding(
                "trends", "database_fact", f"Busiest hour: {peak['hour_utc']}:00 UTC with {_plural(peak['count'], 'event')}.",
                "Hourly event counts by occurrence time.", "high", "exact database count",
                metrics={"events_per_hour": metrics["events_per_hour"]},
            ))
        spikes = list(self.db.scalars(select(QualityIssue).where(*self.issue_window(), QualityIssue.issue_type == "event_spike")
                                      .order_by(QualityIssue.severity_rank.desc(), QualityIssue.occurred_from, QualityIssue.id)))
        ev = self.issue_evidence([q.id for q in spikes[: self.limit]])
        for q in spikes[: self.limit]:
            findings.append(self.finding(
                "trends", "inference", f"{q.title} at {self.lt(q.occurred_from)}.", q.detection_reason, "medium", f"data-quality rule {q.rule_id}",
                score=q.confidence, uncertainty=[q.uncertainty] if q.uncertainty else [], evidence=ev.get(q.id, []),
                evidence_total=q.event_count, quality_issue_ids=[q.id],
            ))
        summary = findings[0].statement if findings else "No events."
        if spikes:
            summary += f" {_plural(len(spikes), 'spike')} detected."
        return BriefingSection(key="trends", title="Significant event trends and spikes", summary=summary, findings=findings)

    # ------------------------------------------------------------------ data quality
    def data_quality(self, metrics: dict[str, Any]) -> BriefingSection:
        findings: list[Finding] = []
        by_type = {t: n for t, n in self.db.execute(
            select(QualityIssue.issue_type, func.count()).where(*self.issue_window()).group_by(QualityIssue.issue_type))}
        metrics["quality_issues_by_type"] = dict(by_type)
        metrics["quality_issues_total"] = sum(by_type.values())
        total = metrics["events_total"] or 0
        order = sorted(by_type.items(), key=lambda kv: (-kv[1], kv[0]))
        top_ids: dict[str, list[int]] = {}
        for t, _n in order:
            top = list(self.db.scalars(select(QualityIssue.id).where(*self.issue_window(), QualityIssue.issue_type == t)
                                       .order_by(QualityIssue.severity_rank.desc(), QualityIssue.occurred_from, QualityIssue.id).limit(3)))
            top_ids[t] = top
        ev = self.issue_evidence([i for ids in top_ids.values() for i in ids], per_issue=1)
        for t, n in order:
            share = f" ({n / total:.1%} of events)" if total and t in ("delayed_event", "low_confidence_detection", "uncorrelatable_event") else ""
            findings.append(self.finding(
                "data_quality", "database_fact", f"{ISSUE_LABELS.get(t, t).capitalize()}: {n:,}{share}.",
                "Counted from Phase 3 data-quality findings overlapping the period.", "high", "exact database count",
                evidence=[r for i in top_ids[t] for r in ev.get(i, [])], evidence_total=n, quality_issue_ids=top_ids[t], metrics={"count": n},
            ))
        if metrics["duplicate_submissions_ignored"]:
            findings.append(self.finding(
                "data_quality", "database_fact",
                f"{_plural(metrics['duplicate_submissions_ignored'], 'repeated submission')} of this period's events were received; "
                "each event is counted once.", "Duplicates share an event_id with a stored event and never create a second event.",
                "high", "exact database count",
            ))
        if "invalid_record" not in by_type:
            all_invalid = self.db.scalar(select(func.count()).where(QualityIssue.issue_type == "invalid_record")) or 0
            if all_invalid:
                findings.append(self.finding(
                    "data_quality", "missing",
                    f"{_plural(all_invalid, 'invalid submission')} stored overall could not be placed in this period.",
                    "Invalid records have no trustworthy occurrence time; they are assigned to periods by ingestion time, and none were "
                    "ingested in this period.", "high", "exact database count",
                    missing_information=["The occurrence time of rejected records."],
                ))
        empty = None if findings else "No data-quality findings overlap this period."
        summary = (f"{metrics['quality_issues_total']:,} data-quality finding(s) overlap this period." if by_type else "No data-quality findings.")
        return BriefingSection(key="data_quality", title="Data-quality issues", summary=summary, findings=findings, empty_reason=empty)

    # ------------------------------------------------------------------ conflicts and uncertainty
    def conflicts_uncertainty(self, scored: list[Scored]) -> BriefingSection:
        findings: list[Finding] = []
        conflicted = [x for x in scored if x.incident.conflicts]
        self.conflicted_unresolved = [x.incident.id for x in conflicted if x.incident.status in UNRESOLVED]
        for x in conflicted[: self.limit]:
            i = x.incident
            findings.append(self.finding(
                "conflicts_uncertainty", "conflict", f"#{i.id} {i.title}: " + "; ".join(i.conflicts[:3]) + ".",
                "Contradictory records inside one incident; the system does not decide which record is correct.", i.confidence_level,
                "incident confidence (heuristic)", score=i.confidence, incident_ids=[i.id],
                missing_information=list(i.missing_information or [])[:3], uncertainty=list(i.uncertainty or [])[:3],
            ))
        conflict_issues = list(self.db.scalars(select(QualityIssue).where(*self.issue_window(), QualityIssue.issue_type.in_(CONFLICT_TYPES))
                                               .order_by(QualityIssue.severity_rank.desc(), QualityIssue.occurred_from, QualityIssue.id)))
        ev = self.issue_evidence([q.id for q in conflict_issues[: self.limit]])
        if conflict_issues:
            findings.append(self.finding(
                "conflicts_uncertainty", "conflict",
                f"{_plural(len(conflict_issues), 'conflicting record set')} (timestamps, locations or sensor results); most serious: "
                + "; ".join(f"#{q.id} {q.title}" for q in conflict_issues[:3]) + ".",
                "Phase 3 conflict findings overlapping the period.", "high", "exact database count",
                evidence=[r for q in conflict_issues[: self.limit] for r in ev.get(q.id, [])][: self.s.sample_events],
                evidence_total=sum(q.event_count for q in conflict_issues), quality_issue_ids=[q.id for q in conflict_issues[: self.limit]],
            ))
        low = [x for x in scored if x.incident.confidence_level == "low"]
        if low:
            findings.append(self.finding(
                "conflicts_uncertainty", "inference",
                f"{_plural(len(low), 'incident')} rest on weak evidence (low heuristic confidence): "
                + ", ".join(f"#{x.incident.id}" for x in low[: self.limit]) + ".",
                "Low incident confidence comes from weak links, low sensor confidence or conflicts (see each incident's confidence reason).",
                "low", "incident confidence (heuristic)", incident_ids=[x.incident.id for x in low[: self.limit]],
            ))
        lcf = self.db.scalar(select(func.count()).where(*self.issue_window(), QualityIssue.issue_type == "low_confidence_detection")) or 0
        unc = self.db.scalar(select(func.count()).where(*self.issue_window(), QualityIssue.issue_type == "uncorrelatable_event")) or 0
        if lcf or unc:
            findings.append(self.finding(
                "conflicts_uncertainty", "observed",
                f"{_plural(lcf, 'detection')} reported below the sensor's confidence threshold and {_plural(unc, 'event')} lacked the "
                "identifiers or context to be linked reliably. These are sensor reports, not verified identifications.",
                "Phase 3 low-confidence and uncorrelatable findings.", "high", "exact database count",
                missing_information=["Reliable identifiers for the unlinkable events."] if unc else [],
            ))
        empty = None if findings else "No conflicts or uncertainty flags were found for this period."
        summary = (f"{len(conflicted)} incident(s) with conflicting evidence, {len(conflict_issues)} conflicting record set(s), "
                   f"{len(low)} low-confidence incident(s).")
        return BriefingSection(key="conflicts_uncertainty", title="Conflicts and uncertainty", summary=summary, findings=findings, empty_reason=empty)

    # ------------------------------------------------------------------ recommendations
    def recommendations(self, scored: list[Scored], sections: list[BriefingSection], metrics: dict[str, Any]) -> list[Finding]:
        out: list[Finding] = []
        rec = "recommendation"

        def add(statement: str, reasoning: str, level: str = "high", **kw: Any) -> None:
            out.append(self.finding("recommendation", rec, statement, reasoning, level, "rule on stored findings", **kw))

        urgent_open = [x for x in scored if x.incident.status == "open" and x.incident.severity in ("critical", "high")]
        if urgent_open:
            add(f"Acknowledge and assign an owner to {_plural(len(urgent_open), 'open critical/high incident')}, starting with "
                + ", ".join(f"#{x.incident.id}" for x in urgent_open[:5]) + ".",
                "These incidents are unresolved, high or critical, and nobody has acknowledged them yet.",
                incident_ids=[x.incident.id for x in urgent_open[: self.limit]])
        if getattr(self, "conflicted_unresolved", None):
            ids = self.conflicted_unresolved
            add(f"Resolve the conflicting evidence in {_plural(len(ids), 'unresolved incident')} (" + ", ".join(f"#{i}" for i in ids[:5])
                + ") before any enforcement or follow-up action.",
                "Their records contradict each other; acting on one side could target the wrong vehicle or person.", incident_ids=ids[: self.limit])
        if getattr(self, "health_unrecovered", None):
            devices = [d for d, _ in self.health_unrecovered]
            add(f"Dispatch maintenance to check {', '.join(devices[:6])}" + (f" and {len(devices) - 6} more" if len(devices) > 6 else "")
                + ": a health problem was reported with no recovery in this period.",
                "Unrecovered device faults leave gaps in sensor coverage; events from these devices may be missing or unreliable.",
                quality_issue_ids=[i for _, ids in self.health_unrecovered for i in ids][: self.limit * 2])
        silent = [f for sec in sections if sec.key == "device_health" for f in sec.findings if f.basis == "missing"]
        if silent:
            names = [f.statement.split(" ")[0] for f in silent]
            add("Confirm that these devices are operating: " + ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")
                + ". Their event volume dropped sharply against the usual level.",
                "A sharp drop without a health report may be an unreported outage.", level="medium")
        q = metrics.get("quality_issues_by_type", {})
        for t, action in (("delayed_event", "Investigate transmission delays"), ("low_confidence_detection", "Check calibration and camera condition"),
                          ("repeated_device_events", "Review repeated alerts"), ("invalid_record", "Ask the integration owners to fix malformed records")):
            n = q.get(t, 0)
            if not n:
                continue
            rows = self.db.execute(select(QualityIssueFacet.value, func.count()).join(QualityIssue, QualityIssue.id == QualityIssueFacet.issue_id)
                                   .where(*self.issue_window(), QualityIssue.issue_type == t, QualityIssueFacet.kind == "device")
                                   .group_by(QualityIssueFacet.value).order_by(func.count().desc(), QualityIssueFacet.value).limit(3)).all()
            ids = list(self.db.scalars(select(QualityIssue.id).where(*self.issue_window(), QualityIssue.issue_type == t)
                                       .order_by(QualityIssue.severity_rank.desc(), QualityIssue.id).limit(5)))
            where = (" on " + ", ".join(f"{d} ({c})" for d, c in rows)) if rows else ""
            add(f"{action}{where}. {ISSUE_LABELS[t].capitalize()} in this period: {n:,}.",
                "Raised when the data-quality engine found this problem in the period; the busiest devices are listed first.",
                level="medium", quality_issue_ids=ids)
        if not out:
            add("No management action is indicated by the stored data for this period.",
                "No unresolved high/critical incidents, conflicts, device faults or data-quality problems were found. This reflects the "
                "stored data only.", level="medium")
        return out

    # ------------------------------------------------------------------ overall confidence
    def overall_confidence(self, metrics: dict[str, Any], scored: list[Scored]) -> OverallConfidence:
        factors: list[ConfidenceFactor] = []
        limitations = [
            "Counts reflect the events stored when the briefing was generated; late arrivals can change them.",
            "Incident status is the current operator status, not the status at the end of the period.",
            "Confidence scores are heuristic rankings, not calibrated probabilities.",
        ]
        total = metrics["events_total"]
        q = metrics.get("quality_issues_by_type", {})
        score = 1.0

        def factor(name: str, effect: float, text: str) -> None:
            nonlocal score
            if effect:
                score += effect
                factors.append(ConfidenceFactor(factor=name, effect=round(effect, 3), explanation=text))

        if total == 0:
            factor("no_events", -0.6, "No events are stored for this period; nothing can be confirmed either way.")
            limitations.append("No reported events is not confirmation of normal operations.")
        else:
            lcf = q.get("low_confidence_detection", 0) / total
            factor("low_confidence", -min(0.15, lcf), f"{lcf:.1%} of events are low-confidence detections.")
            dly = q.get("delayed_event", 0) / total
            factor("delays", -min(0.1, dly * 2), f"{dly:.1%} of events arrived late.")
            dup = metrics["duplicate_submissions_ignored"] / total
            factor("duplicates", -min(0.05, dup), f"{dup:.1%} repeated submissions (excluded from counts).")
            if metrics["events_received_after_period_end"]:
                factor("late_data", -0.05, f"{metrics['events_received_after_period_end']} events were received after the period ended.")
        unrec = len(getattr(self, "health_unrecovered", []))
        factor("device_health", -min(0.15, 0.05 * unrec), f"{unrec} device(s) reported a fault without recovery (coverage gaps).")
        conflicts = sum(1 for x in scored if x.incident.conflicts)
        factor("conflicts", -min(0.15, 0.03 * conflicts), f"{conflicts} incident(s) contain conflicting evidence.")
        invalid = q.get("invalid_record", 0)
        factor("invalid_records", -min(0.1, 0.01 * invalid), f"{invalid} invalid record(s) were received in the period.")
        if metrics["period_extends_past_stored_data"] and total:
            factor("incomplete_period", -0.1, "The period extends beyond the latest stored event; data for the end of the period may be missing.")
        score = round(max(0.0, min(1.0, score)), 2)
        level = "high" if score >= 0.8 else "medium" if score >= 0.6 else "low"
        return OverallConfidence(level=level, score=score, factors=factors, limitations=limitations)

    # ------------------------------------------------------------------ executive summary
    def executive_text(self, m: dict[str, Any], priority: list[PrioritizedIncident], sections: list[BriefingSection],
                       confidence: OverallConfidence) -> str:
        p = self.p
        where = f" in {', '.join(self.zones)}" if self.zones else ""
        if m["events_total"] == 0:
            text = (f"No events with an occurrence time in {p.label}{where} are stored. This is not confirmation that operations were "
                    "normal: sensors may have been offline or data may not have arrived yet.")
            if m["incidents_active"]:
                text += f" {_plural(m['incidents_active'], 'incident')} nevertheless overlap the period."
            return text
        parts = [f"{p.label}{where}: {_plural(m['events_total'], 'event')} from {_plural(m['devices_reporting'], 'device')} "
                 f"in {_plural(m['zones_reporting'], 'zone')}."]
        if m["incidents_active"]:
            sev = m["incidents_by_severity"]
            parts.append(f"{_plural(m['incidents_active'], 'incident')} had activity ({sev.get('critical', 0)} critical, {sev.get('high', 0)} high); "
                         f"{m['incidents_unresolved']} are unresolved and {m['incidents_new']} started in this period.")
        else:
            parts.append("No incidents were identified from these events.")
        immediate = [x for x in priority if x.immediate_attention]
        if immediate:
            parts.append("Needs attention first: " + "; ".join(f"#{x.incident_id} {x.title} ({x.severity}, {x.status})" for x in immediate[:3]) + ".")
        health = next(s for s in sections if s.key == "device_health")
        if health.findings:
            parts.append(f"Device health: {health.summary}")
        dq = m.get("quality_issues_by_type", {})
        if dq:
            top = sorted(dq.items(), key=lambda kv: -kv[1])[:3]
            parts.append("Data quality: " + ", ".join(f"{n:,} {ISSUE_LABELS.get(t, t)}" for t, n in top) + ".")
        parts.append(f"Overall confidence in this briefing: {confidence.level} ({confidence.score:.2f})"
                     + (f"; main factor: {min(confidence.factors, key=lambda f: f.effect).explanation}" if confidence.factors else ".")
                     )
        return " ".join(parts)


def latest_event_time(db: Session) -> datetime | None:
    return db.scalar(select(func.max(Event.occurred_at)))


def build_briefing(db: Session, period: Period, zones: list[str] | None, s: BriefingSettings, rs: ReasoningSettings,
                   limit: int | None = None, now: datetime | None = None) -> Briefing:
    return BriefingBuilder(db, period, zones, s, rs, limit, now).build()

