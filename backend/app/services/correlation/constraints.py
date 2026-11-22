"""Compatibility checks (negative evidence) used before any merge."""

from __future__ import annotations

import math

from app.services.correlation.candidate_selection import Obs


def full_plate(o: Obs) -> str | None:
    """A plate is an identifier only when fully and reliably read."""
    plate = o.get("plate")
    if not plate or "?" in str(plate):
        return None
    if o.source == "anpr" and o.get("read_status") not in (None, "read"):
        return None
    return str(plate)


def distance_km(a: Obs, b: Obs) -> float | None:
    if a.lat is None or b.lat is None:
        return None
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def implied_speed_kph(a: Obs, b: Obs, allowance_km: float = 0.0) -> float | None:
    km = distance_km(a, b)
    if km is None:
        return None
    km = max(0.0, km - allowance_km)
    seconds = abs(b.ts - a.ts)
    if km == 0:
        return 0.0
    return math.inf if seconds == 0 else km / (seconds / 3600)


def vehicle_details_conflict(a: Obs, b: Obs) -> list[str]:
    """Make/colour disagreements between two readings of the same plate."""
    out = []
    for name in ("make", "colour"):
        x, y = a.get(name), b.get(name)
        if x and y and str(x).casefold() != str(y).casefold():
            out.append(f"vehicle {name}: {x} ({a.device}) vs {y} ({b.device})")
    return out


def plausible_movement(a: Obs, b: Obs, max_speed_kph: float) -> bool:
    speed = implied_speed_kph(a, b)
    return speed is not None and speed <= max_speed_kph


def low_confidence(o: Obs, threshold: float) -> bool:
    if o.confidence is not None and o.confidence < threshold:
        return True
    return o.get("read_status") in ("partial", "unreadable") or o.get("match_status") == "uncertain"
