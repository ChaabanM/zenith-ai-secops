"""Registry of quality rules, in execution order (see docs/data-quality.md)."""

from app.services.quality.rules.base import QualityContext, Rule
from app.services.quality.rules.entities import LocationConflictRule, VehicleDetailConflictRule
from app.services.quality.rules.evidence import LowConfidenceRule, MissingIdentifierRule, MissingLocationRule, UncorroboratedReportRule
from app.services.quality.rules.health import DeviceHealthRule
from app.services.quality.rules.identity import IdentityConflictRule, PerimeterVerificationRule
from app.services.quality.rules.records import DuplicateSubmissionRule, InvalidRecordRule
from app.services.quality.rules.timing import DelayedEventRule, LinkedRadarRule, OutOfOrderRule, ReceivedBeforeOccurredRule
from app.services.quality.rules.volume import EventSpikeRule, RepeatedDeviceEventsRule

ALL_RULES: tuple[type[Rule], ...] = (
    DuplicateSubmissionRule,  # A
    InvalidRecordRule,  # B
    DelayedEventRule,  # C
    OutOfOrderRule,  # D
    ReceivedBeforeOccurredRule,  # E
    LinkedRadarRule,  # E / G (traffic measurement)
    LocationConflictRule,  # F
    VehicleDetailConflictRule,  # G
    IdentityConflictRule,  # G / L (unresolved identity)
    PerimeterVerificationRule,  # G
    RepeatedDeviceEventsRule,  # H
    EventSpikeRule,  # I
    DeviceHealthRule,  # J
    LowConfidenceRule,  # K
    MissingIdentifierRule,  # L
    MissingLocationRule,  # L
    UncorroboratedReportRule,  # L
)

__all__ = ["ALL_RULES", "QualityContext", "Rule"]
