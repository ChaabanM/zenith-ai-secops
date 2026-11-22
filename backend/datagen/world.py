"""Fictional operating environment for generated data.

All places, devices, vehicles, people, credentials and addresses are invented.
Coordinates sit in a fictional city grid around 25.2N 55.3E; IP addresses use the
RFC 5737 documentation ranges or private space.
"""

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Zone:
    name: str
    kind: str  # intersection | checkpoint | gate | perimeter | carpark | datahall | control | depot | network | weather
    lat: float | None
    lon: float | None
    speed_limit: int | None = None
    description: str = ""


@dataclass(frozen=True)
class Device:
    device_id: str
    source: str
    zone: str
    time_style: str = "z"  # z | ms | local  (how the device formats timestamps)
    source_label: str | None = None  # alias the device firmware reports, if not canonical
    extra: dict = field(default_factory=dict, hash=False, compare=False)


ZONES: list[Zone] = [
    Zone("Harbour-Rd-INT01", "intersection", 25.265512, 55.296204, 60, "Harbour Rd / Marina St"),
    Zone("Corniche-Rd-INT02", "intersection", 25.248911, 55.306118, 60, "Corniche Rd / Al Bahar St"),
    Zone("Airport-Link-INT03", "intersection", 25.238203, 55.351707, 80, "Airport Link / Oasis Blvd"),
    Zone("Industrial-Ave-INT04", "intersection", 25.187304, 55.269411, 80, "Industrial Ave / Route 7"),
    Zone("University-Rd-INT05", "intersection", 25.159012, 55.241003, 60, "University Rd / Palm Ave"),
    Zone("Expressway-Exit21-INT06", "intersection", 25.112406, 55.200118, 100, "Expressway Exit 21 ramp"),
    Zone("Expressway-CP-KM12", "checkpoint", 25.135017, 55.220094, 120, "ANPR checkpoint, expressway KM 12"),
    Zone("Port-Access-CP", "checkpoint", 25.272104, 55.284907, 60, "Port access road checkpoint"),
    Zone("Gate-A", "gate", 25.221003, 55.329004, None, "Ops campus main gate (vehicles + staff)"),
    Zone("Gate-B", "gate", 25.219502, 55.332207, None, "Ops campus staff gate"),
    Zone("Gate-C", "gate", 25.217806, 55.330102, None, "Ops campus logistics gate"),
    Zone("Data-Hall-2", "datahall", 25.220104, 55.330705, None, "Restricted data hall"),
    Zone("Perimeter-North", "perimeter", 25.222207, 55.330511, None, "North fence line"),
    Zone("Perimeter-East", "perimeter", 25.220309, 55.333508, None, "East fence line"),
    Zone("Perimeter-South", "perimeter", 25.217004, 55.331003, None, "South fence line"),
    Zone("Car-Park-P2", "carpark", 25.218807, 55.327806, None, "Visitor car park P2"),
    Zone("Control-Room", "control", 25.219905, 55.330203, None, "Security operations control room"),
    Zone("Fleet-Depot", "depot", 25.205002, 55.315004, None, "Patrol fleet depot"),
    Zone("SOC-Network", "network", None, None, None, "Corporate network (no physical coordinates)"),
]
ZONE_BY_NAME = {z.name: z for z in ZONES}
INTERSECTIONS = [z for z in ZONES if z.kind == "intersection"]
CHECKPOINTS = [z for z in ZONES if z.kind == "checkpoint"]
ROAD_SITES = INTERSECTIONS + CHECKPOINTS


def _build_devices() -> list[Device]:
    devices: list[Device] = []
    for zone in INTERSECTIONS:
        n = zone.name[-2:]
        legacy = n in ("02", "05")  # older enforcement firmware reports an alias source name
        devices += [
            Device(f"anpr-int{n}", "anpr", zone.name, "ms"),
            Device(f"radar-int{n}", "radar", zone.name, "ms"),
            Device(f"tcam-int{n}", "traffic_violation", zone.name, "ms", "red_light_violation" if legacy else None),
            Device(f"tcam-int{n}", "mobile_phone_detection", zone.name, "ms"),
            Device(f"tcam-int{n}", "seatbelt_detection", zone.name, "ms"),
            Device(f"cctv-int{n}", "cctv", zone.name),
        ]
    devices += [
        Device("anpr-cp12", "anpr", "Expressway-CP-KM12", "ms"),
        Device("radar-cp12", "radar", "Expressway-CP-KM12", "ms"),
        Device("cctv-cp12", "cctv", "Expressway-CP-KM12"),
        Device("anpr-cpport", "anpr", "Port-Access-CP", "ms", "lpr"),
        Device("cctv-cpport", "cctv", "Port-Access-CP"),
    ]
    for gate in ("a", "b", "c"):
        zone = f"Gate-{gate.upper()}"
        devices += [
            Device(f"acs-gate-{gate}", "access_control", zone, "local"),
            Device(f"cctv-gate-{gate}", "cctv", zone),
        ]
        if gate in ("a", "b"):
            devices.append(Device(f"frs-gate-{gate}", "face_recognition", zone))
    devices += [
        Device("anpr-gate-a", "anpr", "Gate-A", "ms"),
        Device("acs-datahall-2", "access_control", "Data-Hall-2", "local"),
        Device("cctv-datahall-2", "cctv", "Data-Hall-2"),
        Device("frs-datahall-2", "face_recognition", "Data-Hall-2"),
        Device("pids-north-01", "perimeter_intrusion", "Perimeter-North"),
        Device("pids-north-02", "perimeter_intrusion", "Perimeter-North"),
        Device("cctv-perim-north", "cctv", "Perimeter-North"),
        Device("pids-east-01", "perimeter_intrusion", "Perimeter-East"),
        Device("pids-east-02", "perimeter_intrusion", "Perimeter-East"),
        Device("cctv-perim-east", "cctv", "Perimeter-East"),
        Device("pids-south-01", "perimeter_intrusion", "Perimeter-South"),
        Device("cctv-perim-south", "cctv", "Perimeter-South"),
        Device("cctv-carpark-p2", "cctv", "Car-Park-P2"),
        Device("anpr-carpark-p2", "anpr", "Car-Park-P2", "ms"),
        Device("cctv-depot", "cctv", "Fleet-Depot"),
        Device("alarm-panel-main", "emergency_alarm", "Control-Room"),
        Device("alarm-panel-datahall", "emergency_alarm", "Data-Hall-2"),
        Device("soc-console-01", "operator_report", "Control-Room"),
        Device("soc-console-02", "operator_report", "Control-Room"),
        Device("soc-console-03", "operator_report", "Control-Room"),
        Device("fw-edge-01", "network_security", "SOC-Network", "ms"),
        Device("ids-core-01", "network_security", "SOC-Network", "ms", "network_security_log"),
        Device("vpn-gw-01", "network_security", "SOC-Network", "ms"),
        Device("dc-auth-01", "network_security", "SOC-Network", "ms"),
        Device("wx-harbour", "weather_environmental", "Harbour-Rd-INT01"),
        Device("wx-campus", "weather_environmental", "Control-Room"),
        Device("wx-expressway", "weather_environmental", "Expressway-CP-KM12"),
    ]
    for i in range(1, 13):
        devices.append(Device(f"gps-flt-{i:02d}", "gps_fleet", "Fleet-Depot"))
    return devices


DEVICES = _build_devices()
DEVICES_BY_SOURCE: dict[str, list[Device]] = {}
for _d in DEVICES:
    DEVICES_BY_SOURCE.setdefault(_d.source, []).append(_d)


def device(device_id: str, source: str) -> Device:
    for d in DEVICES_BY_SOURCE[source]:
        if d.device_id == device_id:
            return d
    raise KeyError((device_id, source))


# Devices whose own observations can go missing during a failure (Scenario D).
FAILABLE_DEVICES = [
    d
    for d in DEVICES
    if d.source in ("anpr", "radar", "cctv", "perimeter_intrusion", "face_recognition")
    and not d.device_id.startswith("cctv-gate")
]

VEHICLE_CATALOGUE = [
    ("Toyota", "Land Cruiser", "suv"),
    ("Toyota", "Camry", "sedan"),
    ("Toyota", "Hilux", "pickup"),
    ("Nissan", "Patrol", "suv"),
    ("Nissan", "Sunny", "sedan"),
    ("Lexus", "LX", "suv"),
    ("Hyundai", "Elantra", "sedan"),
    ("Kia", "Sportage", "suv"),
    ("Mitsubishi", "Pajero", "suv"),
    ("Ford", "Transit", "van"),
    ("Isuzu", "D-Max", "pickup"),
    ("Mercedes-Benz", "Actros", "truck"),
    ("Chevrolet", "Tahoe", "suv"),
    ("Honda", "Accord", "sedan"),
]
COLOURS = ["white", "white", "white", "silver", "black", "grey", "blue", "red", "beige", "gold"]
PLATE_LETTERS = "QRSTWXZ"
PLATE_REGIONS = ["ZN", "ZN", "ZN", "ZS", "ZW"]  # fictional issuing regions
FLEET_ROUTES = ["Patrol-North", "Patrol-Harbour", "Escort-Airport", "Maintenance-Loop", "Patrol-Expressway"]
WATCHLIST = [f"SUBJ-W{i:03d}" for i in range(1, 41)]
EXTERNAL_NETS = ["198.51.100.", "203.0.113."]
INTERNAL_NET = "10.20."
NETWORK_USERS = [f"emp{n}" for n in range(1001, 1061)] + ["svc-backup", "svc-camera-sync", "admin-ops"]
OPERATORS = [f"OP-{n}" for n in (104, 117, 122, 131, 138, 145)]


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def fleet_home(n: int) -> Zone:
    """Road site that patrol vehicle FLT-nn normally works."""
    return ROAD_SITES[n % len(ROAD_SITES)]
