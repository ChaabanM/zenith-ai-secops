"""F. conflicting locations and G. contradictory vehicle details, linked by plate number.

Only observations that carry a fully read plate are compared (ANPR reads with
read_status 'read', enforcement/phone/seatbelt cameras, and fleet GPS whose
payload names its own plate). Observations are streamed sorted by plate and time;
each observation is compared with the latest observation of the same plate from
every other device (a handful per plate), never with the whole dataset.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sqlalchemy import or_, select

from app.models.ingestion import Event
from app.services.quality.rules.base import (
    EVENT_COLUMNS,
    QualityContext,
    Rule,
    add_facets,
    distance_km,
    event_evidence,
    fmt_duration,
    fmt_time,
    payload_field,
)
from app.services.quality.schemas import IssueDraft

PLATE_SOURCES = ("anpr", "traffic_violation", "mobile_phone_detection", "seatbelt_detection", "gps_fleet")


def plate_observations(ctx: QualityContext) -> Iterator[Any]:
    plate = payload_field("plate_number")
    read_status = payload_field("read_status")
    stmt = (
        select(
            *EVENT_COLUMNS,
            plate.label("plate"),
            payload_field("vehicle_make").label("make"),
            payload_field("vehicle_model").label("model"),
            payload_field("vehicle_colour").label("colour"),
        )
        .where(
            Event.source_type.in_(PLATE_SOURCES),
            plate.is_not(None),
            plate.not_like("%?%"),  # partial reads are not identifiers
            or_(read_status.is_(None), read_status == "read"),
        )
        .order_by(plate, Event.occurred_at, Event.id)
    )
    yield from ctx.stream(stmt)


def _place(row: Any) -> str:
    return f"{row.device_id} ({row.zone or 'no zone'})"


class LocationConflictRule(Rule):
    """F. The same plate observed in two places that cannot both be true."""

    rule_id = "LOC-001"
    issue_type = "location_conflict"
    summary = "One vehicle is reported in two places further apart than it could travel in the time between."

    def detect(self, ctx: QualityContext) -> Iterator[IssueDraft]:
        s = ctx.settings
        plate = None
        last_by_device: dict[str, Any] = {}  # latest located observation of this plate per device
        for row in plate_observations(ctx):
            if row.plate != plate:
                plate, last_by_device = row.plate, {}
            if row.latitude is None:
                continue
            # compare with the latest position from every device, including this device's own previous fix
            # (a GPS unit that jumps kilometres and back within seconds is a positioning fault)
            for other in sorted(last_by_device.values(), key=lambda r: (r.occurred_at, r.id)):
                issue = self._check(other, row, s)
                if issue:
                    yield issue
            last_by_device[row.device_id] = row

    def _check(self, a: Any, b: Any, s: Any) -> IssueDraft | None:
        raw_km = distance_km(a.latitude, a.longitude, b.latitude, b.longitude)
        allowance = s.gps_error_allowance_km if "gps_fleet" in (a.source_type, b.source_type) else 0.0
        km = raw_km - allowance
        if km <= s.location_min_distance_km:
            return None
        seconds = (b.occurred_at - a.occurred_at).total_seconds()
        speed = float("inf") if seconds <= 0 else km / (seconds / 3600)
        if speed <= s.max_plausible_speed_kph:
            return None
        speed_text = "simultaneously" if seconds <= 0 else f"{fmt_duration(seconds)} apart, implying at least {speed:,.0f} km/h"
        draft = self.draft(
            f"{a.id}:{b.id}",
            subtype="impossible_travel" if a.device_id != b.device_id else "position_jump",
            severity="medium",
            title=f"Vehicle {a.plate} reported {raw_km:.1f} km apart within {fmt_duration(seconds)}",
            description=(
                "Two observations of the same plate cannot both be correct: the vehicle could not have travelled between "
                "the two places in the time available. One of them is a positioning fault, a misread, or a cloned plate."
            ),
            detection_reason=(
                f"Plate {a.plate} (the shared identifier) was observed by {_place(a)} at {fmt_time(a.occurred_at)} and by "
                f"{_place(b)} at {fmt_time(b.occurred_at)}: {raw_km:.1f} km apart, {speed_text}. The plausible maximum is "
                f"{s.max_plausible_speed_kph:.0f} km/h; distances under {s.location_min_distance_km:.1f} km"
                + (f" (+{allowance:.1f} km GPS allowance)" if allowance else "")
                + " are treated as positioning error."
            ),
            occurred_from=a.occurred_at,
            occurred_to=b.occurred_at,
            confidence=0.85,
            uncertainty="The data does not show which observation is wrong (GPS fault, misread or cloned plate).",
            details={
                "plate_number": a.plate,
                "distance_km": round(raw_km, 3),
                "seconds_apart": seconds,
                "implied_speed_kph": None if speed == float("inf") else round(speed, 1),
                "max_plausible_speed_kph": s.max_plausible_speed_kph,
                "link_basis": "identical plate_number",
                "positions": [
                    {"event_id": r.event_id, "device_id": r.device_id, "zone": r.zone, "latitude": r.latitude, "longitude": r.longitude}
                    for r in (a, b)
                ],
            },
            evidence=[event_evidence(a, "earlier_observation"), event_evidence(b, "later_observation")],
        )
        return add_facets(draft, [a, b])


class VehicleDetailConflictRule(Rule):
    """G. Same plate within a short window, different make or colour."""

    rule_id = "SNS-001"
    issue_type = "conflicting_sensor_results"
    summary = "Two sensors read the same plate but describe different vehicles."

    def detect(self, ctx: QualityContext) -> Iterator[IssueDraft]:
        window = ctx.settings.vehicle_detail_window_seconds
        reference = None  # most recent observation of this plate that carries vehicle details
        for row in plate_observations(ctx):
            if reference is not None and reference.plate != row.plate:
                reference = None
            if row.make is None and row.colour is None:
                continue
            if reference is not None and reference.device_id != row.device_id:
                gap = (row.occurred_at - reference.occurred_at).total_seconds()
                diffs = [
                    (name, getattr(reference, name), getattr(row, name))
                    for name in ("make", "colour")
                    if getattr(reference, name) and getattr(row, name)
                    and str(getattr(reference, name)).casefold() != str(getattr(row, name)).casefold()
                ]
                if diffs and gap <= window:
                    yield self._issue(reference, row, diffs, gap)
            reference = row

    def _issue(self, a: Any, b: Any, diffs: list[tuple[str, Any, Any]], gap: float) -> IssueDraft:
        def vehicle(r: Any) -> str:
            return " ".join(str(x) for x in (r.colour, r.make, r.model) if x)

        diff_text = ", ".join(f"{name}: {x} vs {y}" for name, x, y in diffs)
        draft = self.draft(
            f"{a.id}:{b.id}",
            subtype="vehicle_identification",
            severity="medium",
            title=f"Plate {a.plate}: {vehicle(a)} vs {vehicle(b)}",
            description=(
                "Two sensors read the same plate within a short time but describe different vehicles. Possible causes: a "
                "cloned or swapped plate, or a make/colour recognition error. Both records are kept unchanged."
            ),
            detection_reason=(
                f"{a.device_id} ({a.source_type}) at {fmt_time(a.occurred_at)} and {b.device_id} ({b.source_type}) "
                f"{fmt_duration(gap)} later both read plate {a.plate} (the shared identifier), but report {diff_text}."
            ),
            occurred_from=a.occurred_at,
            occurred_to=b.occurred_at,
            confidence=0.8,
            uncertainty="It is not known which description is correct, or whether the plate itself was misread.",
            details={
                "plate_number": a.plate,
                "differences": [{"field": f"vehicle_{n}", "first": x, "second": y} for n, x, y in diffs],
                "seconds_apart": gap,
                "link_basis": "identical plate_number",
            },
            evidence=[event_evidence(a, "first_observation"), event_evidence(b, "contradicting_observation")],
        )
        return add_facets(draft, [a, b])
