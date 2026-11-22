"""Quality-engine thresholds (documented in docs/data-quality.md).

Every value can be overridden with an environment variable ``ZENITH_QUALITY_<NAME>``;
dictionaries and lists are given as JSON, e.g.
``ZENITH_QUALITY_LOW_CONFIDENCE_THRESHOLDS='{"anpr": 0.7, "default": 0.5}'``.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class QualitySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ZENITH_QUALITY_", env_file=".env", extra="ignore")

    # --- workflow -----------------------------------------------------------------------
    auto_analyze: bool = Field(True, description="Re-run the analysis in the background after every ingest request")

    # --- C. delayed events ------------------------------------------------------------------
    delay_threshold_seconds: dict[str, float] = Field(
        default_factory=lambda: {"default": 300.0, "operator_report": 1800.0, "weather_environmental": 600.0, "device_health": 600.0},
        description="received_at - occurred_at above which an event is 'delayed', per source (key 'default' for others)",
    )
    delay_medium_seconds: float = 3600.0  # delays above this are medium severity

    # --- D. out-of-order arrival --------------------------------------------------------
    out_of_order_tolerance_seconds: dict[str, float] = Field(
        default_factory=lambda: {"default": 20.0, "weather_environmental": 60.0, "operator_report": 300.0},
        description="Inversions up to this are normal transmission jitter (manual reports are typed with variable lag)",
    )

    # --- E. conflicting timestamps ------------------------------------------------------
    clock_skew_tolerance_seconds: float = 60.0  # received_at may precede occurred_at by this much
    linked_timestamp_tolerance_seconds: float = 30.0  # radar event vs the violation that references its track
    linked_track_search_seconds: float = 3600.0  # track ids are recycled; only look this far for the referenced track
    linked_speed_tolerance_kph: float = 10.0

    # --- F. conflicting locations -------------------------------------------------------
    location_min_distance_km: float = 1.0  # below this, differences are treated as positioning error
    gps_error_allowance_km: float = 0.5  # extra allowance when either observation is GPS
    max_plausible_speed_kph: float = 200.0

    # --- G. conflicting sensor results --------------------------------------------------
    vehicle_detail_window_seconds: float = 3600.0  # same plate, different make/colour within this window
    identity_pair_window_seconds: float = 3.0  # badge use and face match provisionally paired (identity not verified)
    identity_min_face_score: float = 0.8  # weaker matches are 'uncertain', not conflicts
    unresolved_identity_window_seconds: float = 60.0
    perimeter_verification_window_seconds: float = 120.0

    # --- H. repeated device events -------------------------------------------------------
    repeat_window_seconds: float = 900.0
    repeat_min_count: int = 10
    telemetry_event_types: list[str] = Field(
        default_factory=lambda: [
            "heartbeat", "position_update", "weather_reading", "plate_read", "access_granted",
            "auth_success", "face_match", "vehicle_detected", "crowd_count", "shift_note",
        ],
        description="Expected routine traffic, never reported as suspicious repetition",
    )

    # --- I. event spikes ----------------------------------------------------------------
    spike_windows_seconds: list[float] = Field(default_factory=lambda: [300.0, 1800.0])
    spike_min_count: int = 8  # never report fewer events than this (one occurrence seen by several sensors is not a spike)
    spike_ratio: float = 3.0  # ... nor less than ratio x expected
    spike_alpha: float = 0.01  # family-wise false-alarm budget: Poisson tail < alpha / number of windows tested
    spike_diurnal_factor: float = 1.5  # daytime rates exceed the 24-hour average
    spike_max_evidence: int = 300

    # --- J. device health -----------------------------------------------------------------
    health_temperature_warning_c: float = 75.0
    health_packet_loss_warning_pct: float = 5.0

    # --- K. low confidence ------------------------------------------------------------------
    low_confidence_thresholds: dict[str, float] = Field(
        default_factory=lambda: {
            "default": 0.5,
            "face_recognition": 0.6,
            "anpr": 0.6,
            "cctv": 0.5,
            "perimeter_intrusion": 0.4,
        }
    )

    # --- L. insufficient linkage evidence -----------------------------------------------
    report_corroboration_window_seconds: float = 900.0


@lru_cache
def get_quality_settings() -> QualitySettings:
    return QualitySettings()
