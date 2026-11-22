"""Canonical event source types and accepted aliases.

The 15 canonical identifiers correspond one-to-one to the minimum event sources in
Section 3 of the Zenith requirements. Producers may send any alias below (case,
hyphens and spaces are ignored); the normalized event always stores the canonical
identifier and the original spelling stays in the raw submission.
"""

import re
from enum import StrEnum


class SourceType(StrEnum):
    CCTV = "cctv"
    ANPR = "anpr"
    FACE_RECOGNITION = "face_recognition"
    MOBILE_PHONE_DETECTION = "mobile_phone_detection"
    SEATBELT_DETECTION = "seatbelt_detection"
    TRAFFIC_VIOLATION = "traffic_violation"
    RADAR = "radar"
    ACCESS_CONTROL = "access_control"
    PERIMETER_INTRUSION = "perimeter_intrusion"
    DEVICE_HEALTH = "device_health"
    OPERATOR_REPORT = "operator_report"
    EMERGENCY_ALARM = "emergency_alarm"
    GPS_FLEET = "gps_fleet"
    NETWORK_SECURITY = "network_security"
    WEATHER_ENVIRONMENTAL = "weather_environmental"


SOURCE_ALIASES: dict[SourceType, tuple[str, ...]] = {
    SourceType.CCTV: ("video", "video_analytics", "camera", "cctv_camera", "surveillance_camera"),
    SourceType.ANPR: ("lpr", "alpr", "license_plate_recognition", "number_plate_recognition"),
    SourceType.FACE_RECOGNITION: ("facial_recognition", "frs", "face"),
    SourceType.MOBILE_PHONE_DETECTION: (
        "mobile_phone",
        "phone_detection",
        "mobile_phone_use",
        "handheld_phone_detection",
    ),
    SourceType.SEATBELT_DETECTION: ("seatbelt", "seat_belt", "seat_belt_detection"),
    SourceType.TRAFFIC_VIOLATION: (
        "red_light_violation",
        "red_light",
        "red_light_traffic_violation",
        "traffic_enforcement",
    ),
    SourceType.RADAR: ("speed_radar", "speed_detection", "radar_speed"),
    SourceType.ACCESS_CONTROL: ("acs", "access", "badge_reader"),
    SourceType.PERIMETER_INTRUSION: (
        "perimeter",
        "intrusion",
        "intrusion_sensor",
        "perimeter_sensor",
        "pids",
    ),
    SourceType.DEVICE_HEALTH: ("device_health_monitoring", "health", "system_health", "nms"),
    SourceType.OPERATOR_REPORT: ("operator", "manual_report"),
    SourceType.EMERGENCY_ALARM: ("emergency", "panic_alarm", "alarm"),
    SourceType.GPS_FLEET: ("gps", "fleet", "avl", "fleet_tracking"),
    SourceType.NETWORK_SECURITY: (
        "network_security_log",
        "network",
        "security_log",
        "siem",
        "firewall",
        "ids",
    ),
    SourceType.WEATHER_ENVIRONMENTAL: ("weather", "environmental", "environment", "weather_sensor"),
}

_LOOKUP: dict[str, SourceType] = {source.value: source for source in SourceType}
for _source, _aliases in SOURCE_ALIASES.items():
    for _alias in _aliases:
        assert _alias not in _LOOKUP, f"duplicate source alias {_alias}"
        _LOOKUP[_alias] = _source

_SEPARATORS = re.compile(r"[\s\-/.]+")


def normalize_source_key(value: str) -> str:
    return _SEPARATORS.sub("_", value.strip().lower()).strip("_")


def resolve_source_type(value: str) -> SourceType | None:
    """Return the canonical source for a canonical name or alias, else None."""
    return _LOOKUP.get(normalize_source_key(value))
