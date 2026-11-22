"""Correlation thresholds (documented in docs/correlation.md).

Override with ``ZENITH_CORRELATION_<NAME>`` environment variables (JSON for lists/dicts).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class CorrelationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ZENITH_CORRELATION_", env_file=".env", extra="ignore")

    auto_correlate: bool = True  # run after the background quality analysis

    # --- vehicle passes (traffic enforcement) ---------------------------------------
    same_site_plate_window_seconds: float = 120.0  # same plate, same zone: same pass
    movement_window_seconds: float = 2700.0  # same plate at another site: plausible onward movement
    movement_max_speed_kph: float = 150.0
    radar_track_search_seconds: float = 3600.0  # track ids are recycled
    linked_timestamp_tolerance_seconds: float = 30.0
    linked_speed_tolerance_kph: float = 10.0
    max_pass_span_seconds: float = 3600.0
    appearance_window_seconds: float = 10.0  # CCTV appearance match around a pass

    # --- impossible travel (identity conflicts) --------------------------------------
    impossible_window_seconds: float = 600.0
    impossible_min_distance_km: float = 1.5
    impossible_speed_kph: float = 200.0

    # --- access -----------------------------------------------------------------------
    denial_min_attempts: int = 3
    denial_chain_gap_seconds: float = 300.0
    door_forced_window_seconds: float = 300.0
    access_context_window_seconds: float = 300.0  # loitering / camera after the last attempt
    face_context_window_seconds: float = 90.0
    identity_pair_window_seconds: float = 3.0
    identity_min_face_score: float = 0.8

    # --- perimeter ---------------------------------------------------------------------
    perimeter_merge_window_seconds: float = 600.0
    panel_link_window_seconds: float = 120.0
    perimeter_camera_window_seconds: float = 60.0
    perimeter_verification_window_seconds: float = 120.0

    # --- operator reports --------------------------------------------------------------
    report_window_before_seconds: float = 120.0
    report_window_after_seconds: float = 900.0

    # --- device health -----------------------------------------------------------------
    health_episode_gap_seconds: float = 21600.0
    affected_output_confidence: float = 0.55  # device output below this during degradation is "possibly affected"
    affected_output_lead_seconds: float = 0.0  # window starts at the first problem report

    # --- cyber ---------------------------------------------------------------------------
    auth_burst_min_failures: int = 10
    auth_burst_gap_seconds: float = 120.0
    auth_success_window_seconds: float = 600.0

    # --- scoring -------------------------------------------------------------------------
    low_source_confidence: float = 0.6  # detections below this are marked uncertain
    max_timeline_items: int = 2000


@lru_cache
def get_correlation_settings() -> CorrelationSettings:
    return CorrelationSettings()
