"""Deterministic, realistic security-event dataset generator with ground truth.

Design rules
------------
* Every random choice comes from one ``random.Random(seed)``; no wall-clock values,
  no ``set`` iteration and no ``hash()``, so the same configuration produces
  byte-identical output.
* Production records contain only observable sensor data. Scenario ids, roles and
  expected answers exist only in the ground-truth file.
* A sensor only carries identifiers it could plausibly know (radar: track id and
  speed, never a plate; generic CCTV: local track id, never a plate or identity).
* Missing observations (device outages) are described as expected absences in the
  ground truth; no events are fabricated for them.
* The exact number of submissions requested is produced. Scenario volume scales
  with the count; background "ordinary activity" fills the remainder.
"""

from __future__ import annotations

import copy
import json
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from datagen import world
from datagen.world import DEVICES_BY_SOURCE, ZONE_BY_NAME, Device, device

GENERATOR_VERSION = "1.1.0"
LOCAL_TZ = timezone(timedelta(hours=4))
DEFAULT_START = datetime(2026, 1, 12, 0, 0, 0, tzinfo=UTC)

SCENARIO_TYPES = {
    "A": "multi_sensor_traffic_incident",
    "B": "unauthorized_access",
    "C": "perimeter_intrusion",
    "D": "device_failure",
    "E": "conflicting_sensors",
    "F": "duplicate_events",
    "G": "delayed_out_of_order",
    "H": "malformed_incomplete",
    "I": "low_confidence",
    "J": "repeated_events_spikes",
    "K": "unrelated_events",
    "L": "ambiguous_uncorrelatable",
}

# Ordinary background activity mix (fractions of background events).
BACKGROUND_MIX = {
    "anpr": 0.20,
    "cctv": 0.12,
    "access_control": 0.12,
    "gps_fleet": 0.10,
    "network_security": 0.08,
    "radar": 0.07,
    "traffic_violation": 0.06,
    "device_health": 0.06,
    "face_recognition": 0.05,
    "mobile_phone_detection": 0.03,
    "seatbelt_detection": 0.03,
    "perimeter_intrusion": 0.03,
    "operator_report": 0.02,
    "weather_environmental": 0.02,
    "emergency_alarm": 0.01,
}

# Typical transmission latency per source (seconds, min/max).
LATENCY = {
    "network_security": (0.2, 2.0),
    "anpr": (0.4, 4.0),
    "radar": (0.2, 2.0),
    "traffic_violation": (1.0, 9.0),
    "mobile_phone_detection": (1.0, 9.0),
    "seatbelt_detection": (1.0, 9.0),
    "cctv": (0.5, 5.0),
    "face_recognition": (0.5, 3.0),
    "access_control": (0.3, 2.0),
    "perimeter_intrusion": (0.3, 2.5),
    "emergency_alarm": (0.2, 1.5),
    "device_health": (1.0, 15.0),
    "gps_fleet": (1.0, 20.0),
    "weather_environmental": (2.0, 40.0),
    "operator_report": (20.0, 180.0),
}

INVALID_VARIANTS = [
    "missing_event_id",
    "missing_source_type",
    "missing_device_id",
    "missing_occurred_at",
    "missing_event_type",
    "wrong_type_device_id",
    "wrong_type_confidence",
    "invalid_timestamp_format",
    "invalid_timestamp_value",
    "timestamp_missing_timezone",
    "invalid_latitude",
    "invalid_longitude",
    "confidence_above_one",
    "confidence_negative",
    "unknown_source_type",
    "payload_not_object",
    "invalid_severity",
    "malformed_json_truncated",
    "malformed_json_legacy_line",
    "not_an_object",
    "empty_object",
]
# Access-control reader watched by each face-recognition camera.
GATE_READERS = {"Gate-A": "acs-gate-a", "Gate-B": "acs-gate-b", "Data-Hall-2": "acs-datahall-2"}
SYNTAX_INVALID = {"malformed_json_truncated", "malformed_json_legacy_line"}


@dataclass
class Config:
    count: int
    seed: int = 42
    start: datetime = DEFAULT_START
    days: float = 3.0
    profile: str = "mixed"  # mixed | clean
    invalid_rate: float = 0.03
    duplicate_rate: float = 0.02
    delay_rate: float = 0.02
    low_confidence_rate: float = 0.02
    scenario_scale: float = 1.0

    def effective(self) -> Config:
        if self.profile == "clean":
            return Config(**{**self.__dict__, "invalid_rate": 0.0, "duplicate_rate": 0.0})
        return self

    def describe(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "count": self.count,
            "profile": self.profile,
            "start": iso(self.start),
            "end": iso(self.start + timedelta(days=self.days)),
            "days": self.days,
            "rates": {
                "invalid": self.invalid_rate,
                "duplicate": self.duplicate_rate,
                "delay": self.delay_rate,
                "low_confidence": self.low_confidence_rate,
            },
            "scenario_scale": self.scenario_scale,
        }


@dataclass
class Sub:
    """One submission (a line of the JSONL file)."""

    record: Any
    sort_time: datetime
    seq: int
    valid: bool
    event_id: str | None = None
    content_key: str | None = None
    source: str | None = None
    device_id: str | None = None
    occurred: datetime | None = None
    raw_line: str | None = None  # literal text for syntactically malformed lines
    invalid_reasons: list[str] = field(default_factory=list)
    scenarios: list[str] = field(default_factory=list)
    index: int = -1

    def line(self) -> str:
        if self.raw_line is not None:
            return self.raw_line
        return json.dumps(self.record, ensure_ascii=False, separators=(",", ":"))

    def json_value(self) -> Any:
        return self.raw_line if self.raw_line is not None else self.record


@dataclass
class Scenario:
    scenario_id: str
    letter: str
    variant: str
    title: str
    description: str
    expected_incident: Any = True  # True | False | "uncertain" | "separate"
    subs: list[Sub] = field(default_factory=list)
    related: list[Sub] = field(default_factory=list)
    unrelated: list[Sub] = field(default_factory=list)
    anomalies: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    uncertainty: list[dict[str, Any]] = field(default_factory=list)
    absences: list[dict[str, Any]] = field(default_factory=list)
    separate_groups: list[list[Sub]] = field(default_factory=list)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


class BudgetExceeded(Exception):
    pass


class DatasetGenerator:
    def __init__(self, config: Config):
        self.cfg = config.effective()
        if self.cfg.count < 1:
            raise ValueError("count must be >= 1")
        self.rng = random.Random(self.cfg.seed)
        self.start = self.cfg.start
        self.end = self.cfg.start + timedelta(days=self.cfg.days)
        self.subs: list[Sub] = []
        self.valid_events: list[Sub] = []
        self.scenarios: list[Scenario] = []
        self.used_ids: set[str] = set()
        self.used_plates: set[str] = set()
        self.blackouts: dict[str, list[tuple[datetime, datetime]]] = {}
        self.scenario_counter: dict[str, int] = {}
        self.seq = 0
        self.budget = self.cfg.count
        self._background_plates: dict[str, list[dict[str, str]]] = {}
        self._device_offsets = {d.device_id: i for i, d in enumerate(world.DEVICES)}

    # ================================================================ primitives

    def new_event_id(self) -> str:
        while True:
            eid = f"evt-{self.rng.getrandbits(48):012x}"
            if eid not in self.used_ids:
                self.used_ids.add(eid)
                return eid

    def new_vehicle(self) -> dict[str, str]:
        while True:
            plate = f"{self.rng.choice(world.PLATE_LETTERS)} {self.rng.randint(10000, 99999)}"
            if plate not in self.used_plates:
                self.used_plates.add(plate)
                break
        make, model, vtype = self.rng.choice(world.VEHICLE_CATALOGUE)
        return {
            "plate_number": plate,
            "plate_region": self.rng.choice(world.PLATE_REGIONS),
            "vehicle_make": make,
            "vehicle_model": model,
            "vehicle_colour": self.rng.choice(world.COLOURS),
            "vehicle_type": vtype,
        }

    def background_vehicle(self, zone: str) -> dict[str, str]:
        """A vehicle from the local population of ``zone``.

        Ordinary traffic is local: each monitored site sees its own pool of regular
        vehicles, so background plates never "teleport" between distant sites.
        Cross-site movement only happens in scenarios, at realistic travel speeds.
        """
        pool = self._background_plates.get(zone)
        if pool is None:
            pool = self._background_plates[zone] = [self.new_vehicle() for _ in range(250)]
        return self.rng.choice(pool)

    def fmt(self, dt: datetime, style: str) -> str:
        if style == "ms":
            return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        if style == "local":
            return dt.astimezone(LOCAL_TZ).isoformat(timespec="seconds")
        return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    def location_of(self, dev: Device) -> dict[str, Any]:
        zone = ZONE_BY_NAME[dev.zone]
        if zone.lat is None:
            return {"zone": zone.name}
        k = self._device_offsets[dev.device_id]
        return {
            "latitude": round(zone.lat + ((k % 7) - 3) * 0.00011, 6),
            "longitude": round(zone.lon + ((k % 5) - 2) * 0.00013, 6),
            "zone": zone.name,
        }

    def latency(self, source: str) -> float:
        lo, hi = LATENCY[source]
        return self.rng.uniform(lo, hi)

    def diurnal_time(self, margin: timedelta = timedelta(0)) -> datetime:
        span = (self.end - self.start - margin).total_seconds()
        while True:
            t = self.start + timedelta(seconds=self.rng.random() * span)
            hour = t.astimezone(LOCAL_TZ).hour
            weight = 0.35 if hour < 6 else 0.7 if hour == 6 else 0.6 if hour >= 21 else 1.0
            if self.rng.random() < weight:
                return t

    def available(self, device_id: str, a: datetime, b: datetime | None = None) -> bool:
        b = b or a
        return all(not (a < end and b >= start) for start, end in self.blackouts.get(device_id, ()))

    def pick_time(self, device_ids: list[str], after: timedelta = timedelta(minutes=30), before: timedelta = timedelta(0)) -> datetime:
        for _ in range(200):
            t = self.diurnal_time(margin=after)
            if t - before < self.start:
                continue
            if all(self.available(d, t - before, t + after) for d in device_ids):
                return t
        raise BudgetExceeded("no free time window")

    def emit(
        self,
        source: str,
        dev: Device,
        event_type: str,
        t: datetime,
        *,
        severity: str | None,
        confidence: float | None,
        payload: dict[str, Any],
        location: Any = "device",
        received: Any = "auto",
        sort_time: datetime | None = None,
        scenario: Scenario | None = None,
    ) -> Sub:
        if source != "device_health" and not self.available(dev.device_id, t):
            raise BudgetExceeded("device unavailable")
        eid = self.new_event_id()
        occurred_text = self.fmt(t, dev.time_style)
        occurred = datetime.fromisoformat(occurred_text).astimezone(UTC)
        if received == "auto":
            received = occurred + timedelta(seconds=self.latency(source))
        record: dict[str, Any] = {
            "event_id": eid,
            # firmware aliases apply only to the device's own observations, not health records about it
            "source_type": (dev.source_label if dev.source == source else None) or source,
            "device_id": dev.device_id,
            "event_type": event_type,
            "occurred_at": occurred_text,
        }
        if received is not None:
            record["received_at"] = self.fmt(received, dev.time_style)
        loc = self.location_of(dev) if location == "device" else location
        if loc is not None:
            record["location"] = loc
        if severity is not None:
            record["severity"] = severity
        if confidence is not None:
            record["confidence"] = round(min(max(confidence, 0.0), 1.0), 3)
        record["payload"] = payload
        arrival = sort_time or (received if received is not None else occurred + timedelta(seconds=self.latency(source)))
        sub = Sub(
            record=record,
            sort_time=arrival,
            seq=self._next_seq(),
            valid=True,
            event_id=eid,
            content_key=f"{eid}#0",
            source=source,
            device_id=dev.device_id,
            occurred=occurred,
        )
        self._add(sub, scenario)
        self.valid_events.append(sub)
        return sub

    def _next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def _add(self, sub: Sub, scenario: Scenario | None) -> None:
        if len(self.subs) >= self.budget:
            raise BudgetExceeded("submission budget exhausted")
        self.subs.append(sub)
        if scenario is not None:
            sub.scenarios.append(scenario.scenario_id)
            scenario.subs.append(sub)

    def new_scenario(self, letter: str, variant: str, title: str, description: str, expected_incident: Any = True) -> Scenario:
        n = self.scenario_counter.get(letter, 0) + 1
        self.scenario_counter[letter] = n
        scenario = Scenario(f"SCN-{letter}-{n:04d}", letter, variant, title, description, expected_incident)
        self.scenarios.append(scenario)
        return scenario

    @staticmethod
    def note(target: list[dict[str, Any]], kind: str, subs: list[Sub], detail: str) -> None:
        target.append({"kind": kind, "subs": list(subs), "detail": detail})

    def img(self, dev: Device, t: datetime) -> str:
        return f"img://{dev.device_id}/{t:%Y%m%d}/{self.rng.getrandbits(40):010x}.jpg"

    def track(self, prefix: str) -> str:
        return f"{prefix}-{self.rng.randint(100000, 999999)}"

    # ================================================================ payload helpers

    def anpr_payload(self, dev: Device, t: datetime, v: dict[str, str], read_status: str = "read") -> dict[str, Any]:
        return {
            **v,
            "read_status": read_status,
            "lane": self.rng.randint(1, 4),
            "direction": self.rng.choice(["northbound", "southbound", "eastbound", "westbound"]),
            "image_ref": self.img(dev, t),
        }

    def radar_payload(self, track: str, speed: int, limit: int, triggered: bool) -> dict[str, Any]:
        return {
            "track_id": track,
            "speed_kph": speed,
            "speed_limit_kph": limit,
            "direction": self.rng.choice(["approach", "recede"]),
            "lane": self.rng.randint(1, 4),
            "vehicle_length_m": round(self.rng.uniform(3.8, 5.6), 1),
            "camera_triggered": triggered,
        }

    def cctv_payload(self, dev: Device, observation: str, category: str, count: int = 1, **attrs: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "observation_type": observation,
            "object_category": category,
            "object_count": count,
            "track_id": self.track(f"{dev.device_id}-trk"),
        }
        if attrs:
            payload["attributes"] = attrs
        return payload

    def health_payload(self, status: str, code: str | None, **extra: Any) -> dict[str, Any]:
        payload = {"status": status, "monitor": "nms-01", "diagnostic_code": code}
        payload.update(extra)
        return payload

    def employee(self) -> tuple[str, str]:
        n = self.rng.randint(1001, 1400)
        return f"EMP-{n}", f"CRD-{20000 + n * 7}"

    def operator_report(
        self, t: datetime, zone: str, category: str, summary: str, priority: str, references: list[str] | None, scenario: Scenario | None, severity: str = "medium", received: Any = "auto"
    ) -> Sub:
        dev = self.rng.choice(DEVICES_BY_SOURCE["operator_report"])
        payload: dict[str, Any] = {
            "operator_id": self.rng.choice(world.OPERATORS),
            "report_category": category,
            "summary": summary,
            "priority": priority,
        }
        if references:
            payload["referenced_event_ids"] = references
        return self.emit(
            "operator_report", dev, "incident_report" if category != "shift_note" else "shift_note", t,
            severity=severity, confidence=None, payload=payload, location={"zone": zone}, received=received, scenario=scenario,
        )

    # ================================================================ scenarios

    def scn_a_traffic(self) -> None:
        site = self.rng.choice(world.INTERSECTIONS)
        n = site.name[-2:]
        radar = device(f"radar-int{n}", "radar")
        tv = device(f"tcam-int{n}", "traffic_violation")
        phone = device(f"tcam-int{n}", "mobile_phone_detection")
        belt = device(f"tcam-int{n}", "seatbelt_detection")
        anpr = device(f"anpr-int{n}", "anpr")
        cctv = device(f"cctv-int{n}", "cctv")
        t = self.pick_time([radar.device_id, tv.device_id, anpr.device_id, cctv.device_id], after=timedelta(minutes=15))
        variant = self.rng.choice(["speeding", "speeding", "red_light"])
        v = self.new_vehicle()
        s = self.new_scenario(
            "A", variant, f"{variant.replace('_', '-')} violation with multiple sensor captures at {site.name}",
            "One vehicle observed by radar/enforcement camera, ANPR and CCTV within seconds at the same site; "
            "plate and radar track id link the enforcement records, CCTV links only by time, place and appearance.",
        )
        plate_fields = {k: v[k] for k in ("plate_number", "plate_region", "vehicle_make", "vehicle_model", "vehicle_colour")}
        related = []
        if variant == "speeding":
            limit = site.speed_limit or 60
            speed = limit + self.rng.randint(25, 65)
            trk = f"T{self.rng.randint(10000, 99999)}"
            sev = "high" if speed - limit >= 40 else "medium"
            related.append(self.emit("radar", radar, "speed_violation", t, severity=sev, confidence=self.rng.uniform(0.92, 0.99),
                                     payload=self.radar_payload(trk, speed, limit, True), scenario=s))
            related.append(self.emit("traffic_violation", tv, "speeding", t + timedelta(seconds=0.8), severity=sev,
                                     confidence=self.rng.uniform(0.9, 0.98), scenario=s, payload={
                                         "violation_type": "speeding", **plate_fields, "measured_speed_kph": speed,
                                         "speed_limit_kph": limit, "radar_device_id": radar.device_id, "radar_track_id": trk,
                                         "lane": self.rng.randint(1, 4), "evidence_images": [self.img(tv, t), self.img(tv, t)]}))
        else:
            related.append(self.emit("traffic_violation", tv, "red_light_violation", t, severity="high",
                                     confidence=self.rng.uniform(0.9, 0.98), scenario=s, payload={
                                         "violation_type": "red_light", **plate_fields, "signal_phase": "red",
                                         "time_since_red_ms": self.rng.randint(400, 3500), "stop_line_crossed": True,
                                         "lane": self.rng.randint(1, 3), "evidence_images": [self.img(tv, t), self.img(tv, t)]}))
        has_phone = self.rng.random() < 0.6
        if has_phone:
            related.append(self.emit("mobile_phone_detection", phone, "phone_use_detected", t + timedelta(seconds=1.2),
                                     severity="medium", confidence=self.rng.uniform(0.82, 0.96), scenario=s, payload={
                                         **plate_fields, "occupant": "driver", "phone_position": self.rng.choice(["hand_to_ear", "texting"]),
                                         "image_ref": self.img(phone, t)}))
        if self.rng.random() < 0.5 or not has_phone:
            related.append(self.emit("seatbelt_detection", belt, "seatbelt_violation", t + timedelta(seconds=1.4),
                                     severity="low", confidence=self.rng.uniform(0.8, 0.95), scenario=s, payload={
                                         **plate_fields, "occupant": "driver", "belt_status": "unfastened", "image_ref": self.img(belt, t)}))
        related.append(self.emit("anpr", anpr, "plate_read", t + timedelta(seconds=2.1), severity="informational",
                                 confidence=self.rng.uniform(0.9, 0.99), payload=self.anpr_payload(anpr, t, v), scenario=s))
        related.append(self.emit("cctv", cctv, "vehicle_detected", t + timedelta(seconds=3.0), severity="informational",
                                 confidence=self.rng.uniform(0.75, 0.93), scenario=s,
                                 payload=self.cctv_payload(cctv, "vehicle_detected", v["vehicle_type"], colour=v["vehicle_colour"])))
        if self.rng.random() < 0.4:
            other = self.rng.choice([z for z in world.ROAD_SITES if z.name != site.name])
            down = next(d for d in DEVICES_BY_SOURCE["anpr"] if d.zone == other.name)
            km = world.distance_km(site.lat, site.lon, other.lat, other.lon)
            t2 = t + timedelta(hours=km / self.rng.uniform(50, 90), seconds=self.rng.uniform(30, 120))
            if self.available(down.device_id, t2):
                related.append(self.emit("anpr", down, "plate_read", t2, severity="informational",
                                         confidence=self.rng.uniform(0.9, 0.99), payload=self.anpr_payload(down, t2, v), scenario=s))
        bystander = self.new_vehicle()
        tb = t + timedelta(seconds=self.rng.uniform(6, 25))
        other_read = self.emit("anpr", anpr, "plate_read", tb, severity="informational", confidence=self.rng.uniform(0.9, 0.99),
                               payload=self.anpr_payload(anpr, tb, bystander), scenario=s)
        s.related, s.unrelated = related, [other_read]
        s.separate_groups = [related, [other_read]]

    def scn_b_access(self) -> None:
        gate = self.rng.choice(["b", "c", "datahall"])
        if gate == "datahall":
            acs, cctv, frs, zone = device("acs-datahall-2", "access_control"), device("cctv-datahall-2", "cctv"), device("frs-datahall-2", "face_recognition"), "Data-Hall-2"
        else:
            zone = f"Gate-{gate.upper()}"
            acs, cctv = device(f"acs-gate-{gate}", "access_control"), device(f"cctv-gate-{gate}", "cctv")
            frs = device(f"frs-gate-{gate}", "face_recognition") if gate == "b" else None
        ids = [acs.device_id, cctv.device_id] + ([frs.device_id] if frs else [])
        t = self.pick_time(ids, after=timedelta(minutes=20))
        reason = self.rng.choice(["credential_revoked", "unknown_credential", "outside_schedule", "invalid_pin"])
        holder, cred = self.employee()
        if reason == "unknown_credential":
            holder, cred = None, f"CRD-{self.rng.randint(90000, 99999)}"
        s = self.new_scenario("B", reason, f"Repeated denied access at {zone}",
                              "Several denied badge attempts with one credential, followed by camera and operator observations at the same door.")
        attempts = []
        tt = t
        for i in range(self.rng.randint(3, 5)):
            attempts.append(self.emit("access_control", acs, "access_denied", tt, severity="low" if i < 2 else "medium", confidence=None, scenario=s,
                                      payload={"credential_id": cred, "holder_id": holder, "door_id": f"{zone}-D1", "result": "denied",
                                               "reason": reason, "direction": "in"}))
            tt += timedelta(seconds=self.rng.uniform(12, 35))
        related = list(attempts)
        related.append(self.emit("cctv", cctv, "loitering_detected", tt + timedelta(seconds=self.rng.uniform(5, 25)), severity="medium",
                                 confidence=self.rng.uniform(0.78, 0.94), scenario=s,
                                 payload=self.cctv_payload(cctv, "loitering_detected", "person", dwell_seconds=self.rng.randint(60, 180))))
        if frs:
            related.append(self.emit("face_recognition", frs, "face_no_match", tt + timedelta(seconds=self.rng.uniform(20, 40)), severity="medium",
                                     confidence=self.rng.uniform(0.7, 0.88), scenario=s,
                                     payload={"match_status": "no_match", "subject_id": None, "match_score": round(self.rng.uniform(0.1, 0.3), 3),
                                              "face_quality": round(self.rng.uniform(0.6, 0.9), 2), "gallery": "staff"}))
        if self.rng.random() < 0.3:
            related.append(self.emit("access_control", acs, "door_forced_open", tt + timedelta(seconds=self.rng.uniform(60, 120)), severity="critical",
                                     confidence=None, scenario=s, payload={"credential_id": None, "holder_id": None, "door_id": f"{zone}-D1",
                                                                           "result": "alarm", "reason": "door_forced", "direction": "in"}))
        refs = [attempts[0].event_id] if self.rng.random() < 0.6 else None
        related.append(self.operator_report(tt + timedelta(minutes=self.rng.uniform(3, 7)), zone, "access_violation",
                                            f"Repeated badge failures at {zone}. Guard dispatched; person left the area before the guard arrived.",
                                            "high", refs, s))
        s.related = related
        self.note(s.anomalies, "access_failure_repetition", attempts, f"{len(attempts)} denied attempts with credential {cred}")

    def scn_c_perimeter(self) -> None:
        side = self.rng.choice(["north", "east", "south"])
        zone = f"Perimeter-{side.title()}"
        pids = self.rng.choice([d for d in DEVICES_BY_SOURCE["perimeter_intrusion"] if d.zone == zone])
        cctv = device(f"cctv-perim-{side}", "cctv")
        panel = device("alarm-panel-main", "emergency_alarm")
        t = self.pick_time([pids.device_id, cctv.device_id], after=timedelta(minutes=15))
        alarm_type = self.rng.choice(["fence_cut", "fence_climb", "beam_break"])
        confirmed = self.rng.random() < 0.6
        s = self.new_scenario("C", alarm_type, f"Perimeter intrusion alarm at {zone}",
                              "Perimeter sensor alarm, camera detection, panel alarm and an operator follow-up at the same fence section.")
        segment = f"{side[0].upper()}-{self.rng.randint(1, 24):02d}"
        related = [
            self.emit("perimeter_intrusion", pids, alarm_type, t, severity="critical" if alarm_type == "fence_cut" else "high",
                      confidence=self.rng.uniform(0.8, 0.97), scenario=s,
                      payload={"sensor_segment": segment, "alarm_type": alarm_type, "intensity": round(self.rng.uniform(0.6, 0.98), 2),
                               "duration_ms": self.rng.randint(800, 6000)}),
            self.emit("cctv", cctv, "person_detected", t + timedelta(seconds=self.rng.uniform(8, 25)), severity="high",
                      confidence=self.rng.uniform(0.72, 0.93), scenario=s,
                      payload=self.cctv_payload(cctv, "person_detected", "person", count=self.rng.choice([1, 1, 2]), heading="inbound")),
            self.emit("emergency_alarm", panel, "intrusion_alarm", t + timedelta(seconds=self.rng.uniform(15, 30)), severity="critical",
                      confidence=None, scenario=s, location=self.location_of(pids) | {"zone": zone},
                      payload={"alarm_type": "intrusion", "panel_id": panel.device_id, "input_point": f"PIDS-{segment}", "state": "active"}),
        ]
        summary = (f"Patrol confirmed damage at fence section {segment}; intruder not located." if confirmed
                   else f"Patrol checked fence section {segment}; no intruder or damage found.")
        related.append(self.operator_report(t + timedelta(minutes=self.rng.uniform(2, 6)), zone, "perimeter", summary, "critical",
                                            [related[2].event_id] if self.rng.random() < 0.5 else None, s, severity="high"))
        s.related = related

    def scn_d_device_failure(self) -> None:
        candidates = [d for d in world.FAILABLE_DEVICES]
        dev = self.rng.choice(candidates)
        t0 = self.pick_time([dev.device_id], after=timedelta(hours=6), before=timedelta(minutes=5))
        t_fault = t0 + timedelta(minutes=self.rng.uniform(8, 18))
        t_off = t0 + timedelta(minutes=self.rng.uniform(22, 40))
        t_rec = t_off + timedelta(minutes=self.rng.uniform(45, 270))
        codes = {"anpr": "E-ANPR-IR-ILLUMINATOR", "radar": "E-RADAR-TX-POWER", "cctv": "E-CAM-VIDEO-LOSS",
                 "perimeter_intrusion": "E-PIDS-CABLE-OPEN", "face_recognition": "E-FRS-LENS-OBSTRUCTED"}
        s = self.new_scenario("D", dev.source, f"{dev.device_id} degraded, failed and recovered",
                              "Device health deteriorates, a sensor fault is reported, the device goes offline and later recovers. "
                              "No observations from the device exist while it is offline.")
        loc = self.location_of(dev)
        health = []
        health.append(self.emit("device_health", dev, "device_degraded", t0, severity="medium", confidence=None, location=loc, scenario=s,
                                payload=self.health_payload("degraded", "W-TEMP-HIGH", cpu_temp_c=round(self.rng.uniform(74, 88), 1),
                                                            packet_loss_pct=round(self.rng.uniform(8, 25), 1), uptime_s=self.rng.randint(200000, 900000))))
        degraded_obs = []
        for _ in range(2):
            t = t0 + timedelta(minutes=self.rng.uniform(1, 7))
            degraded_obs.append(self.degraded_observation(dev, t, s))
        health.append(self.emit("device_health", dev, "sensor_failure", t_fault, severity="high", confidence=None, location=loc, scenario=s,
                                payload=self.health_payload("fault", codes[dev.source])))
        offline = self.emit("device_health", dev, "device_offline", t_off, severity="high", confidence=None, location=loc, scenario=s,
                            payload=self.health_payload("offline", "E-HEARTBEAT-LOST", missed_heartbeats=3,
                                                        last_contact_at=iso(t_off - timedelta(seconds=self.rng.randint(60, 180)))))
        recovered = self.emit("device_health", dev, "device_recovered", t_rec, severity="informational", confidence=None, location=loc, scenario=s,
                              payload=self.health_payload("online", None, downtime_s=int((t_rec - t_off).total_seconds()),
                                                          action=self.rng.choice(["remote_reboot", "field_technician_visit"])))
        self.blackouts.setdefault(dev.device_id, []).append((offline.occurred, recovered.occurred))
        s.related = health[:1] + degraded_obs + health[1:] + [offline, recovered]
        self.note(s.anomalies, "device_degraded", health[:1] + degraded_obs, "high temperature / packet loss and low-quality output before failure")
        self.note(s.anomalies, "sensor_failure", [health[1]], codes[dev.source])
        self.note(s.anomalies, "device_offline", [offline], "heartbeat lost")
        self.note(s.anomalies, "device_recovered", [recovered], "device back online")
        s.absences.append({"device_id": dev.device_id, "source_type": dev.source, "from": iso(offline.occurred), "to": iso(recovered.occurred),
                           "detail": "no observations expected while the device is offline; absence is not represented by events"})

    def degraded_observation(self, dev: Device, t: datetime, s: Scenario) -> Sub:
        low = self.rng.uniform(0.3, 0.5)
        if dev.source == "anpr":
            v = self.background_vehicle(dev.zone)
            partial = dict(v, plate_number=v["plate_number"][:3] + "??" + v["plate_number"][5:])
            return self.emit("anpr", dev, "plate_read", t, severity="informational", confidence=low, scenario=s,
                             payload=self.anpr_payload(dev, t, partial, read_status="partial"))
        if dev.source == "radar":
            return self.emit("radar", dev, "speed_measurement", t, severity="informational", confidence=low, scenario=s,
                             payload=self.radar_payload(f"T{self.rng.randint(10000, 99999)}", self.rng.randint(20, 90), 80, False) | {"signal_quality": "poor"})
        if dev.source == "perimeter_intrusion":
            return self.emit("perimeter_intrusion", dev, "fence_vibration", t, severity="low", confidence=low, scenario=s,
                             payload={"sensor_segment": "unknown", "alarm_type": "vibration", "intensity": round(self.rng.uniform(0.1, 0.3), 2), "duration_ms": 200})
        if dev.source == "face_recognition":
            return self.emit("face_recognition", dev, "face_uncertain", t, severity="low", confidence=low, scenario=s,
                             payload={"match_status": "uncertain", "subject_id": None, "match_score": round(self.rng.uniform(0.3, 0.5), 3), "face_quality": 0.31, "gallery": "staff"})
        return self.emit("cctv", dev, "motion_detected", t, severity="informational", confidence=low, scenario=s,
                         payload=self.cctv_payload(dev, "motion_detected", "unknown", image_quality="degraded"))

    def scn_e_conflict(self, variant: str) -> None:
        s = self.new_scenario("E", variant, "", "")
        if variant == "contradictory_vehicle_details":
            site = self.rng.choice(world.INTERSECTIONS)
            n = site.name[-2:]
            anpr, tv = device(f"anpr-int{n}", "anpr"), device(f"tcam-int{n}", "traffic_violation")
            t = self.pick_time([anpr.device_id, tv.device_id])
            v = self.new_vehicle()
            other = dict(v)
            while (other["vehicle_make"], other["vehicle_colour"]) == (v["vehicle_make"], v["vehicle_colour"]):
                make, model, vtype = self.rng.choice(world.VEHICLE_CATALOGUE)
                other.update(vehicle_make=make, vehicle_model=model, vehicle_type=vtype, vehicle_colour=self.rng.choice(world.COLOURS))
            a = self.emit("anpr", anpr, "plate_read", t, severity="informational", confidence=0.96, payload=self.anpr_payload(anpr, t, v), scenario=s)
            b = self.emit("traffic_violation", tv, "lane_violation", t + timedelta(seconds=4), severity="medium", confidence=0.93, scenario=s,
                          payload={"violation_type": "lane_violation", **{k: other[k] for k in ("plate_number", "plate_region", "vehicle_make", "vehicle_model", "vehicle_colour")},
                                   "lane": 1, "evidence_images": [self.img(tv, t)]})
            control_v = self.new_vehicle()
            control_v.update(vehicle_make=v["vehicle_make"], vehicle_model=v["vehicle_model"])
            c = self.emit("anpr", anpr, "plate_read", t + timedelta(seconds=9), severity="informational", confidence=0.95,
                          payload=self.anpr_payload(anpr, t, control_v), scenario=s)
            s.title = f"Same plate, contradictory vehicle details at {site.name}"
            s.description = "ANPR and enforcement camera read the same plate seconds apart but report different make/colour (possible cloned plate or misread). A similar vehicle with a different plate passes shortly after and is unrelated."
            s.related, s.unrelated = [a, b], [c]
            s.separate_groups = [[a, b], [c]]
            self.note(s.conflicts, "contradictory_vehicle_details", [a, b],
                      f"plate {v['plate_number']}: {v['vehicle_colour']} {v['vehicle_make']} vs {other['vehicle_colour']} {other['vehicle_make']}")
        elif variant == "conflicting_identity":
            acs, frs = device("acs-gate-a", "access_control"), device("frs-gate-a", "face_recognition")
            t = self.pick_time([acs.device_id, frs.device_id])
            holder, cred = self.employee()
            other, _ = self.employee()
            while other == holder:
                other, _ = self.employee()
            a = self.emit("access_control", acs, "access_granted", t, severity="informational", confidence=None, scenario=s,
                          payload={"credential_id": cred, "holder_id": holder, "door_id": "Gate-A-D2", "result": "granted", "reason": "valid_credential", "direction": "in"})
            b = self.emit("face_recognition", frs, "face_match", t + timedelta(seconds=1), severity="low", confidence=0.91, scenario=s,
                          payload={"match_status": "matched", "subject_id": other, "match_score": 0.91, "face_quality": 0.88, "gallery": "staff"})
            s.title = "Badge holder and recognised face disagree at Gate-A"
            s.description = "A credential belonging to one employee is used while face recognition matches a different enrolled employee at the same door (possible credential sharing or misidentification)."
            s.related = [a, b]
            self.note(s.conflicts, "conflicting_identity", [a, b], f"credential holder {holder} vs face match {other}")
        elif variant == "conflicting_timestamps":
            site = self.rng.choice([z for z in world.INTERSECTIONS if (z.speed_limit or 0) >= 80])
            n = site.name[-2:]
            radar, tv = device(f"radar-int{n}", "radar"), device(f"tcam-int{n}", "traffic_violation")
            t = self.pick_time([radar.device_id, tv.device_id], before=timedelta(minutes=15))
            v = self.new_vehicle()
            trk = f"T{self.rng.randint(10000, 99999)}"
            limit = site.speed_limit or 80
            speed = limit + self.rng.randint(30, 50)
            skew = timedelta(minutes=self.rng.uniform(5, 12))
            a = self.emit("radar", radar, "speed_violation", t, severity="high", confidence=0.97, payload=self.radar_payload(trk, speed, limit, True), scenario=s)
            b = self.emit("traffic_violation", tv, "speeding", t - skew, severity="high", confidence=0.95, received=t + timedelta(seconds=3), scenario=s,
                          payload={"violation_type": "speeding", **{k: v[k] for k in ("plate_number", "plate_region", "vehicle_make", "vehicle_model", "vehicle_colour")},
                                   "measured_speed_kph": speed, "speed_limit_kph": limit, "radar_device_id": radar.device_id, "radar_track_id": trk,
                                   "lane": 2, "evidence_images": [self.img(tv, t)]})
            s.title = f"Radar and enforcement camera timestamps disagree at {site.name}"
            s.description = "The violation record references the radar track id of a radar event but its occurred_at is several minutes earlier (camera clock drift)."
            s.related = [a, b]
            self.note(s.conflicts, "conflicting_timestamps", [a, b], f"same radar track {trk}, occurred_at differs by {int(skew.total_seconds())}s")
        elif variant == "conflicting_locations":
            n = self.rng.randint(1, 12)
            gps = device(f"gps-flt-{n:02d}", "gps_fleet")
            home = world.fleet_home(n)
            anpr = next(d for d in DEVICES_BY_SOURCE["anpr"] if d.zone == home.name)  # the vehicle really is on its beat
            t = self.pick_time([gps.device_id, anpr.device_id])
            v = self.fleet_vehicle(n)
            depot = ZONE_BY_NAME["Fleet-Depot"]
            a = self.emit("gps_fleet", gps, "position_update", t, severity="informational", confidence=None, scenario=s,
                          location={"latitude": round(depot.lat + 0.0004, 6), "longitude": round(depot.lon - 0.0003, 6), "zone": "Fleet-Depot"},
                          payload=self.gps_payload(n, v, speed=0))
            b = self.emit("anpr", anpr, "plate_read", t + timedelta(seconds=40), severity="informational", confidence=0.97, scenario=s,
                          payload=self.anpr_payload(anpr, t, {k: v[k] for k in ("plate_number", "plate_region", "vehicle_make", "vehicle_model", "vehicle_colour", "vehicle_type")}))
            s.title = f"Fleet vehicle FLT-{n:02d} reported in two distant places"
            s.description = (f"GPS places the vehicle at the depot while ANPR at {home.name}, "
                             f"{world.distance_km(depot.lat, depot.lon, home.lat, home.lon):.1f} km away, reads its plate 40 seconds later (GPS fault or cloned plate).")
            s.related = [a, b]
            self.note(s.conflicts, "conflicting_locations", [a, b], f"{world.distance_km(depot.lat, depot.lon, home.lat, home.lon):.1f} km apart within 40 s")
        else:  # conflicting_sensor_results
            side = self.rng.choice(["north", "east"])
            pids = self.rng.choice([d for d in DEVICES_BY_SOURCE["perimeter_intrusion"] if d.zone == f"Perimeter-{side.title()}"])
            cctv = device(f"cctv-perim-{side}", "cctv")
            t = self.pick_time([pids.device_id, cctv.device_id])
            a = self.emit("perimeter_intrusion", pids, "fence_cut", t, severity="high", confidence=0.86, scenario=s,
                          payload={"sensor_segment": f"{side[0].upper()}-{self.rng.randint(1, 24):02d}", "alarm_type": "fence_cut", "intensity": 0.81, "duration_ms": 2400})
            b = self.emit("cctv", cctv, "scene_clear", t + timedelta(seconds=6), severity="informational", confidence=0.88, scenario=s,
                          payload={"observation_type": "alarm_verification", "object_category": "none", "object_count": 0,
                                   "track_id": None, "verification_result": "no_activity"})
            s.title = f"Fence sensor alarm not confirmed by camera at Perimeter-{side.title()}"
            s.description = "The fence sensor reports a cut while the camera covering the section reports no activity seconds later."
            s.related = [a, b]
            self.note(s.conflicts, "conflicting_sensor_results", [a, b], "fence_cut alarm vs camera verification no_activity")

    def fleet_vehicle(self, n: int) -> dict[str, str]:
        rng = random.Random(1000 + n)  # fixed per fleet vehicle, independent of dataset seed
        make, model, vtype = [("Toyota", "Land Cruiser", "suv"), ("Nissan", "Patrol", "suv"), ("Toyota", "Hilux", "pickup")][n % 3]
        return {"plate_number": f"Z {40000 + n * 113}", "plate_region": "ZN", "vehicle_make": make, "vehicle_model": model,
                "vehicle_colour": "white", "vehicle_type": vtype, "route": world.FLEET_ROUTES[n % len(world.FLEET_ROUTES)],
                "driver_id": f"DRV-{200 + rng.randint(0, 99)}"}

    def gps_payload(self, n: int, v: dict[str, str], speed: int | None = None) -> dict[str, Any]:
        return {"vehicle_id": f"FLT-{n:02d}", "plate_number": v["plate_number"], "route": v["route"], "driver_id": v["driver_id"],
                "speed_kph": speed if speed is not None else self.rng.randint(0, 95), "heading_deg": self.rng.randint(0, 359),
                "hdop": round(self.rng.uniform(0.6, 2.4), 1), "ignition": "on"}

    def scn_g_reverse_order(self) -> None:
        n = self.rng.randint(1, 12)
        gps = device(f"gps-flt-{n:02d}", "gps_fleet")
        t = self.pick_time([gps.device_id], after=timedelta(minutes=30))
        v = self.fleet_vehicle(n)
        s = self.new_scenario("G", "reverse_order_arrival", f"FLT-{n:02d} position trail delivered in reverse order",
                              "A buffered GPS trail is uploaded after a coverage gap; records arrive newest-first.")
        site = world.fleet_home(n)
        points = self.rng.randint(4, 7)
        flush = t + timedelta(minutes=points * 0.5 + self.rng.uniform(5, 15))
        trail = []
        for i in range(points):
            ti = t + timedelta(seconds=30 * i)
            loc = {"latitude": round(site.lat + 0.0009 * i, 6), "longitude": round(site.lon + 0.0006 * i, 6), "zone": site.name}
            recv = flush + timedelta(seconds=points - i)
            trail.append(self.emit("gps_fleet", gps, "position_update", ti, severity="informational", confidence=None, location=loc,
                                   received=recv, payload=self.gps_payload(n, v), scenario=s))
        s.related = trail
        self.note(s.anomalies, "out_of_order_arrival", trail, "arrival order is the reverse of occurrence order")
        self.note(s.anomalies, "delayed_event", trail, f"delivered {int((flush - t).total_seconds())}s+ after the first position")

    def scn_g_clock_skew(self) -> None:
        dev = self.rng.choice([device("cctv-carpark-p2", "cctv"), device("wx-expressway", "weather_environmental"), device("cctv-depot", "cctv")])
        t = self.pick_time([dev.device_id], after=timedelta(hours=1))
        skew = timedelta(minutes=self.rng.uniform(3, 10))
        s = self.new_scenario("G", "clock_skew", f"{dev.device_id} reports timestamps in the future",
                              "The device clock runs ahead: occurred_at is later than received_at (suspicious but structurally valid).", expected_incident=False)
        subs = []
        for i in range(3):
            real = t + timedelta(minutes=12 * i)
            if dev.source == "cctv":
                subs.append(self.emit("cctv", dev, "vehicle_detected", real + skew, severity="informational", confidence=0.84, received=real + timedelta(seconds=2),
                                      sort_time=real + timedelta(seconds=2), scenario=s, payload=self.cctv_payload(dev, "vehicle_detected", "sedan", colour="white")))
            else:
                subs.append(self.emit("weather_environmental", dev, "weather_reading", real + skew, severity="informational", confidence=None,
                                      received=real + timedelta(seconds=5), sort_time=real + timedelta(seconds=5), scenario=s, payload=self.weather_payload()))
        s.related = subs
        self.note(s.anomalies, "timestamp_inconsistency", subs, f"received_at earlier than occurred_at by about {int(skew.total_seconds())}s")

    def scn_g_store_and_forward(self, k: int) -> int:
        dev = self.rng.choice([d for d in world.DEVICES if d.source in ("anpr", "cctv", "access_control", "radar")])
        t = self.pick_time([dev.device_id], after=timedelta(hours=3))
        window = timedelta(minutes=self.rng.uniform(30, 150))
        flush = t + window + timedelta(seconds=self.rng.uniform(30, 600))
        s = self.new_scenario("G", "store_and_forward_delay", f"{dev.device_id} uploads buffered events late",
                              "Device lost its uplink and forwarded its buffered events in one burst after reconnecting.", expected_incident=False)
        subs = []
        times = sorted(t + timedelta(seconds=self.rng.uniform(0, window.total_seconds())) for _ in range(k))
        for i, ti in enumerate(times):
            sub = self.background_event(dev.source, ti, dev=dev, received=flush + timedelta(seconds=0.2 * i), scenario=s)
            subs.append(sub)
        s.related = subs
        self.note(s.anomalies, "delayed_event", subs, f"delays between {int((flush - times[-1]).total_seconds())}s and {int((flush - times[0]).total_seconds())}s")
        return len(subs)

    def scn_g_late_report(self) -> int:
        t = self.diurnal_time(margin=timedelta(hours=10))
        zone = self.rng.choice(["Gate-A", "Car-Park-P2", "Perimeter-South", "Control-Room"])
        delay = timedelta(hours=self.rng.uniform(2, 8))
        s = self.new_scenario("G", "late_operator_entry", "Operator report entered hours after the observation",
                              "An operator logged an observation at the end of the shift; received_at is hours after occurred_at.", expected_incident=False)
        sub = self.operator_report(t, zone, "shift_note", f"Patrol noted an unattended delivery van at {zone}; driver returned and left.",
                                   "low", None, s, severity="low", received=t + delay)
        s.related = [sub]
        self.note(s.anomalies, "delayed_event", [sub], f"received {int(delay.total_seconds())}s after occurrence")
        return 1

    def scn_i_low_confidence(self) -> int:
        kind = self.rng.choice(["face", "cctv", "anpr_unreadable", "anpr_partial"])
        s = self.new_scenario("I", kind, "", "", expected_incident=False)
        subs = []
        if kind == "face":
            dev = self.rng.choice(DEVICES_BY_SOURCE["face_recognition"])
            t = self.pick_time([dev.device_id], after=timedelta(minutes=5))
            candidate, _ = self.employee()
            for i in range(self.rng.randint(1, 3)):
                score = round(self.rng.uniform(0.32, 0.55), 3)
                subs.append(self.emit("face_recognition", dev, "face_uncertain", t + timedelta(seconds=20 * i), severity="low", confidence=score, scenario=s,
                                      payload={"match_status": "uncertain", "subject_id": None, "candidate_subject_id": candidate, "match_score": score,
                                               "face_quality": round(self.rng.uniform(0.25, 0.5), 2), "gallery": "staff"}))
            s.title, s.description = f"Uncertain face matches at {dev.zone}", "Face recognition offers only a weak candidate match; identity must not be treated as known."
        elif kind == "cctv":
            dev = self.rng.choice(DEVICES_BY_SOURCE["cctv"])
            t = self.pick_time([dev.device_id], after=timedelta(minutes=5))
            for i in range(self.rng.randint(1, 2)):
                subs.append(self.emit("cctv", dev, "person_detected", t + timedelta(seconds=40 * i), severity="low", confidence=self.rng.uniform(0.15, 0.35),
                                      scenario=s, payload=self.cctv_payload(dev, "person_detected", "person", image_quality="low_light")))
            s.title, s.description = f"Low-confidence detection on {dev.device_id}", "Video analytics detection with very low confidence (poor light)."
        else:
            dev = self.rng.choice(DEVICES_BY_SOURCE["anpr"])
            t = self.pick_time([dev.device_id], after=timedelta(minutes=5))
            v = self.background_vehicle(dev.zone)
            if kind == "anpr_unreadable":
                v = dict(v, plate_number=None, plate_region=None)
                payload = self.anpr_payload(dev, t, v, read_status="unreadable")
                conf = self.rng.uniform(0.05, 0.2)
            else:
                plate = v["plate_number"]
                v = dict(v, plate_number=plate[:2] + "?" + plate[3:5] + "?" + plate[6:])
                payload = self.anpr_payload(dev, t, v, read_status="partial")
                conf = self.rng.uniform(0.25, 0.45)
            subs.append(self.emit("anpr", dev, "plate_read", t, severity="informational", confidence=conf, payload=payload, scenario=s))
            s.title, s.description = f"Unreliable plate read on {dev.device_id}", "ANPR could not read the plate reliably; the vehicle identity is unknown."
        s.related = subs
        self.note(s.uncertainty, "low_confidence_detection", subs, "confidence below 0.6")
        self.note(s.anomalies, "low_confidence_detection", subs, "confidence below 0.6")
        return len(subs)

    def scn_j_spike(self, variant: str, big: bool) -> None:
        s = self.new_scenario("J", variant, "", "", expected_incident=False)
        subs: list[Sub] = []
        if variant == "repeated_device_events":
            dev = self.rng.choice([d for d in DEVICES_BY_SOURCE["cctv"] if "perim" in d.device_id or "carpark" in d.device_id])
            t = self.pick_time([dev.device_id], after=timedelta(minutes=20))
            for _ in range(self.rng.randint(20, 40) if big else 12):
                ti = t + timedelta(seconds=self.rng.uniform(0, 900))
                subs.append(self.emit("cctv", dev, "motion_detected", ti, severity="low", confidence=self.rng.uniform(0.4, 0.7), scenario=s,
                                      payload=self.cctv_payload(dev, "motion_detected", "unknown", region="vegetation_mask_edge")))
            s.title, s.description = f"Repeated motion alerts from {dev.device_id}", "One camera raises the same alert many times in 15 minutes (likely nuisance trigger)."
            self.note(s.anomalies, "repeated_device_events", subs, f"{len(subs)} motion alerts in 15 minutes")
            s.separate_groups = []
        elif variant == "access_failure_spike":
            acs = self.rng.choice(DEVICES_BY_SOURCE["access_control"])
            t = self.pick_time([acs.device_id], after=timedelta(minutes=10))
            for _ in range(self.rng.randint(12, 20) if big else 10):
                holder, cred = self.employee()
                ti = t + timedelta(seconds=self.rng.uniform(0, 300))
                subs.append(self.emit("access_control", acs, "access_denied", ti, severity="low", confidence=None, scenario=s,
                                      payload={"credential_id": cred, "holder_id": holder, "door_id": f"{acs.zone}-D1", "result": "denied",
                                               "reason": "reader_error", "direction": "in"}))
            s.title, s.description = f"Spike of denied access at {acs.zone}", "Many different valid staff credentials denied within 5 minutes (reader fault or controller sync issue, not one intruder)."
            self.note(s.anomalies, "access_failure_spike", subs, f"{len(subs)} denials across different credentials in 5 minutes")
        elif variant == "event_spike":
            dev = device("dc-auth-01", "network_security")
            t = self.pick_time([dev.device_id], after=timedelta(minutes=10))
            src = f"{self.rng.choice(world.EXTERNAL_NETS)}{self.rng.randint(2, 254)}"
            target = self.rng.choice(world.NETWORK_USERS)
            for _ in range(self.rng.randint(30, 60) if big else 15):
                ti = t + timedelta(seconds=self.rng.uniform(0, 180))
                subs.append(self.emit("network_security", dev, "auth_failure", ti, severity="medium", confidence=0.99, scenario=s,
                                      payload={"src_ip": src, "dst_ip": "10.20.0.10", "dst_port": 443, "protocol": "https", "username": target,
                                               "auth_method": "password", "action": "deny", "failure_reason": "bad_password"}))
            last = max(x.occurred for x in subs)
            success = self.emit("network_security", dev, "auth_success", last + timedelta(seconds=20), severity="high", confidence=0.99, scenario=s,
                                payload={"src_ip": src, "dst_ip": "10.20.0.10", "dst_port": 443, "protocol": "https", "username": target,
                                         "auth_method": "password", "action": "allow"})
            subs.append(success)
            s.title, s.description = f"Password-guessing burst against {target}", "Burst of failed logins from one external address, followed by a success (possible account compromise)."
            s.expected_incident = True
            self.note(s.anomalies, "event_spike", subs[:-1], f"{len(subs) - 1} failures from {src} in 3 minutes")
        else:  # localized_cluster
            site = self.rng.choice(world.INTERSECTIONS)
            n = site.name[-2:]
            radar, tv = device(f"radar-int{n}", "radar"), device(f"tcam-int{n}", "traffic_violation")
            t = self.pick_time([radar.device_id, tv.device_id], after=timedelta(minutes=25))
            groups = []
            limit = site.speed_limit or 60
            for _ in range(self.rng.randint(6, 10) if big else 5):
                ti = t + timedelta(seconds=self.rng.uniform(0, 1200))
                v = self.new_vehicle()
                trk = f"T{self.rng.randint(10000, 99999)}"
                speed = limit + self.rng.randint(12, 30)
                a = self.emit("radar", radar, "speed_violation", ti, severity="medium", confidence=0.95, payload=self.radar_payload(trk, speed, limit, True), scenario=s)
                b = self.emit("traffic_violation", tv, "speeding", ti + timedelta(seconds=0.7), severity="medium", confidence=0.93, scenario=s,
                              payload={"violation_type": "speeding", **{k: v[k] for k in ("plate_number", "plate_region", "vehicle_make", "vehicle_model", "vehicle_colour")},
                                       "measured_speed_kph": speed, "speed_limit_kph": limit, "radar_device_id": radar.device_id, "radar_track_id": trk,
                                       "lane": self.rng.randint(1, 3), "evidence_images": [self.img(tv, ti)]})
                groups.append([a, b])
                subs += [a, b]
            s.title, s.description = f"Cluster of speeding violations at {site.name}", "Many different vehicles speeding at one site within 20 minutes: a location trend, not one incident."
            s.separate_groups = groups
            self.note(s.anomalies, "localized_cluster", subs, f"{len(groups)} vehicles in 20 minutes")
        s.related = subs

    def scn_k_unrelated(self, variant: str) -> None:
        s = self.new_scenario("K", variant, "", "", expected_incident="separate")
        if variant == "same_zone_different_subjects":
            site = self.rng.choice(world.INTERSECTIONS)
            n = site.name[-2:]
            anpr, radar = device(f"anpr-int{n}", "anpr"), device(f"radar-int{n}", "radar")
            t = self.pick_time([anpr.device_id, radar.device_id])
            v1, v2 = self.new_vehicle(), self.new_vehicle()
            g1 = [self.emit("anpr", anpr, "plate_read", t, severity="informational", confidence=0.97, payload=self.anpr_payload(anpr, t, v1), scenario=s)]
            t2 = t + timedelta(seconds=self.rng.uniform(8, 20))
            g2 = [self.emit("anpr", anpr, "plate_read", t2, severity="informational", confidence=0.96, payload=self.anpr_payload(anpr, t2, v2), scenario=s),
                  self.emit("radar", radar, "speed_violation", t2 + timedelta(seconds=0.5), severity="low", confidence=0.94,
                            payload=self.radar_payload(f"T{self.rng.randint(10000, 99999)}", (site.speed_limit or 60) + 9, site.speed_limit or 60, False), scenario=s)]
            s.title, s.description = f"Two different vehicles at {site.name} within seconds", "Same place and almost the same time, different plates: separate observations."
        elif variant == "same_time_different_incidents":
            acs, pids = device("acs-gate-a", "access_control"), self.rng.choice([d for d in DEVICES_BY_SOURCE["perimeter_intrusion"] if d.zone == "Perimeter-South"])
            t = self.pick_time([acs.device_id, pids.device_id])
            holder, cred = self.employee()
            g1 = [self.emit("access_control", acs, "access_denied", t, severity="low", confidence=None, scenario=s,
                            payload={"credential_id": cred, "holder_id": holder, "door_id": "Gate-A-D1", "result": "denied", "reason": "expired_credential", "direction": "in"})]
            g2 = [self.emit("perimeter_intrusion", pids, "fence_vibration", t + timedelta(seconds=self.rng.uniform(5, 40)), severity="medium", confidence=0.74, scenario=s,
                            payload={"sensor_segment": f"S-{self.rng.randint(1, 24):02d}", "alarm_type": "vibration", "intensity": 0.55, "duration_ms": 1500})]
            s.title, s.description = "Access denial and fence alarm in the same minute", "Simultaneous events in different areas with no shared subject: separate."
        elif variant == "same_device_separate_observations":
            site = self.rng.choice(world.INTERSECTIONS)
            n = site.name[-2:]
            radar = device(f"radar-int{n}", "radar")
            t = self.pick_time([radar.device_id], after=timedelta(minutes=10))
            limit = site.speed_limit or 60
            g1 = [self.emit("radar", radar, "speed_violation", t, severity="medium", confidence=0.96, payload=self.radar_payload(f"T{self.rng.randint(10000, 99999)}", limit + 22, limit, True), scenario=s)]
            t2 = t + timedelta(minutes=self.rng.uniform(3, 6))
            g2 = [self.emit("radar", radar, "speed_violation", t2, severity="medium", confidence=0.95, payload=self.radar_payload(f"T{self.rng.randint(10000, 99999)}", limit + 24, limit, True), scenario=s)]
            s.title, s.description = f"Two speeding measurements by {radar.device_id}", "Same device, same event type, different radar tracks minutes apart: different vehicles."
        else:  # similar_types_no_relation
            a_site, b_site = self.rng.sample(world.INTERSECTIONS, 2)
            ta = device(f"tcam-int{a_site.name[-2:]}", "traffic_violation")
            tb = device(f"tcam-int{b_site.name[-2:]}", "traffic_violation")
            t = self.pick_time([ta.device_id, tb.device_id])
            out = []
            for dev, tt in ((ta, t), (tb, t + timedelta(seconds=self.rng.uniform(5, 50)))):
                v = self.new_vehicle()
                out.append(self.emit("traffic_violation", dev, "red_light_violation", tt, severity="high", confidence=0.94, scenario=s,
                                     payload={"violation_type": "red_light", **{k: v[k] for k in ("plate_number", "plate_region", "vehicle_make", "vehicle_model", "vehicle_colour")},
                                              "signal_phase": "red", "time_since_red_ms": self.rng.randint(500, 2500), "stop_line_crossed": True,
                                              "lane": 1, "evidence_images": [self.img(dev, tt)]}))
            g1, g2 = [out[0]], [out[1]]
            s.title, s.description = "Red-light violations at two intersections in the same minute", "Similar event types, different places and vehicles: no relationship."
        s.related = []
        s.unrelated = g1 + g2
        s.separate_groups = [g1, g2]

    def scn_l_ambiguous(self, variant: str) -> None:
        s = self.new_scenario("L", variant, "", "", expected_incident="uncertain")
        if variant == "missing_entity_identifiers":
            cctv, anpr = device("cctv-carpark-p2", "cctv"), device("anpr-carpark-p2", "anpr")
            t = self.pick_time([cctv.device_id, anpr.device_id])
            a = self.emit("cctv", cctv, "person_detected", t, severity="low", confidence=0.66, scenario=s,
                          payload={"observation_type": "person_detected", "object_category": "person", "object_count": 1, "track_id": None})
            v = self.background_vehicle(anpr.zone)
            b = self.emit("anpr", anpr, "plate_read", t + timedelta(minutes=self.rng.uniform(2, 6)), severity="informational", confidence=0.41, scenario=s,
                          payload=self.anpr_payload(anpr, t, dict(v, plate_number=v["plate_number"][:3] + "???" + v["plate_number"][6:]), read_status="partial"))
            subs = [a, b]
            s.title, s.description = "Person and partial plate in Car-Park-P2", "No track id and only a partial plate: there is no evidence that these observations are linked."
            self.note(s.uncertainty, "missing_entity_identifier", subs, "no stable identifier to link the person and the vehicle")
        elif variant == "insufficient_linking_evidence":
            pids = self.rng.choice([d for d in DEVICES_BY_SOURCE["perimeter_intrusion"] if d.zone == "Perimeter-East"])
            t = self.pick_time([pids.device_id], after=timedelta(hours=1))
            a = self.operator_report(t, "Perimeter-East", "suspicious_activity", "Caller reported a suspicious vehicle somewhere near the east fence; no plate or description given.",
                                     "medium", None, s, severity="low")
            b = self.emit("perimeter_intrusion", pids, "fence_vibration", t + timedelta(minutes=self.rng.uniform(20, 45)), severity="low", confidence=0.58, scenario=s,
                          payload={"sensor_segment": f"E-{self.rng.randint(1, 24):02d}", "alarm_type": "vibration", "intensity": 0.32, "duration_ms": 700})
            subs = [a, b]
            s.title, s.description = "Vague report and later weak fence alert at Perimeter-East", "Same area, half an hour apart, no shared identifier: possibly related, cannot be confirmed."
            self.note(s.uncertainty, "insufficient_linking_evidence", subs, "only zone proximity; 20-45 minutes apart")
        elif variant == "unresolved_conflicting_evidence":
            acs, frs = device("acs-gate-b", "access_control"), device("frs-gate-b", "face_recognition")
            t = self.pick_time([acs.device_id, frs.device_id])
            holder, cred = self.employee()
            candidate, _ = self.employee()
            a = self.emit("face_recognition", frs, "face_uncertain", t, severity="low", confidence=0.58, scenario=s,
                          payload={"match_status": "uncertain", "subject_id": None, "candidate_subject_id": candidate, "match_score": 0.58, "face_quality": 0.52, "gallery": "staff"})
            b = self.emit("access_control", acs, "access_granted", t + timedelta(seconds=self.rng.uniform(30, 60)), severity="informational", confidence=None, scenario=s,
                          payload={"credential_id": cred, "holder_id": holder, "door_id": "Gate-B-D1", "result": "granted", "reason": "valid_credential", "direction": "in"})
            subs = [a, b]
            s.title, s.description = "Weak face candidate and a different badge holder at Gate-B", "Evidence points to different people but the face match is too weak to call it a conflict; unresolved."
            self.note(s.uncertainty, "unresolved_conflict", subs, f"face candidate {candidate} (0.58) vs credential holder {holder}")
        else:  # missing_location
            dev = device("ids-core-01", "network_security")
            t = self.pick_time([dev.device_id])
            a = self.emit("network_security", dev, "ids_alert", t, severity="medium", confidence=0.62, scenario=s, location=None,
                          payload={"src_ip": f"{world.INTERNAL_NET}{self.rng.randint(30, 90)}.{self.rng.randint(2, 254)}", "dst_ip": f"{self.rng.choice(world.EXTERNAL_NETS)}{self.rng.randint(2, 254)}",
                                   "dst_port": 8443, "protocol": "tls", "signature": "Suspicious beacon interval", "rule_id": "ZN-IDS-4410", "asset_owner": None})
            subs = [a]
            s.title, s.description = "Network alert with no asset or location mapping", "Internal host is not in the asset register; the alert cannot be tied to a site or device."
            self.note(s.uncertainty, "uncorrelatable_event", subs, "no location and unknown asset owner")
        s.related = subs

    # ================================================================ invalid records & duplicates

    def invalid_record(self, variant: str, scenario: Scenario) -> None:
        source = self.rng.choice(list(BACKGROUND_MIX))
        t = self.diurnal_time()
        dev = self.rng.choice(DEVICES_BY_SOURCE.get(source) or world.DEVICES)
        base = {
            "event_id": self.new_event_id(),
            "source_type": (dev.source_label if dev.source == source else None) or source,
            "device_id": dev.device_id,
            "event_type": "plate_read" if source == "anpr" else "status_report",
            "occurred_at": self.fmt(t, dev.time_style),
            "received_at": self.fmt(t + timedelta(seconds=2), dev.time_style),
            "location": self.location_of(dev),
            "severity": "low",
            "confidence": 0.8,
            "payload": {"note": "sensor message"},
        }
        record: Any = base
        raw_line = None
        if variant.startswith("missing_"):
            del base[variant.removeprefix("missing_")]
        elif variant == "wrong_type_device_id":
            base["device_id"] = self.rng.randint(1000, 9999)
        elif variant == "wrong_type_confidence":
            base["confidence"] = "high"
        elif variant == "invalid_timestamp_format":
            base["occurred_at"] = t.strftime("%d/%m/%Y %H:%M")
        elif variant == "invalid_timestamp_value":
            base["occurred_at"] = "2026-02-30T25:61:00Z"
        elif variant == "timestamp_missing_timezone":
            base["occurred_at"] = t.strftime("%Y-%m-%dT%H:%M:%S")
        elif variant == "invalid_latitude":
            base["location"] = {"latitude": round(self.rng.uniform(91, 180), 4), "longitude": 55.3, "zone": dev.zone}
        elif variant == "invalid_longitude":
            base["location"] = {"latitude": 25.2, "longitude": round(self.rng.uniform(-400, -181), 4), "zone": dev.zone}
        elif variant == "confidence_above_one":
            base["confidence"] = round(self.rng.uniform(1.05, 9.9), 2)
        elif variant == "confidence_negative":
            base["confidence"] = round(-self.rng.uniform(0.01, 1), 2)
        elif variant == "unknown_source_type":
            base["source_type"] = self.rng.choice(["toaster_sensor", "lidar_v9", "unknown"])
        elif variant == "payload_not_object":
            base["payload"] = "plate=Q 12345;speed=88"
        elif variant == "invalid_severity":
            base["severity"] = self.rng.choice(["urgent", "sev1", "P2"])
        elif variant == "malformed_json_truncated":
            text = json.dumps(base, separators=(",", ":"))
            raw_line = text[: self.rng.randint(40, len(text) - 10)]
        elif variant == "malformed_json_legacy_line":
            raw_line = f"ANPR|{dev.device_id}|Q {self.rng.randint(10000, 99999)}|{t:%Y-%m-%d %H:%M:%S}|CONF=0.91"
        elif variant == "not_an_object":
            record = [base["event_id"], base["source_type"], base["occurred_at"]]
        elif variant == "empty_object":
            record = {}
        sub = Sub(record=record, raw_line=raw_line, sort_time=t + timedelta(seconds=self.rng.uniform(1, 60)), seq=self._next_seq(),
                  valid=False, event_id=base.get("event_id") if isinstance(base.get("event_id"), str) and variant not in SYNTAX_INVALID and record is base else None,
                  invalid_reasons=[variant])
        self._add(sub, scenario)

    def duplicates(self, n: int) -> None:
        pool = [e for e in self.valid_events]
        exact_turn = True
        while n > 0 and pool:
            target = pool.pop(self.rng.randrange(len(pool)))  # each original is repeated by one scenario only
            copies = 1 if (n == 1 or self.rng.random() < 0.7) else 2
            s = self.new_scenario("F", "", f"Repeated submissions of {target.event_id}", "", expected_incident=False)
            kinds = []
            for _ in range(min(copies, n)):
                kind = "exact" if exact_turn else "altered"
                exact_turn = not exact_turn
                rec = copy.deepcopy(target.record)
                key = target.content_key
                if kind == "exact":
                    when = target.sort_time + timedelta(seconds=self.rng.uniform(1, 1800))
                else:
                    self.alter(rec)
                    key = f"{target.event_id}#{self._next_seq()}"
                    if self.rng.random() < 0.9:
                        when = target.sort_time + timedelta(seconds=self.rng.uniform(60, 10800))
                    else:  # the re-sent version overtakes the original in transit (never before the event occurred)
                        when = target.occurred + (target.sort_time - target.occurred) * self.rng.uniform(0.1, 0.9)
                dup = Sub(record=rec, sort_time=when, seq=self._next_seq(), valid=True, event_id=target.event_id, content_key=key,
                          source=target.source, device_id=target.device_id, occurred=target.occurred)
                self._add(dup, s)
                kinds.append(kind)
                n -= 1
            s.variant = "+".join(kinds)
            s.description = "The same event_id is submitted more than once: identical retransmission and/or a re-sent version with changed values."
            s.related = [target]
            # duplicate subs appear in s.subs; the original is referenced via related

    def alter(self, rec: dict[str, Any]) -> None:
        payload = rec["payload"]
        choice = self.rng.choice(["confidence", "severity", "payload"])
        if choice == "confidence" and "confidence" in rec:
            rec["confidence"] = round(max(0.0, rec["confidence"] - self.rng.uniform(0.1, 0.3)), 3)
        elif choice == "severity" and "severity" in rec:
            order = ["informational", "low", "medium", "high", "critical"]
            rec["severity"] = order[(order.index(rec["severity"]) + 1) % len(order)]
        elif "plate_number" in payload and payload["plate_number"]:
            plate = payload["plate_number"]
            payload["plate_number"] = plate[:-1] + str((int(plate[-1]) + 1) % 10) if plate[-1].isdigit() else plate + "1"
        else:
            payload["revision"] = 2

    # ================================================================ background

    def background_event(self, source: str, t: datetime, dev: Device | None = None, received: Any = "auto", scenario: Scenario | None = None) -> Sub:
        rng = self.rng
        if dev is None and source != "device_health":  # health events pick their monitored target below
            options = [d for d in DEVICES_BY_SOURCE[source] if self.available(d.device_id, t)]
            if not options:
                raise BudgetExceeded("no device")
            dev = rng.choice(options)
        kw: dict[str, Any] = {"received": received, "scenario": scenario}
        if source == "anpr":
            v = self.background_vehicle(dev.zone)
            return self.emit(source, dev, "plate_read", t, severity="informational", confidence=rng.uniform(0.82, 0.99), payload=self.anpr_payload(dev, t, v), **kw)
        if source == "cctv":
            obs, cat = rng.choice([("vehicle_detected", "sedan"), ("vehicle_detected", "suv"), ("person_detected", "person"), ("motion_detected", "unknown"), ("crowd_count", "person")])
            count = rng.randint(3, 25) if obs == "crowd_count" else 1
            return self.emit(source, dev, obs, t, severity="informational", confidence=rng.uniform(0.6, 0.97), payload=self.cctv_payload(dev, obs, cat, count=count), **kw)
        if source == "radar":
            zone = ZONE_BY_NAME[dev.zone]
            limit = zone.speed_limit or 60
            return self.emit(source, dev, "speed_violation", t, severity="low", confidence=rng.uniform(0.9, 0.99),
                             payload=self.radar_payload(f"T{rng.randint(10000, 99999)}", limit + rng.randint(6, 18), limit, False), **kw)
        if source == "traffic_violation":
            v = self.background_vehicle(dev.zone)
            vt = rng.choice(["red_light_violation", "lane_violation", "illegal_turn", "red_light_violation"])
            payload = {"violation_type": vt.removesuffix("_violation"), **{k: v[k] for k in ("plate_number", "plate_region", "vehicle_make", "vehicle_model", "vehicle_colour")},
                       "lane": rng.randint(1, 4), "evidence_images": [self.img(dev, t)]}
            if vt == "red_light_violation":
                payload |= {"signal_phase": "red", "time_since_red_ms": rng.randint(200, 3000), "stop_line_crossed": True}
            return self.emit(source, dev, vt, t, severity="high" if vt == "red_light_violation" else "medium", confidence=rng.uniform(0.85, 0.98), payload=payload, **kw)
        if source == "mobile_phone_detection":
            v = self.background_vehicle(dev.zone)
            return self.emit(source, dev, "phone_use_detected", t, severity="medium", confidence=rng.uniform(0.75, 0.96),
                             payload={**{k: v[k] for k in ("plate_number", "plate_region", "vehicle_make", "vehicle_model", "vehicle_colour")},
                                      "occupant": "driver", "phone_position": rng.choice(["hand_to_ear", "texting", "in_hand"]), "image_ref": self.img(dev, t)}, **kw)
        if source == "seatbelt_detection":
            v = self.background_vehicle(dev.zone)
            return self.emit(source, dev, "seatbelt_violation", t, severity="low", confidence=rng.uniform(0.75, 0.95),
                             payload={**{k: v[k] for k in ("plate_number", "plate_region", "vehicle_make", "vehicle_model", "vehicle_colour")},
                                      "occupant": rng.choice(["driver", "front_passenger"]), "belt_status": "unfastened", "image_ref": self.img(dev, t)}, **kw)
        if source == "access_control":
            holder, cred = self.employee()
            granted = rng.random() < 0.93
            return self.emit(source, dev, "access_granted" if granted else "access_denied", t, severity="informational" if granted else "low", confidence=None,
                             payload={"credential_id": cred, "holder_id": holder, "door_id": f"{dev.zone}-D{rng.randint(1, 2)}", "result": "granted" if granted else "denied",
                                      "reason": "valid_credential" if granted else rng.choice(["expired_credential", "invalid_pin", "outside_schedule"]),
                                      "direction": rng.choice(["in", "out"])}, **kw)
        if source == "face_recognition":
            if rng.random() < 0.8:
                holder, cred = self.employee()
                score = round(rng.uniform(0.82, 0.98), 3)
                reader = GATE_READERS.get(dev.zone)
                badge_time = t - timedelta(seconds=rng.uniform(0.5, 2.0))
                if reader and self.budget - len(self.subs) >= 2 and self.available(reader, badge_time):
                    # the recognised person badges through the reader the camera watches (same holder)
                    self.emit("access_control", device(reader, "access_control"), "access_granted", badge_time, severity="informational",
                              confidence=None, received=received, scenario=scenario,
                              payload={"credential_id": cred, "holder_id": holder, "door_id": f"{dev.zone}-D1", "result": "granted",
                                       "reason": "valid_credential", "direction": "in"})
                return self.emit(source, dev, "face_match", t, severity="informational", confidence=score,
                                 payload={"match_status": "matched", "subject_id": holder, "match_score": score, "face_quality": round(rng.uniform(0.7, 0.95), 2), "gallery": "staff"}, **kw)
            return self.emit(source, dev, "face_no_match", t, severity="low", confidence=round(rng.uniform(0.7, 0.9), 3),
                             payload={"match_status": "no_match", "subject_id": None, "match_score": round(rng.uniform(0.05, 0.3), 3), "face_quality": round(rng.uniform(0.6, 0.9), 2), "gallery": "staff"}, **kw)
        if source == "perimeter_intrusion":
            return self.emit(source, dev, "fence_vibration", t, severity="low", confidence=rng.uniform(0.4, 0.7),
                             payload={"sensor_segment": f"{dev.zone.split('-')[1][0]}-{rng.randint(1, 24):02d}", "alarm_type": "vibration",
                                      "intensity": round(rng.uniform(0.1, 0.35), 2), "duration_ms": rng.randint(150, 900), "probable_cause": rng.choice(["wind", "wildlife", "unknown"])}, **kw)
        if source == "device_health":
            target = rng.choice([d for d in world.DEVICES if d.source not in ("operator_report",)])
            if not self.available(target.device_id, t):
                raise BudgetExceeded("device in outage")
            return self.emit(source, target, "heartbeat", t, severity="informational", confidence=None, location=self.location_of(target),
                             payload=self.health_payload("online", None, cpu_temp_c=round(rng.uniform(38, 62), 1), uptime_s=rng.randint(10000, 2000000),
                                                         packet_loss_pct=round(rng.uniform(0, 1.5), 1)), **kw)
        if source == "operator_report":
            zone = rng.choice(["Gate-A", "Gate-B", "Car-Park-P2", "Fleet-Depot", "Control-Room", "Perimeter-North"])
            text = rng.choice([f"Routine patrol of {zone} completed; nothing to report.", f"Visitor escorted through {zone}.",
                               f"Contractor badge issued at {zone}.", f"Lighting fault reported at {zone}; facilities informed."])
            return self.operator_report(t, zone, "shift_note", text, "low", None, scenario, severity="informational", received=received)
        if source == "emergency_alarm":
            kind = rng.choice(["alarm_test", "alarm_test", "fire_alarm_fault", "panic_button_test"])
            return self.emit(source, dev, kind, t, severity="informational" if "test" in kind else "low", confidence=None,
                             payload={"alarm_type": kind, "panel_id": dev.device_id, "input_point": f"IN-{rng.randint(1, 64):02d}", "state": "cleared"}, **kw)
        if source == "gps_fleet":
            n = int(dev.device_id[-2:])
            v = self.fleet_vehicle(n)
            site = world.fleet_home(n)  # each patrol vehicle works its own beat
            loc = {"latitude": round(site.lat + rng.uniform(-0.003, 0.003), 6), "longitude": round(site.lon + rng.uniform(-0.003, 0.003), 6), "zone": site.name}
            return self.emit(source, dev, "position_update", t, severity="informational", confidence=None, location=loc, payload=self.gps_payload(n, v), **kw)
        if source == "network_security":
            kind = rng.choice(["auth_success", "auth_success", "auth_failure", "firewall_block", "firewall_block", "ids_alert"])
            src = f"{world.INTERNAL_NET}{rng.randint(1, 60)}.{rng.randint(2, 254)}" if kind.startswith("auth") else f"{rng.choice(world.EXTERNAL_NETS)}{rng.randint(2, 254)}"
            payload = {"src_ip": src, "dst_ip": f"{world.INTERNAL_NET}0.{rng.randint(2, 40)}", "dst_port": rng.choice([22, 443, 3389, 8443]), "protocol": "tcp"}
            if kind.startswith("auth"):
                payload |= {"username": rng.choice(world.NETWORK_USERS), "auth_method": rng.choice(["password", "certificate", "mfa"]), "action": "allow" if kind == "auth_success" else "deny"}
            elif kind == "ids_alert":
                payload |= {"signature": rng.choice(["Port scan detected", "Outdated TLS client", "Suspicious DNS query"]), "rule_id": f"ZN-IDS-{rng.randint(1000, 4999)}", "action": "alert"}
            else:
                payload |= {"rule_id": f"FW-{rng.randint(100, 999)}", "action": "drop"}
            sev = {"auth_success": "informational", "auth_failure": "low", "firewall_block": "informational", "ids_alert": "medium"}[kind]
            return self.emit(source, dev, kind, t, severity=sev, confidence=0.99, location={"zone": "SOC-Network"}, payload=payload, **kw)
        if source == "weather_environmental":
            payload = self.weather_payload()
            kind = "weather_reading"
            sev = "informational"
            if payload["visibility_m"] < 1000:
                kind, sev = "low_visibility", "low"
            return self.emit(source, dev, kind, t, severity=sev, confidence=None, payload=payload, **kw)
        raise ValueError(source)

    def weather_payload(self) -> dict[str, Any]:
        rng = self.rng
        return {"temperature_c": round(rng.uniform(16, 31), 1), "humidity_pct": rng.randint(35, 92), "wind_speed_kph": round(rng.uniform(2, 38), 1),
                "wind_direction_deg": rng.randint(0, 359), "visibility_m": rng.choice([10000, 10000, 8000, 6000, 3500, 800]),
                "rain_mm_h": rng.choice([0.0, 0.0, 0.0, 0.4, 2.1]), "pm10_ugm3": rng.randint(20, 160)}

    def fill_background(self, n: int) -> None:
        sources = list(BACKGROUND_MIX)
        weights = [BACKGROUND_MIX[s] for s in sources]
        # First make sure every source appears at least once (when the budget allows).
        first_pass = list(sources) if n >= len(sources) else []
        produced = 0
        while produced < n:
            from_first_pass = bool(first_pass)
            source = first_pass.pop(0) if first_pass else self.rng.choices(sources, weights)[0]
            before = len(self.subs)
            try:
                self.background_event(source, self.diurnal_time())
                produced += len(self.subs) - before
            except BudgetExceeded as exc:
                if "budget" in str(exc):
                    raise
                if from_first_pass:  # device busy at that moment: retry this source later
                    first_pass.append(source)

    # ================================================================ orchestration

    def _try(self, builder, *args) -> bool:  # noqa: ANN001
        snapshot = (len(self.subs), len(self.valid_events), len(self.scenarios), {k: list(v) for k, v in self.blackouts.items()})
        counter = dict(self.scenario_counter)
        try:
            builder(*args)
            return True
        except BudgetExceeded:
            n_subs, n_valid, n_scen, blackouts = snapshot
            del self.subs[n_subs:]
            del self.valid_events[n_valid:]
            del self.scenarios[n_scen:]
            self.blackouts = blackouts
            self.scenario_counter = counter
            return False

    def plan(self) -> list[tuple[Any, tuple]]:
        n = self.cfg.count
        scale = self.cfg.scenario_scale * n / 1000

        def count(per_1000: float, minimum: int) -> int:
            return max(minimum, round(per_1000 * scale))

        tasks: dict[str, list[tuple[Any, tuple]]] = {}
        tasks["D"] = [(self.scn_d_device_failure, ())] * count(1, 1)
        tasks["A"] = [(self.scn_a_traffic, ())] * count(3, 1)
        tasks["B"] = [(self.scn_b_access, ())] * count(2, 1)
        tasks["C"] = [(self.scn_c_perimeter, ())] * count(1, 1)
        e_variants = ["contradictory_vehicle_details", "conflicting_identity", "conflicting_timestamps", "conflicting_locations", "conflicting_sensor_results"]
        tasks["E"] = [(self.scn_e_conflict, (e_variants[i % 5],)) for i in range(count(5, 5))]
        tasks["G"] = [(self.scn_g_reverse_order, ()), (self.scn_g_clock_skew, ())] * count(1, 1)
        big = n >= 5000
        j_variants = ["repeated_device_events", "access_failure_spike", "event_spike", "localized_cluster"]
        tasks["J"] = [(self.scn_j_spike, (j_variants[i % 4], big)) for i in range(count(1, 4))]
        k_variants = ["same_zone_different_subjects", "same_time_different_incidents", "same_device_separate_observations", "similar_types_no_relation"]
        tasks["K"] = [(self.scn_k_unrelated, (k_variants[i % 4],)) for i in range(count(2, 4))]
        l_variants = ["missing_entity_identifiers", "insufficient_linking_evidence", "unresolved_conflicting_evidence", "missing_location"]
        tasks["L"] = [(self.scn_l_ambiguous, (l_variants[i % 4],)) for i in range(count(2, 4))]
        # Device failures first: their outage windows must exist before any other
        # observation is placed, so nothing is generated inside an expected absence.
        order: list[tuple[Any, tuple]] = tasks.pop("D")
        # Round-robin over the rest so that small budgets still get every scenario type.
        queues = [list(v) for v in tasks.values()]
        while any(queues):
            for q in queues:
                if q:
                    order.append(q.pop(0))
        return order

    def generate(self) -> GeneratedDataset:
        n = self.cfg.count
        n_invalid = round(n * self.cfg.invalid_rate)
        n_dup = round(n * self.cfg.duplicate_rate)
        n_delay = round(n * self.cfg.delay_rate)
        n_low = round(n * self.cfg.low_confidence_rate)
        reserve = n_invalid + n_dup

        self.budget = max(0, n - reserve)
        for builder, args in self.plan():
            self._try(builder, *args)

        # delayed / out-of-order (store-and-forward bursts + late operator entries)
        delayed = 0
        while delayed < n_delay:
            want = min(self.rng.randint(5, 25), n_delay - delayed)
            if want >= 3 and self._try(self.scn_g_store_and_forward, want):
                delayed += want
            elif self._try(self.scn_g_late_report):
                delayed += 1
            else:
                break
        low = 0
        attempts = 0
        while low < n_low and attempts < n_low * 3:
            attempts += 1
            before = len(self.subs)
            if self._try(self.scn_i_low_confidence):
                low += len(self.subs) - before

        # malformed / incomplete records
        self.budget = n - n_dup
        if n_invalid:
            per_group = len(INVALID_VARIANTS)
            remaining = n_invalid
            v = 0
            while remaining > 0:
                s = self.new_scenario("H", "mixed_defects", "Malformed and incomplete submissions",
                                      "Records that must be rejected: missing mandatory fields, wrong types, invalid values and unparseable lines.", expected_incident=False)
                for _ in range(min(per_group, remaining)):
                    self.invalid_record(INVALID_VARIANTS[v % len(INVALID_VARIANTS)], s)
                    v += 1
                    remaining -= 1

        # ordinary background activity
        self.budget = n
        background = n - len(self.subs) - n_dup
        self.fill_background(background)
        if not self.valid_events:
            self.fill_background(n_dup)
        else:
            self.duplicates(n_dup)
        assert len(self.subs) == n, (len(self.subs), n)
        return GeneratedDataset(self.cfg, self.subs, self.scenarios, self.end)


class GeneratedDataset:
    def __init__(self, config: Config, subs: list[Sub], scenarios: list[Scenario], end: datetime):
        self.config = config
        self.subs = sorted(subs, key=lambda s: (s.sort_time, s.seq))
        for i, sub in enumerate(self.subs):
            sub.index = i
        self.scenarios = scenarios
        self.end = end

    # ------------------------------------------------------------------ outputs

    def lines(self) -> list[str]:
        return [s.line() for s in self.subs]

    def write_jsonl(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            for sub in self.subs:
                fh.write(sub.line())
                fh.write("\n")

    def write_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            json.dump([s.json_value() for s in self.subs], fh, ensure_ascii=False, indent=1)
            fh.write("\n")

    def write_ground_truth(self, path: Path, data_file: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        truth = self.ground_truth(data_file)
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            json.dump(truth, fh, ensure_ascii=False, indent=1 if len(self.subs) <= 5000 else None)
            fh.write("\n")

    # ------------------------------------------------------------------ ground truth

    def expected_statuses(self) -> list[dict[str, Any]]:
        winners: dict[str, Sub] = {}
        out = []
        for sub in self.subs:
            entry: dict[str, Any] = {"index": sub.index, "line": sub.index + 1, "event_id": sub.event_id}
            if not sub.valid:
                entry["expected_status"] = "invalid"
                entry["invalid_reasons"] = sub.invalid_reasons
            elif sub.event_id in winners:
                winner = winners[sub.event_id]
                entry["expected_status"] = "duplicate"
                entry["duplicate_kind"] = "exact" if winner.content_key == sub.content_key else "conflicting"
                entry["duplicate_of_index"] = winner.index
            else:
                winners[sub.event_id] = sub
                entry["expected_status"] = "accepted"
            if sub.scenarios:
                entry["scenario_ids"] = sub.scenarios
            out.append(entry)
        return out

    def ground_truth(self, data_file: str) -> dict[str, Any]:
        statuses = self.expected_statuses()
        accepted_index = {e["event_id"]: e["index"] for e in statuses if e["expected_status"] == "accepted"}

        def ids(subs: list[Sub]) -> list[str]:
            seen: list[str] = []
            for s in subs:
                if s.valid and s.event_id and s.event_id not in seen:
                    seen.append(s.event_id)
            return seen

        def notes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [{"kind": i["kind"], "event_ids": ids(i["subs"]), "submission_indexes": sorted({s.index for s in i["subs"]}), "detail": i["detail"]} for i in items]

        scenarios = []
        separations = []
        anomaly_counts: dict[str, int] = {}
        for sc in self.scenarios:
            entry = {
                "scenario_id": sc.scenario_id,
                "type": f"{sc.letter}_{SCENARIO_TYPES[sc.letter]}",
                "variant": sc.variant,
                "title": sc.title,
                "description": sc.description,
                "expected_incident": sc.expected_incident,
                "related_event_ids": ids(sc.related),
                "unrelated_event_ids": ids(sc.unrelated),
                "submission_indexes": sorted({s.index for s in sc.subs}),
                "expected_anomalies": notes(sc.anomalies),
                "expected_conflicts": notes(sc.conflicts),
                "expected_uncertainty": notes(sc.uncertainty),
                "expected_absences": sc.absences,
            }
            if sc.letter == "F":
                # the original may itself become the duplicate when an altered copy arrives first
                dup_entries = [statuses[s.index] for s in sc.related + sc.subs]
                entry["expected_anomalies"] = [
                    {"kind": f"duplicate_{e['duplicate_kind']}", "event_ids": [e["event_id"]], "submission_indexes": [e["duplicate_of_index"], e["index"]],
                     "detail": f"submission {e['index']} repeats submission {e['duplicate_of_index']}"}
                    for e in dup_entries if e["expected_status"] == "duplicate"
                ]
            if sc.letter == "H":
                entry["expected_anomalies"] = [
                    {"kind": "invalid_record", "event_ids": [], "submission_indexes": [s.index], "detail": ",".join(s.invalid_reasons)} for s in sc.subs
                ]
            for a in entry["expected_anomalies"]:
                anomaly_counts[a["kind"]] = anomaly_counts.get(a["kind"], 0) + 1
            for c in entry["expected_conflicts"]:
                anomaly_counts[c["kind"]] = anomaly_counts.get(c["kind"], 0) + 1
            for a in entry["expected_absences"]:
                anomaly_counts["expected_absence"] = anomaly_counts.get("expected_absence", 0) + 1
            scenarios.append(entry)
            if sc.separate_groups and len(sc.separate_groups) > 1:
                separations.append({"scenario_id": sc.scenario_id, "reason": sc.description or sc.title, "groups": [ids(g) for g in sc.separate_groups]})

        outcome = {"total_submissions": len(self.subs), "accepted": 0, "invalid": 0, "duplicate": 0, "exact_duplicate": 0, "conflicting_duplicate": 0}
        for e in statuses:
            outcome[e["expected_status"]] += 1
            if e["expected_status"] == "duplicate":
                outcome[f"{e['duplicate_kind']}_duplicate"] += 1
        source_counts: dict[str, int] = {}
        for sub in self.subs:
            if sub.valid and accepted_index.get(sub.event_id) == sub.index:
                source_counts[sub.source] = source_counts.get(sub.source, 0) + 1
        scenario_counts: dict[str, int] = {}
        variant_counts: dict[str, int] = {}
        for sc in self.scenarios:
            key = f"{sc.letter}_{SCENARIO_TYPES[sc.letter]}"
            scenario_counts[key] = scenario_counts.get(key, 0) + 1
            vkey = f"{sc.letter}:{sc.variant}"
            variant_counts[vkey] = variant_counts.get(vkey, 0) + 1

        return {
            "purpose": "EVALUATION ONLY. Never load into the platform; production logic must infer relationships from event evidence.",
            "generator": {"name": "zenith-datagen", "version": GENERATOR_VERSION, **self.config.describe()},
            "data_file": data_file,
            "index_note": "submission index is 0-based in file order; line = index + 1 (JSONL line number, JSON array position)",
            "expected_outcomes": outcome,
            "source_counts": dict(sorted(source_counts.items())),
            "scenario_counts": dict(sorted(scenario_counts.items())),
            "scenario_variant_counts": dict(sorted(variant_counts.items())),
            "anomaly_counts": dict(sorted(anomaly_counts.items())),
            "scenarios": scenarios,
            "must_remain_separate": separations,
            "submissions": statuses,
        }


def generate(config: Config) -> GeneratedDataset:
    return DatasetGenerator(config).generate()

