"""Briefing settings (``ZENITH_BRIEFING_*``)."""

from functools import lru_cache

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class ShiftDefinition(BaseModel):
    name: str
    label: str
    start: str  # "HH:MM" local time; a shift ends where the next one starts


class BriefingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ZENITH_BRIEFING_", env_file=".env", extra="ignore")

    timezone: str = "UTC"  # display / shift time zone, e.g. Asia/Dubai; storage is always UTC
    shifts: list[ShiftDefinition] = [
        ShiftDefinition(name="day", label="Day shift", start="06:00"),
        ShiftDefinition(name="evening", label="Evening shift", start="14:00"),
        ShiftDefinition(name="night", label="Night shift", start="22:00"),
    ]
    max_period_days: int = 31
    top_n: int = 10  # items per list section
    sample_events: int = 5  # evidence events listed per finding
    baseline_windows: int = 3  # comparison windows (same time on previous days for periods up to 24 h)
    significance_z: float = 3.0  # Poisson z-score for a significant increase
    significance_min_count: int = 5
    drop_ratio: float = 3.0  # a drop to <= baseline / drop_ratio is significant (baseline >= min count)


@lru_cache
def get_briefing_settings() -> BriefingSettings:
    return BriefingSettings()
