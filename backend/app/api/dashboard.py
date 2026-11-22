"""GET /dashboard/summary: the operations overview for one reporting period."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.api.briefing import resolve_period
from app.api.events import MultiValue, _split
from app.core.database import get_db
from app.schemas.dashboard import DashboardSummary
from app.services.briefing.configuration import get_briefing_settings
from app.services.briefing.dashboard import build_dashboard
from app.services.reasoning.configuration import get_reasoning_settings

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/summary", response_model=DashboardSummary)
async def dashboard_summary(
    db: Annotated[Session, Depends(get_db)],
    mode: Annotated[Literal["shift", "day", "custom"], Query(description="Same period rules as GET /briefing")] = "shift",
    date_: Annotated[str | None, Query(alias="date")] = None,
    shift: str | None = None,
    start: str | None = None,
    end: str | None = None,
    tz: Annotated[str | None, Query(description="IANA time zone")] = None,
    zone: MultiValue = None,
) -> DashboardSummary:
    """Period metrics (default: the latest shift with stored data), priority incidents, event activity, data quality,
    device health and a briefing preview, plus clearly labelled all-time totals. Computed by the briefing engine."""
    period = resolve_period(db, mode, date_, shift, start, end, tz)
    return await run_in_threadpool(build_dashboard, db, period, _split(zone), get_briefing_settings(), get_reasoning_settings())
