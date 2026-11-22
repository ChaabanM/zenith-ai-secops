"""Scores used by the correlation engine. None of these are calibrated probabilities.

Three different quantities are kept apart:

* **source detection confidence**: the sensor's own ``confidence`` on an event
  (stored on the event, never modified here);
* **linkage confidence**: how strong the evidence is that an event belongs to the
  incident, fixed per link type (table below) and reduced when the link carries a
  conflict;
* **incident confidence**: a heuristic score combining the linkage strength of the
  core evidence, the detection confidence of that evidence, independent
  corroboration, and conflicts (see ``incident_confidence``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.correlation.candidate_selection import Obs

SEVERITY_ORDER = ["informational", "low", "medium", "high", "critical"]
SEVERITY_RANK = {s: i + 1 for i, s in enumerate(SEVERITY_ORDER)}

# link type -> (base linkage confidence, is a strong/core link, human description)
LINK_TYPES: dict[str, tuple[float, bool, str]] = {
    "anchor": (1.0, True, "the event that defines the incident"),
    "explicit_reference": (0.95, True, "one record explicitly references the other"),
    "same_affected_device": (0.95, True, "health reports about the same affected device in one continuous episode"),
    "same_plate_same_site": (0.9, True, "the same fully read plate at the same site within seconds"),
    "same_credential": (0.9, True, "the same access credential in a continuous sequence of attempts"),
    "same_source_and_account": (0.9, True, "the same source address against the same account in one burst"),
    "same_door": (0.8, True, "an alarm on the same door shortly after the attempts"),
    "plausible_movement": (0.75, True, "the same fully read plate at another site, consistent with driving there"),
    "same_identifier_conflict": (0.8, True, "the same identifier observed in incompatible places or by incompatible identities"),
    "unambiguous_pairing": (0.7, True, "the only badge use and the only face capture at the same door within seconds, provisionally "
                                       "paired by time and place (not verified to be the same person)"),
    "contextual": (0.55, False, "same zone and a compatible event type in a short window, with no other candidate incident"),
    "appearance_match": (0.55, False, "camera detection at the same site within seconds with matching vehicle appearance"),
    "affected_output": (0.5, False, "low-confidence output of the failing device during its degradation"),
}

CONFLICT_FACTOR = 0.7


def link_confidence(link_type: str, conflict: bool = False) -> float:
    base = LINK_TYPES[link_type][0]
    return round(base * (CONFLICT_FACTOR if conflict else 1.0), 2)


@dataclass
class Member:
    obs: Obs
    role: str
    link_type: str
    explanation: str
    conflict: str | None = None
    core: bool = True
    linkage_confidence: float = field(default=0.0)

    def __post_init__(self) -> None:
        if not self.linkage_confidence:
            self.linkage_confidence = link_confidence(self.link_type, self.conflict is not None)
        self.core = LINK_TYPES[self.link_type][1] and self.core


def incident_confidence(members: list[Member], conflicts: int, low_source_threshold: float) -> tuple[float, str, str]:
    """score = 0.5 * mean core linkage + 0.4 * mean core detection confidence
             + corroboration bonus (0.05 per additional independent source, max 0.10)
             - 0.10 per conflict (max 0.30) - 0.10 for a single uncorroborated event."""
    core = [m for m in members if m.core]
    linkage = sum(m.linkage_confidence for m in core) / len(core)
    detections = [m.obs.confidence if m.obs.confidence is not None else 0.85 for m in core]
    detection = sum(detections) / len(detections)
    sources = {m.obs.source for m in members}
    bonus = min(0.10, 0.05 * (len(sources) - 1))
    single = 0.10 if len(members) == 1 else 0.0
    penalty = min(0.30, 0.10 * conflicts)
    score = max(0.05, min(0.99, 0.5 * linkage + 0.4 * detection + bonus - penalty - single))
    level = "high" if score >= 0.75 else "medium" if score >= 0.5 else "low"
    low = sum(1 for m in core if m.obs.confidence is not None and m.obs.confidence < low_source_threshold)
    reason = (
        f"Heuristic score (not a probability): core linkage strength {linkage:.2f}, mean detection confidence of core evidence "
        f"{detection:.2f} (sensors that report none count as 0.85), {len(sources)} independent source type(s)"
        + (f", {conflicts} conflict(s) (-{penalty:.2f})" if conflicts else "")
        + (", single uncorroborated event (-0.10)" if single else "")
        + (f"; {low} core detection(s) below {low_source_threshold:.2f} confidence" if low else "")
        + "."
    )
    return round(score, 2), level, reason


def max_severity(members: list[Member]) -> tuple[str, Member | None]:
    best, who = "informational", None
    for m in members:
        if not m.core or m.obs.severity is None:
            continue
        if SEVERITY_RANK[m.obs.severity] > SEVERITY_RANK[best]:
            best, who = m.obs.severity, m
    return best, who


def cap(severity: str, ceiling: str) -> str:
    return severity if SEVERITY_RANK[severity] <= SEVERITY_RANK[ceiling] else ceiling


def raise_to(severity: str, floor: str) -> str:
    return severity if SEVERITY_RANK[severity] >= SEVERITY_RANK[floor] else floor
