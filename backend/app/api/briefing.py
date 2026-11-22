"""GET /briefing (operational briefing), GET /briefing/periods and GET /reasoning/incidents/{id}."""

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.events import MultiValue, _split
from app.core.database import get_db
from app.core.errors import AppError
from app.models.incidents import Incident
from app.models.ingestion import Event
from app.schemas.briefing import Briefing, IncidentReasoning
from app.schemas.events import parse_utc_timestamp
from app.services.briefing import periods
from app.services.briefing.configuration import ShiftDefinition, get_briefing_settings
from app.services.briefing.engine import build_briefing, latest_event_time
from app.services.reasoning.configuration import get_reasoning_settings
from app.services.reasoning.incident_reasoning import incident_reasoning

router = APIRouter(tags=["briefing"])


def _timestamp(name: str, value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return parse_utc_timestamp(value)
    except ValueError as exc:
        raise periods.invalid(name, str(exc)) from None


def _date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise periods.invalid("date", f"invalid date {value!r}; expected YYYY-MM-DD") from None


def resolve_period(db: Session, mode: str, date_: str | None, shift: str | None, start: str | None, end: str | None,
                   tz: str | None) -> periods.Period:
    """Shared by GET /briefing and GET /dashboard/summary so both use identical period rules."""
    s = get_briefing_settings()
    return periods.resolve(mode, periods.zone(tz, s), s, day=_date(date_), shift_name=shift, start=_timestamp("start", start),
                           end=_timestamp("end", end), latest_event=latest_event_time(db))


@router.get("/briefing", response_model=Briefing)
async def get_briefing(
    db: Annotated[Session, Depends(get_db)],
    mode: Annotated[Literal["shift", "day", "custom"], Query(description="shift, day or custom (start + end)")] = "shift",
    date_: Annotated[str | None, Query(alias="date", description="Local date (YYYY-MM-DD) of the day, or the day the shift starts")] = None,
    shift: Annotated[str | None, Query(description="Shift name (see GET /briefing/periods); default: the shift of the latest event")] = None,
    start: Annotated[str | None, Query(description="custom: inclusive start, ISO-8601 with offset")] = None,
    end: Annotated[str | None, Query(description="custom: exclusive end, ISO-8601 with offset")] = None,
    tz: Annotated[str | None, Query(description="IANA time zone for shifts, days and display (default ZENITH_BRIEFING_TIMEZONE)")] = None,
    zone: MultiValue = None,
    limit: Annotated[int | None, Query(ge=1, le=50, description="Items per list section")] = None,
) -> Briefing:
    """Shift, daily or custom operational briefing, grounded in stored events, incidents and quality findings.

    Without parameters it covers the shift containing the most recent stored event (the response says so).
    """
    period = resolve_period(db, mode, date_, shift, start, end, tz)
    return await run_in_threadpool(build_briefing, db, period, _split(zone), get_briefing_settings(), get_reasoning_settings(), limit)


class PeriodsInfo(BaseModel):
    timezone: str
    shifts: list[ShiftDefinition]
    data_start: datetime | None
    data_end: datetime | None
    max_period_days: int
    time_basis: str


@router.get("/briefing/periods", response_model=PeriodsInfo)
def briefing_periods(db: Annotated[Session, Depends(get_db)]) -> PeriodsInfo:
    """Configured shifts and time zone, and the occurrence-time range of the stored events (for period pickers)."""
    s = get_briefing_settings()
    lo, hi = db.execute(select(func.min(Event.occurred_at), func.max(Event.occurred_at))).one()
    return PeriodsInfo(timezone=s.timezone, shifts=s.shifts, data_start=lo, data_end=hi, max_period_days=s.max_period_days,
                       time_basis="Periods select events by occurrence time (occurred_at), half-open [start, end).")


@router.get("/reasoning/incidents/{incident_id}", response_model=IncidentReasoning)
def get_incident_reasoning(db: Annotated[Session, Depends(get_db)], incident_id: Annotated[int, Path()]) -> IncidentReasoning:
    """Priority breakdown and the incident's statements classified as observed / inference / conflict / missing / recommendation."""
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise AppError(f"incident {incident_id} not found", code="not_found", status_code=status.HTTP_404_NOT_FOUND)
    return incident_reasoning(db, incident, get_reasoning_settings())
