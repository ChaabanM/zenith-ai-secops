"""Candidate selection: load only correlation-relevant events and index them.

Each source family is read with one query that selects the scalar event columns
plus the specific payload fields correlation needs (via ``json_extract``); raw
submissions and full payloads are never loaded. Routine families are filtered in
SQL (heartbeats, shift notes, plain motion events are excluded).

Indexes are in-memory hash maps keyed by an identifier (plate, credential, zone,
device, radar track, ...) whose values are lists sorted by event time, so a
candidate lookup is a hash lookup plus a binary search over a bounded time window.
Nothing is ever compared pairwise across the whole dataset.
"""

from __future__ import annotations

import bisect
import json
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.ingestion import Event

BASE_COLUMNS = (
    Event.id,
    Event.event_id,
    Event.submission_id,
    Event.source_type,
    Event.device_id,
    Event.event_type,
    Event.occurred_at,
    Event.zone,
    Event.latitude,
    Event.longitude,
    Event.severity,
    Event.confidence,
)


@dataclass(slots=True)
class Obs:
    """One normalized event as seen by the correlation engine (read-only)."""

    pk: int
    event_id: str
    submission_id: int
    source: str
    device: str
    etype: str
    t: datetime
    zone: str | None
    lat: float | None
    lon: float | None
    severity: str | None
    confidence: float | None
    f: dict[str, Any] = field(default_factory=dict)  # selected payload fields

    @property
    def ts(self) -> float:
        return self.t.timestamp()

    def get(self, name: str) -> Any:
        return self.f.get(name)


def _pf(name: str) -> Any:
    return func.json_extract(Event.payload, f"$.{name}")


FAMILIES: dict[str, tuple[dict[str, str], Any]] = {
    # family: ({field: json path}, SQL filter)
    "plate": (
        {"plate": "plate_number", "make": "vehicle_make", "colour": "vehicle_colour", "model": "vehicle_model", "vtype": "vehicle_type",
         "read_status": "read_status", "radar_device": "radar_device_id", "radar_track": "radar_track_id", "speed": "measured_speed_kph",
         "vehicle_id": "vehicle_id"},
        Event.source_type.in_(["anpr", "traffic_violation", "mobile_phone_detection", "seatbelt_detection", "gps_fleet"]),
    ),
    "radar": ({"track": "track_id", "speed": "speed_kph", "limit": "speed_limit_kph", "triggered": "camera_triggered"}, Event.source_type == "radar"),
    "access": (
        {"credential": "credential_id", "holder": "holder_id", "door": "door_id", "result": "result", "reason": "reason"},
        Event.source_type == "access_control",
    ),
    "face": (
        {"match_status": "match_status", "subject": "subject_id", "candidate": "candidate_subject_id", "score": "match_score"},
        Event.source_type == "face_recognition",
    ),
    "cctv": (
        {"category": "object_category", "track": "track_id", "colour": "attributes.colour", "verification": "verification_result",
         "heading": "attributes.heading"},
        and_(Event.source_type == "cctv", Event.event_type.in_(["vehicle_detected", "person_detected", "loitering_detected", "scene_clear"]),
             or_(Event.event_type != "vehicle_detected", _pf("attributes").is_not(None))),
    ),
    "perimeter": ({"segment": "sensor_segment", "alarm_type": "alarm_type", "intensity": "intensity"}, Event.source_type == "perimeter_intrusion"),
    "emergency": ({"alarm_type": "alarm_type", "input_point": "input_point", "state": "state", "panel": "panel_id"},
                  Event.source_type == "emergency_alarm"),
    "health": (
        {"status": "status", "code": "diagnostic_code", "monitor": "monitor"},
        and_(Event.source_type == "device_health", or_(Event.event_type != "heartbeat", _pf("status") != "online")),
    ),
    "network": (
        {"src_ip": "src_ip", "username": "username", "action": "action", "signature": "signature"},
        and_(Event.source_type == "network_security",
             or_(Event.event_type.in_(["auth_failure", "auth_success"]), Event.severity.in_(["high", "critical"]))),
    ),
    "report": (
        {"category": "report_category", "priority": "priority", "refs": "referenced_event_ids", "summary": "summary"},
        and_(Event.source_type == "operator_report", Event.event_type != "shift_note"),
    ),
}


def load_family(db: Session, family: str) -> list[Obs]:
    fields, condition = FAMILIES[family]
    columns = [_pf(path).label(name) for name, path in fields.items()]
    stmt = select(*BASE_COLUMNS, *columns).where(condition).order_by(Event.occurred_at, Event.id)
    out = []
    for row in db.execute(stmt.execution_options(yield_per=5000)):
        payload = {name: getattr(row, name) for name in fields}
        if "refs" in payload and isinstance(payload["refs"], str):
            try:
                payload["refs"] = json.loads(payload["refs"])
            except ValueError:
                payload["refs"] = None
        out.append(Obs(row.id, row.event_id, row.submission_id, row.source_type, row.device_id, row.event_type, row.occurred_at,
                       row.zone, row.latitude, row.longitude, row.severity, row.confidence, payload))
    return out


def load_device_window(db: Session, device_id: str, start: datetime, end: datetime, max_confidence: float) -> list[Obs]:
    """Low-confidence output of one device in a time window (indexed by device_id, occurred_at)."""
    stmt = (
        select(*BASE_COLUMNS)
        .where(Event.device_id == device_id, Event.source_type != "device_health", Event.occurred_at >= start, Event.occurred_at <= end,
               Event.confidence < max_confidence)
        .order_by(Event.occurred_at, Event.id)
    )
    return [Obs(r.id, r.event_id, r.submission_id, r.source_type, r.device_id, r.event_type, r.occurred_at, r.zone, r.latitude,
                r.longitude, r.severity, r.confidence) for r in db.execute(stmt)]


class TimeIndex:
    """key -> observations sorted by time; window lookups by binary search."""

    def __init__(self, items: Iterable[Obs], key: Callable[[Obs], Any]):
        grouped: dict[Any, list[Obs]] = defaultdict(list)
        for o in items:
            k = key(o)
            if k is not None:
                grouped[k].append(o)
        self._items: dict[Any, list[Obs]] = {}
        self._times: dict[Any, list[float]] = {}
        for k, values in grouped.items():
            values.sort(key=lambda o: (o.t, o.pk))
            self._items[k] = values
            self._times[k] = [o.ts for o in values]

    def window(self, key: Any, start: float, end: float) -> list[Obs]:
        times = self._times.get(key)
        if not times:
            return []
        return self._items[key][bisect.bisect_left(times, start) : bisect.bisect_right(times, end)]

    def keys(self) -> Iterable[Any]:
        return self._items.keys()

    def all(self, key: Any) -> list[Obs]:
        return self._items.get(key, [])


@dataclass
class Candidates:
    families: dict[str, list[Obs]]

    @property
    def count(self) -> int:
        return sum(len(v) for v in self.families.values())


def load_candidates(db: Session) -> Candidates:
    return Candidates({name: load_family(db, name) for name in FAMILIES})
