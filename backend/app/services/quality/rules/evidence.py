"""K. low-confidence detections and L. events that cannot be reliably correlated.

Phase 3 does not correlate events. "Cannot be reliably correlated" is identified
conservatively, only for observations whose own content makes linkage unreliable:

* UNC-001 an observation of a person/vehicle without a usable identifier
  (ANPR read with no or partial plate; CCTV person/vehicle detection without a
  track id; face result with only a weak candidate identity);
* UNC-002 an event with no location at all (neither zone nor coordinates);
* UNC-003 a manual incident report that references no events and has no
  corroborating medium-or-higher alert in the same zone within the window;
* contradictory identifiers (weak face candidate vs badge holder) come from
  rules/identity.py (SNS-002, issue type uncorrelatable_event).

Normal standalone events (ordinary reads, telemetry, shift notes) are never flagged.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, or_, select

from app.models.ingestion import Event
from app.services.quality.rules.base import (
    EVENT_COLUMNS,
    QualityContext,
    Rule,
    add_facets,
    describe,
    event_evidence,
    fmt_duration,
    fmt_time,
    payload_field,
)
from app.services.quality.schemas import IssueDraft

UNCERTAIN_READS = ("partial", "unreadable")


class LowConfidenceRule(Rule):
    """K. Detection confidence below the source threshold, or the sensor itself reports an uncertain result."""

    rule_id = "LCF-001"
    issue_type = "low_confidence_detection"
    summary = "A detection whose evidence is too uncertain to rely on without corroboration."

    def detect(self, ctx: QualityContext) -> Iterator[IssueDraft]:
        thresholds = ctx.settings.low_confidence_thresholds
        default = thresholds.get("default", 0.5)
        highest = max(thresholds.values())
        read_status, match_status = payload_field("read_status"), payload_field("match_status")
        stmt = (
            select(*EVENT_COLUMNS, read_status.label("read_status"), match_status.label("match_status"))
            .where(or_(Event.confidence < highest, read_status.in_(UNCERTAIN_READS), match_status == "uncertain"))
            .order_by(Event.id)
        )
        for row in ctx.stream(stmt):
            limit = thresholds.get(row.source_type, default)
            reasons = []
            if row.confidence is not None and row.confidence < limit:
                reasons.append(f"confidence {row.confidence:.2f} is below the {limit:.2f} threshold for {row.source_type}")
            if row.read_status in UNCERTAIN_READS:
                reasons.append(f"the sensor reports the plate read as '{row.read_status}'")
            if row.match_status == "uncertain":
                reasons.append("face recognition reports only an uncertain candidate match")
            if not reasons:
                continue
            alert = row.severity in ("high", "critical")
            draft = self.draft(
                str(row.id),
                subtype=row.source_type,
                severity="medium" if alert else "low",
                title=f"Low-confidence {row.source_type} {row.event_type} on {row.device_id}"
                + (f" ({row.confidence:.0%})" if row.confidence is not None else ""),
                description=(
                    "The detection is uncertain. Low confidence means the evidence is weak, not that the detection is false: "
                    "do not act on it alone; look for corroborating observations."
                    + (" This is a high-severity alert resting on weak evidence." if alert else "")
                ),
                detection_reason=f"{describe(row)} at {fmt_time(row.occurred_at)}: " + "; ".join(reasons) + ".",
                occurred_from=row.occurred_at,
                occurred_to=row.occurred_at,
                confidence=1.0,
                uncertainty=f"Reported detection confidence: {row.confidence if row.confidence is not None else 'not provided'}.",
                details={"confidence": row.confidence, "threshold": limit, "read_status": row.read_status, "match_status": row.match_status},
                evidence=[event_evidence(row, "low_confidence_event")],
            )
            yield add_facets(draft, [row])


class MissingIdentifierRule(Rule):
    """L / UNC-001. An observation of a person or vehicle with no usable identifier."""

    rule_id = "UNC-001"
    issue_type = "uncorrelatable_event"
    summary = "An observation lacks the identifier needed to link it to other evidence."

    TRACKED = ("person_detected", "vehicle_detected", "loitering_detected")

    def detect(self, ctx: QualityContext) -> Iterator[IssueDraft]:
        plate, track, match_status, subject = (payload_field(p) for p in ("plate_number", "track_id", "match_status", "subject_id"))
        stmt = (
            select(*EVENT_COLUMNS, plate.label("plate"), track.label("track"), match_status.label("match_status"), subject.label("subject"),
                   payload_field("candidate_subject_id").label("candidate"))
            .where(
                or_(
                    and_(Event.source_type == "anpr", or_(plate.is_(None), plate.like("%?%"))),
                    and_(Event.source_type == "cctv", Event.event_type.in_(self.TRACKED), track.is_(None)),
                    and_(Event.source_type == "face_recognition", match_status == "uncertain", subject.is_(None)),
                )
            )
            .order_by(Event.id)
        )
        for row in ctx.stream(stmt):
            if row.source_type == "anpr":
                what = "no plate could be read" if row.plate is None else f"only a partial plate '{row.plate}' was read"
                subtype, missing = "missing_plate", "A full plate number (or another vehicle identifier)."
            elif row.source_type == "cctv":
                what = "the detection has no track id"
                subtype, missing = "missing_track", "A track id or appearance attributes to follow the subject across cameras."
            else:
                what = "the face was not identified" + (f" (weak candidate {row.candidate} only)" if row.candidate else "")
                subtype, missing = "uncertain_identity", "A confident identification."
            draft = self.draft(
                str(row.id),
                subtype=subtype,
                severity="low",
                title=f"{row.source_type} {row.event_type} on {row.device_id} cannot be linked to an entity",
                description=(
                    "This observation concerns a person or vehicle but carries no reliable identifier, so it can only be linked "
                    "to other events by time and place, which is not sufficient evidence on its own."
                ),
                detection_reason=f"{describe(row)} at {fmt_time(row.occurred_at)}: {what}.",
                occurred_from=row.occurred_at,
                occurred_to=row.occurred_at,
                confidence=0.9,
                uncertainty="Any relationship with nearby events would be an assumption.",
                missing_evidence=missing,
                details={"criterion": subtype, "plate_number": row.plate, "track_id": row.track},
                evidence=[event_evidence(row, "unlinkable_event")],
            )
            yield add_facets(draft, [row])


class MissingLocationRule(Rule):
    """L / UNC-002. No zone and no coordinates."""

    rule_id = "UNC-002"
    issue_type = "uncorrelatable_event"
    summary = "An event has no location, so it cannot be placed or linked spatially."

    def detect(self, ctx: QualityContext) -> Iterator[IssueDraft]:
        stmt = select(*EVENT_COLUMNS).where(Event.zone.is_(None), Event.latitude.is_(None)).order_by(Event.id)
        for row in ctx.stream(stmt):
            draft = self.draft(
                str(row.id),
                subtype="missing_location",
                severity="low",
                title=f"{row.source_type} {row.event_type} on {row.device_id} has no location",
                description="Without a zone or coordinates this event cannot be tied to a site, and spatial correlation is impossible.",
                detection_reason=f"{describe(row)} at {fmt_time(row.occurred_at)} was submitted without a zone and without coordinates.",
                occurred_from=row.occurred_at,
                occurred_to=row.occurred_at,
                confidence=1.0,
                missing_evidence="Zone or coordinates (or an asset register entry for the device).",
                details={"criterion": "missing_location"},
                evidence=[event_evidence(row, "unlocated_event")],
            )
            yield add_facets(draft, [row])


class UncorroboratedReportRule(Rule):
    """L / UNC-003. A manual incident report with no references and nothing corroborating it nearby."""

    rule_id = "UNC-003"
    issue_type = "uncorrelatable_event"
    summary = "A manual report cannot be linked to any sensor evidence."

    def detect(self, ctx: QualityContext) -> Iterator[IssueDraft]:
        window = timedelta(seconds=ctx.settings.report_corroboration_window_seconds)
        refs, category = payload_field("referenced_event_ids"), payload_field("report_category")
        reports = ctx.db.execute(
            select(*EVENT_COLUMNS, category.label("category"), payload_field("summary").label("summary"))
            .where(Event.source_type == "operator_report", Event.event_type != "shift_note", refs.is_(None), Event.zone.is_not(None))
            .order_by(Event.id)
        ).all()
        for report in reports:
            corroborating = ctx.db.execute(
                select(Event.id)
                .where(
                    Event.zone == report.zone,
                    Event.occurred_at >= report.occurred_at - window,
                    Event.occurred_at <= report.occurred_at + window,
                    Event.source_type != "operator_report",
                    Event.severity.in_(["medium", "high", "critical"]),
                )
                .limit(1)
            ).first()
            if corroborating:
                continue
            draft = self.draft(
                str(report.id),
                subtype="uncorroborated_report",
                severity="low",
                title=f"Operator report at {report.zone} has no supporting sensor evidence",
                description=(
                    "The report references no events, and no sensor raised a significant alert in the same zone around that time. "
                    "It may still be accurate, but there is nothing to link it to."
                ),
                detection_reason=(
                    f"Report {report.event_id} ({report.category or 'uncategorised'}) at {fmt_time(report.occurred_at)}: "
                    f"\"{report.summary or ''}\". No referenced_event_ids, and no medium-or-higher sensor event in {report.zone} "
                    f"within ±{fmt_duration(window.total_seconds())}."
                ),
                occurred_from=report.occurred_at,
                occurred_to=report.occurred_at,
                confidence=0.7,
                uncertainty="Lower-severity events nearby may or may not be related; the data is insufficient to decide.",
                missing_evidence="Event references from the operator, or a corroborating sensor observation.",
                details={"criterion": "uncorroborated_report", "window_seconds": window.total_seconds(), "report_category": report.category},
                evidence=[event_evidence(report, "uncorroborated_report")],
            )
            yield add_facets(draft, [report])
