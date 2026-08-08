"""ContextSnapshot: single ablation surface for the demo paper.

This is a frozen contract.

The snapshot is the structured object the agent reasons over. Any field can
be zeroed out (set to None or empty dict) to disable that signal; this is
the ablation knob the paper uses for the multi-signal fusion claims.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

WeatherCondition = Literal["clear", "cloud", "rain", "storm", "snow"]
CrowdLevel = Literal["low", "med", "high", "closed"]


@dataclass(frozen=True)
class WeatherReading:
    """Current weather at the city level."""

    temp_c: float
    precip_mm_per_h: float
    condition: WeatherCondition
    fetched_at: datetime
    source: str  # "openmeteo" | "recorded" | "synthetic"


@dataclass(frozen=True)
class CrowdReading:
    """Crowd / queue snapshot for a single POI."""

    poi_id: str
    crowd_level: CrowdLevel
    queue_minutes: float | None
    fetched_at: datetime
    source: str


@dataclass(frozen=True)
class TransitReading:
    """Travel time + disruption flag for a directed edge."""

    from_poi: str
    to_poi: str
    mode: Literal["walk", "cycle", "transit", "rideshare", "drive"]
    duration_min: float
    disruption: bool
    fetched_at: datetime
    source: str


@dataclass(frozen=True)
class UserState:
    """Current state of the tourist (or group)."""

    fatigue_0_1: float  # 0 = fresh, 1 = exhausted
    elapsed_min: float
    pois_visited: int
    last_break_min_ago: float | None
    explicit_pref_changes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ContextSnapshot:
    """The single object the agent reasons over.

    Authored by `fusion.fuser`, consumed by `replanner.replan`. Any field can
    be zeroed out to disable that signal, used for ablations in the paper.
    """

    snapshot_id: str
    at: datetime
    city: str
    weather: WeatherReading | None
    crowd: dict[str, CrowdReading]  # by poi_id, only for upcoming POIs
    transit: dict[tuple[str, str], TransitReading]
    user: UserState
    sources_used: list[str] = field(default_factory=list)

    def has_weather(self) -> bool:
        return self.weather is not None

    def has_crowd(self) -> bool:
        return bool(self.crowd)

    def has_transit(self) -> bool:
        return bool(self.transit)
