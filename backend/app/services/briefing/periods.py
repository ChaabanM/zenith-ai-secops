"""Briefing periods: shift, day or custom window, resolved to explicit UTC bounds.

All bounds are half-open ``[start, end)``. Shifts and days are defined in a
display time zone (IANA name), so a Dubai night shift (22:00-06:00 +04:00) is
18:00-02:00 UTC. Storage and queries always use UTC.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.errors import AppError
from app.services.briefing.configuration import BriefingSettings, ShiftDefinition


@dataclass
class Period:
    mode: str
    label: str
    start: datetime  # UTC
    end: datetime  # UTC, exclusive
    tz: ZoneInfo
    default_reason: str | None = None
    query: dict[str, str] | None = None  # GET /briefing parameters that reproduce exactly this period

    @property
    def length(self) -> timedelta:
        return self.end - self.start

    def baselines(self, count: int) -> list[tuple[datetime, datetime]]:
        """Comparison windows: same time on previous days for periods up to 24 h, else previous equal windows."""
        step = timedelta(days=1) if self.length <= timedelta(days=1) else self.length
        return [(self.start - step * k, self.end - step * k) for k in range(1, count + 1)]


def invalid(field: str, message: str) -> AppError:
    return AppError(message, code="invalid_parameter", status_code=422, details=[{"field": field, "message": message}])


def zone(name: str | None, settings: BriefingSettings) -> ZoneInfo:
    try:
        return ZoneInfo(name or settings.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise invalid("tz", f"unknown time zone {name!r}; use an IANA name such as UTC or Asia/Dubai") from None


def _shift_bounds(shift: ShiftDefinition, day: date, shifts: list[ShiftDefinition], tz: ZoneInfo) -> tuple[datetime, datetime]:
    ordered = sorted(shifts, key=lambda s: s.start)
    idx = ordered.index(shift)
    start_t = time.fromisoformat(shift.start)
    nxt = ordered[(idx + 1) % len(ordered)]
    end_t = time.fromisoformat(nxt.start)
    start = datetime.combine(day, start_t, tzinfo=tz)
    end_day = day if end_t > start_t else day + timedelta(days=1)
    end = datetime.combine(end_day, end_t, tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)


def shift_containing(instant: datetime, settings: BriefingSettings, tz: ZoneInfo) -> tuple[ShiftDefinition, date]:
    local_day = instant.astimezone(tz).date()
    for day in (local_day, local_day - timedelta(days=1)):
        for shift in settings.shifts:
            s, e = _shift_bounds(shift, day, settings.shifts, tz)
            if s <= instant < e:
                return shift, day
    raise invalid("shift", "the configured shifts do not cover the whole day")  # pragma: no cover - configuration error


def _fmt(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%d %b %Y %H:%M")


def resolve(
    mode: str,
    tz: ZoneInfo,
    settings: BriefingSettings,
    *,
    day: date | None = None,
    shift_name: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    latest_event: datetime | None = None,
    now: datetime | None = None,
) -> Period:
    anchor = latest_event or now or datetime.now(UTC)
    anchor_reason = "the most recent stored event" if latest_event else "the current time (no events are stored)"
    anchor_text = f"{anchor_reason} ({_fmt(anchor, tz)})"
    tzname = str(tz.key)
    if mode == "custom":
        if start is None or end is None:
            raise invalid("start" if start is None else "end", "custom periods need both start and end (ISO-8601 with offset)")
        if end <= start:
            raise invalid("end", "end must be after start")
        if end - start > timedelta(days=settings.max_period_days):
            raise invalid("end", f"periods are limited to {settings.max_period_days} days")
        return Period("custom", f"{_fmt(start, tz)} – {_fmt(end, tz)} ({tzname})", start, end, tz,
                      query={"mode": "custom", "start": start.isoformat(), "end": end.isoformat(), "tz": tzname})
    if start is not None or end is not None:
        raise invalid("start" if start is not None else "end", "start/end are only accepted with mode=custom")
    if mode == "day":
        reason = None
        if day is None:
            day = anchor.astimezone(tz).date()
            reason = f"No date given: the most recent day at least half covered by stored data, based on {anchor_text}."
            if anchor - datetime.combine(day, time(0), tzinfo=tz) < timedelta(hours=12):
                day -= timedelta(days=1)
        s = datetime.combine(day, time(0), tzinfo=tz).astimezone(UTC)
        e = datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz).astimezone(UTC)
        return Period("day", f"{day.strftime('%d %b %Y')} ({tzname})", s, e, tz, reason,
                      query={"mode": "day", "date": day.isoformat(), "tz": tzname})
    if mode == "shift":
        reason = None
        if shift_name is None and day is None:
            shift, day = shift_containing(anchor, settings, tz)
            s0, e0 = _shift_bounds(shift, day, settings.shifts, tz)
            if anchor - s0 < (e0 - s0) / 2:  # mostly in the future of the data: use the previous shift
                shift, day = shift_containing(s0 - timedelta(seconds=1), settings, tz)
            reason = f"No shift given: the most recent shift at least half covered by stored data, based on {anchor_text}."
        else:
            if shift_name is None:
                raise invalid("shift", f"choose a shift: {[s.name for s in settings.shifts]}")
            shift = next((s for s in settings.shifts if s.name == shift_name), None)
            if shift is None:
                raise invalid("shift", f"unknown shift {shift_name!r}; configured shifts: {[s.name for s in settings.shifts]}")
            if day is None:
                day = anchor.astimezone(tz).date()
                reason = f"No date given: {shift.label.lower()} starting on the day of {anchor_reason}."
        s, e = _shift_bounds(shift, day, settings.shifts, tz)
        return Period("shift", f"{shift.label}, {_fmt(s, tz)} – {_fmt(e, tz)} ({tzname})", s, e, tz, reason,
                      query={"mode": "shift", "date": day.isoformat(), "shift": shift.name, "tz": tzname})
    raise invalid("mode", "mode must be shift, day or custom")
