"""C. delayed events, D. out-of-order arrival, E. conflicting timestamps."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, select

from app.models.ingestion import Event, RawSubmission
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
    span,
)
from app.services.quality.schemas import IssueDraft


class DelayedEventRule(Rule):
    """C. received_at - occurred_at above a per-source threshold."""

    rule_id = "DLY-001"
    issue_type = "delayed_event"
    summary = "The event reached the platform much later than it occurred."

    def detect(self, ctx: QualityContext) -> Iterator[IssueDraft]:
        thresholds = ctx.settings.delay_threshold_seconds
        default = thresholds.get("default", 300.0)
        lowest = min(thresholds.values())
        stmt = select(*EVENT_COLUMNS).where(Event.delay_seconds > lowest).order_by(Event.id)
        for row in ctx.stream(stmt):
            limit = thresholds.get(row.source_type, default)
            if row.delay_seconds <= limit:
                continue
            severity = "medium" if row.delay_seconds >= ctx.settings.delay_medium_seconds else "low"
            draft = self.draft(
                str(row.id),
                severity=severity,
                title=f"{row.source_type} event {row.event_id} arrived {fmt_duration(row.delay_seconds)} late",
                description=(
                    "The observation is valid and has been kept, but it arrived too late to support real-time response. "
                    "Views that assume arrival order (live feeds, shift handovers) may have missed it."
                ),
                detection_reason=(
                    f"{describe(row)} occurred at {fmt_time(row.occurred_at)} and was received at {fmt_time(row.received_at)}: "
                    f"an observed delay of {fmt_duration(row.delay_seconds)} ({row.delay_seconds:.0f} s), above the "
                    f"{fmt_duration(limit)} threshold for {row.source_type}. Shorter, ordinary transmission delays are not reported."
                ),
                occurred_from=row.occurred_at,
                occurred_to=row.occurred_at,
                confidence=1.0,
                details={"delay_seconds": row.delay_seconds, "threshold_seconds": limit, "received_at": row.received_at.isoformat()},
                evidence=[event_evidence(row, "delayed_event")],
            )
            yield add_facets(draft, [row])


class OutOfOrderRule(Rule):
    """D. Per device, an event ingested after another event of the same device that occurred later.

    Ordering basis: ingestion sequence (raw-submission id, i.e. the order records
    were received by the platform) within one device. Different devices and sources
    interleave legitimately, so only same-device inversions larger than the jitter
    tolerance are reported. The reference is the latest-occurring event ingested so
    far from that device, counting only events with plausible timestamps (an event
    whose clock runs ahead is reported by TSC-001 and must not make every later
    event look late). All events that arrived "behind" the same reference event form
    one finding (one late delivery burst).
    """

    rule_id = "ORD-001"
    issue_type = "out_of_order_arrival"
    summary = "Events from one device arrived in a different order than they occurred."

    def detect(self, ctx: QualityContext) -> Iterator[IssueDraft]:
        tolerances = ctx.settings.out_of_order_tolerance_seconds
        default = tolerances.get("default", 20.0)
        skew = ctx.settings.clock_skew_tolerance_seconds
        stmt = (
            select(*EVENT_COLUMNS, RawSubmission.batch_id)
            .join(RawSubmission, RawSubmission.id == Event.submission_id)
            .order_by(Event.device_id, Event.submission_id)
        )
        current_device = None
        latest: Any = None
        group: dict[str, Any] | None = None
        for row in ctx.stream(stmt):
            if row.device_id != current_device:
                if group:
                    yield self._issue(group, tolerance)
                current_device, latest, group = row.device_id, None, None
            tolerance = timedelta(seconds=tolerances.get(row.source_type, default))
            if latest is not None and row.occurred_at < latest.occurred_at - tolerance:
                if group is None or group["reference"].id != latest.id:
                    if group:
                        yield self._issue(group, tolerance)
                    group = {"reference": latest, "late": []}
                group["late"].append(row)
            trusted = row.delay_seconds is None or row.delay_seconds >= -skew
            if trusted and (latest is None or row.occurred_at > latest.occurred_at):
                latest = row
        if group:
            yield self._issue(group, tolerance)

    def _issue(self, group: dict[str, Any], tolerance: timedelta) -> IssueDraft:
        ref, late = group["reference"], group["late"]
        rows = [*late, ref]
        start, end = span(rows)
        max_lag = max((ref.occurred_at - r.occurred_at).total_seconds() for r in late)
        examples = "; ".join(f"{r.event_id} (occurred {fmt_time(r.occurred_at)})" for r in late[:3])
        batches = sorted({r.batch_id for r in late})
        draft = self.draft(
            f"{ref.device_id}:{ref.id}",
            severity="low",
            title=f"{len(late)} event(s) from {ref.device_id} arrived out of order",
            description=(
                "The device's events were not delivered in the order they happened. Event-time views remain correct "
                "(they sort by occurred_at), but anything that processes arrivals as a live sequence would see a distorted timeline."
            ),
            detection_reason=(
                f"Ordering basis: ingestion sequence per device. {ref.event_id} (occurred {fmt_time(ref.occurred_at)}) was ingested first; "
                f"{len(late)} event(s) from {ref.device_id} that occurred earlier arrived after it (largest inversion {fmt_duration(max_lag)}; "
                f"inversions up to {fmt_duration(tolerance.total_seconds())} are normal jitter and ignored). Examples: {examples}."
            ),
            occurred_from=start,
            occurred_to=end,
            confidence=1.0,
            details={
                "ordering_basis": "raw submission sequence (ingestion order) within one device",
                "reference_event_id": ref.event_id,
                "batch_ids": batches,
                "out_of_order_count": len(late),
                "max_inversion_seconds": max_lag,
                "tolerance_seconds": tolerance.total_seconds(),
            },
            evidence=[event_evidence(r, "late_arrival") for r in late] + [event_evidence(ref, "occurred_later_but_arrived_first")],
        )
        return add_facets(draft, rows)


class ReceivedBeforeOccurredRule(Rule):
    """E1. received_at earlier than occurred_at beyond the clock-skew tolerance."""

    rule_id = "TSC-001"
    issue_type = "timestamp_conflict"
    summary = "An event claims to have been received before it occurred."

    def detect(self, ctx: QualityContext) -> Iterator[IssueDraft]:
        tolerance = ctx.settings.clock_skew_tolerance_seconds
        stmt = select(*EVENT_COLUMNS).where(Event.delay_seconds < -tolerance).order_by(Event.id)
        for row in ctx.stream(stmt):
            skew = -row.delay_seconds
            draft = self.draft(
                str(row.id),
                subtype="received_before_occurred",
                severity="medium",
                title=f"{row.device_id} reports {row.event_id} {fmt_duration(skew)} in the future",
                description=(
                    "The event's own timestamps are impossible: it was received before it occurred. The device clock is "
                    "probably wrong, so its occurred_at (and its place in any timeline) cannot be trusted."
                ),
                detection_reason=(
                    f"{describe(row)}: occurred_at {fmt_time(row.occurred_at)} is {fmt_duration(skew)} after received_at "
                    f"{fmt_time(row.received_at)}. Differences up to {fmt_duration(tolerance)} are accepted as normal clock skew."
                ),
                occurred_from=row.occurred_at,
                occurred_to=row.occurred_at,
                confidence=1.0,
                uncertainty="The true occurrence time is unknown; received_at is an upper bound.",
                details={"skew_seconds": skew, "tolerance_seconds": tolerance, "received_at": row.received_at.isoformat()},
                evidence=[event_evidence(row, "inconsistent_event")],
            )
            yield add_facets(draft, [row])


class LinkedRadarRule(Rule):
    """E2 / G. A violation record that references a radar track disagrees with that radar event.

    The link is explicit evidence (``radar_device_id`` + ``radar_track_id`` in the
    violation payload), so a mismatch is a genuine contradiction about one
    occurrence. Track ids are recycled, so only radar events within
    ``linked_track_search_seconds`` are considered and the closest one is used.
    """

    rule_id = "TSC-002"
    issue_type = "timestamp_conflict"
    summary = "Two linked records of the same vehicle pass disagree on time or speed."

    def detect(self, ctx: QualityContext) -> Iterator[IssueDraft]:
        s = ctx.settings
        search = timedelta(seconds=s.linked_track_search_seconds)
        radar_device, radar_track = payload_field("radar_device_id"), payload_field("radar_track_id")
        violations = ctx.db.execute(
            select(*EVENT_COLUMNS, radar_device.label("radar_device"), radar_track.label("radar_track"), payload_field("measured_speed_kph").label("speed"))
            .where(Event.source_type == "traffic_violation", radar_track.is_not(None), radar_device.is_not(None))
            .order_by(Event.id)
        ).all()
        track = payload_field("track_id")
        for v in violations:
            candidates = ctx.db.execute(
                select(*EVENT_COLUMNS, payload_field("speed_kph").label("speed"))
                .where(
                    and_(
                        Event.device_id == v.radar_device,
                        Event.occurred_at >= v.occurred_at - search,
                        Event.occurred_at <= v.occurred_at + search,
                        track == v.radar_track,
                    )
                )
            ).all()
            if not candidates:
                continue
            radar = min(candidates, key=lambda r: (abs((r.occurred_at - v.occurred_at).total_seconds()), r.id))
            gap = (v.occurred_at - radar.occurred_at).total_seconds()
            link = f"{v.event_id} references radar track {v.radar_track} on {v.radar_device}, recorded by {radar.event_id}"
            if abs(gap) > s.linked_timestamp_tolerance_seconds:
                draft = self.draft(
                    f"{radar.id}:{v.id}",
                    subtype="linked_records_disagree",
                    severity="medium",
                    title=f"Radar and enforcement camera disagree on the time of track {v.radar_track} by {fmt_duration(gap)}",
                    description=(
                        "Two records that explicitly describe the same vehicle pass give different times. One device clock is "
                        "probably drifting; any evidence package built from these records has an unreliable timeline."
                    ),
                    detection_reason=(
                        f"{link}. The violation says {fmt_time(v.occurred_at)}, the radar says {fmt_time(radar.occurred_at)}: a difference of "
                        f"{fmt_duration(gap)} where at most {fmt_duration(s.linked_timestamp_tolerance_seconds)} is expected for the same pass."
                    ),
                    occurred_from=min(v.occurred_at, radar.occurred_at),
                    occurred_to=max(v.occurred_at, radar.occurred_at),
                    confidence=0.9,
                    uncertainty="The data does not show which device clock is wrong.",
                    details={"difference_seconds": gap, "tolerance_seconds": s.linked_timestamp_tolerance_seconds, "radar_track_id": v.radar_track,
                             "link_basis": "radar_device_id + radar_track_id in the violation payload"},
                    evidence=[event_evidence(radar, "radar_measurement"), event_evidence(v, "violation_record")],
                )
                yield add_facets(draft, [radar, v])
            elif v.speed is not None and radar.speed is not None and abs(float(v.speed) - float(radar.speed)) > s.linked_speed_tolerance_kph:
                diff = float(v.speed) - float(radar.speed)
                draft = self.draft(
                    f"speed:{radar.id}:{v.id}",
                    issue_type="conflicting_sensor_results",
                    subtype="traffic_measurement",
                    severity="medium",
                    title=f"Violation speed {v.speed} km/h does not match radar {radar.speed} km/h (track {v.radar_track})",
                    description="The enforcement record and the radar measurement it cites report different speeds for the same pass.",
                    detection_reason=f"{link}. Reported speeds differ by {diff:+.0f} km/h (tolerance {s.linked_speed_tolerance_kph:.0f} km/h).",
                    occurred_from=min(v.occurred_at, radar.occurred_at),
                    occurred_to=max(v.occurred_at, radar.occurred_at),
                    confidence=0.9,
                    uncertainty="The data does not show which measurement is correct.",
                    details={"violation_speed_kph": v.speed, "radar_speed_kph": radar.speed, "radar_track_id": v.radar_track},
                    evidence=[event_evidence(radar, "radar_measurement"), event_evidence(v, "violation_record")],
                )
                yield add_facets(draft, [radar, v])
