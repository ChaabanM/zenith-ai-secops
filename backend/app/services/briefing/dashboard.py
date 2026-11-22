"""Operations overview: a compact projection of the briefing for one period, plus labelled all-time totals.

It reuses ``BriefingBuilder`` so the dashboard and the briefing always agree
(same period rules, counts, priorities and findings); the only additional
queries are the all-time totals and the high-severity quality findings list.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.incidents import Incident
from app.models.ingestion import Event, RawSubmission
from app.models.quality import QualityIssue
from app.schemas.dashboard import BriefingPreview, CountItem, DashboardSummary, QualityIssueRef
from app.services.briefing.configuration import BriefingSettings
from app.services.briefing.engine import CONFLICT_TYPES, BriefingBuilder
from app.services.briefing.periods import Period
from app.services.reasoning.configuration import ReasoningSettings
from app.services.reasoning.grounding import verify_references
from app.services.reasoning.prioritization import UNRESOLVED

PREVIEW = 5


def stored_totals(db: Session) -> dict:
    active = Incident.correlation_state == "active"
    unresolved = Incident.status.in_(UNRESOLVED)
    lo, hi = db.execute(select(func.min(Event.occurred_at), func.max(Event.occurred_at))).one()
    return {
        "events_stored": db.scalar(select(func.count()).select_from(Event)) or 0,
        "submissions_invalid": db.scalar(select(func.count()).where(RawSubmission.status == "invalid")) or 0,
        "submissions_duplicate": db.scalar(select(func.count()).where(RawSubmission.status == "duplicate")) or 0,
        "incidents_active": db.scalar(select(func.count()).where(active)) or 0,
        "incidents_unresolved": db.scalar(select(func.count()).where(active, unresolved)) or 0,
        "incidents_unresolved_high_critical": db.scalar(
            select(func.count()).where(active, unresolved, Incident.severity.in_(("critical", "high")))) or 0,
        "quality_issues": db.scalar(select(func.count()).select_from(QualityIssue)) or 0,
        "data_start": lo.isoformat() if lo else None,
        "data_end": hi.isoformat() if hi else None,
    }


def build_dashboard(db: Session, period: Period, zones: list[str] | None, s: BriefingSettings, rs: ReasoningSettings) -> DashboardSummary:
    builder = BriefingBuilder(db, period, zones, s, rs, limit=PREVIEW)
    b = builder.build()
    sections = {sec.key: sec for sec in b.sections}
    m = b.key_metrics
    q = m.get("quality_issues_by_type", {})

    hs = [QualityIssue.severity.in_(("critical", "high"))]
    high_rows = list(db.scalars(select(QualityIssue).where(*builder.issue_window(), *hs)
                                .order_by(QualityIssue.severity_rank.desc(), QualityIssue.occurred_from.desc(), QualityIssue.id).limit(PREVIEW)))
    high_total = db.scalar(select(func.count()).where(*builder.issue_window(), *hs)) or 0

    health = sections["device_health"].findings
    unrecovered = len(getattr(builder, "health_unrecovered", []))
    drops = sum(1 for f in health if f.basis == "missing")
    reported = sum(1 for f in health if f.basis == "observed")
    spikes = [f for f in sections["trends"].findings if f.quality_issue_ids]

    extra_q = [r.id for r in high_rows]
    unverified = list(b.grounding.unverified_references) + verify_references(db, [], [], extra_q)
    grounding = b.grounding.model_copy(update={
        "quality_issues_referenced": len(set(b.quality_issue_ids) | set(extra_q)),
        "all_references_verified": not unverified, "unverified_references": unverified,
    })

    return DashboardSummary(
        period=b.period,
        generated_at=b.generated_at,
        filters=b.filters,
        scope={
            "period": f"Period figures cover {b.period.label}: events by occurrence time in [start, end); incidents whose activity "
                      "overlaps the period; quality findings whose time span overlaps it.",
            "status": "Incident status is the operator status now, not at the end of the period.",
            "stored_totals": "'All stored data' figures ignore the period and the zone filter.",
            "scores": "Priority, confidence and briefing-confidence values are heuristic scores, not probabilities.",
        },
        period_metrics={
            "events_total": m["events_total"],
            "devices_reporting": m["devices_reporting"],
            "zones_reporting": m["zones_reporting"],
            "incidents_active": m["incidents_active"],
            "incidents_unresolved": m["incidents_unresolved"],
            "incidents_new": m["incidents_new"],
            "incidents_high_critical": m["incidents_by_severity"].get("critical", 0) + m["incidents_by_severity"].get("high", 0),
            "incidents_unresolved_high_critical": m["incidents_unresolved_high_critical"],
            "incidents_by_severity": m["incidents_by_severity"],
            "quality_issues_total": m.get("quality_issues_total", 0),
            "duplicate_submissions_ignored": m["duplicate_submissions_ignored"],
            "devices_with_health_reports": reported,
            "devices_without_reported_recovery": unrecovered,
            "devices_with_volume_drop": drops,
            "device_health_concerns": unrecovered + drops,
            "latest_stored_event_at": m["latest_stored_event_at"],
            "period_extends_past_stored_data": m["period_extends_past_stored_data"],
        },
        stored_totals=stored_totals(db),
        priority_incidents=b.priority_incidents[:PREVIEW],
        events_by_source=[CountItem(value=k, count=v) for k, v in m["events_by_source"].items()],
        events_per_hour=m["events_per_hour"],
        spikes=spikes[:PREVIEW],
        quality_by_type=[CountItem(value=k, count=v) for k, v in sorted(q.items(), key=lambda kv: (-kv[1], kv[0]))],
        quality_high_severity=[QualityIssueRef(id=r.id, issue_type=r.issue_type, severity=r.severity, title=r.title,
                                               occurred_from=r.occurred_from, occurred_to=r.occurred_to, event_count=r.event_count)
                               for r in high_rows],
        quality_high_severity_total=high_total,
        uncertainty={
            "low_confidence_detections": q.get("low_confidence_detection", 0),
            "unlinkable_events": q.get("uncorrelatable_event", 0),
            "conflicting_record_sets": sum(q.get(t, 0) for t in CONFLICT_TYPES),
            "incidents_with_conflicts": m["incidents_with_conflicts"],
            "delayed_events": q.get("delayed_event", 0),
        },
        device_health=health[:PREVIEW * 2],
        device_health_note=(
            "Device health comes only from device-health reports and from sharp drops in a device's event volume. Devices that "
            "send no health reports cannot be assessed, and no reports does not mean a device is healthy."
        ),
        briefing=BriefingPreview(executive_summary=b.executive_summary, recommendations=b.recommended_actions[:3],
                                 recommendations_total=len(b.recommended_actions), confidence=b.confidence),
        grounding=grounding,
    )
