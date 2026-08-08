"""Canonical plan and POI types. These are a frozen contract: changing them
ripples through the optimiser, the replanner, the UI schemas, and the logs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from adaptivetouragent.accommodations.types import Accommodation


@dataclass(frozen=True)
class POI:
    """A point of interest in the city knowledge base."""

    poi_id: str
    name: str
    category: str
    lat: float
    lon: float
    avg_duration_min: float
    popularity: float  # 0..1
    open_hours: tuple[int, int] = (9, 21)  # (open_hour, close_hour) 24h
    kid_friendly: bool = True
    indoor: bool = False  # used by weather-driven replanning
    entry_fee_usd: float = 0.0  # per-person admission; 0 for free POIs
    wheelchair_accessible: bool = True
    dietary_options: tuple[str, ...] = ()  # "vegetarian", "halal", "vegan", "gluten-free"
    sensory_low_stimulation: bool = False  # quiet, low-crowd-noise spaces


@dataclass
class POIVisit:
    """A scheduled visit to a POI within an itinerary."""

    poi_id: str
    name: str
    arrive: datetime
    depart: datetime
    category: str
    entry_fee_usd: float = 0.0
    travel_cost_usd: float = 0.0  # transit fare from previous stop
    travel_co2e_kg: float = 0.0  # carbon emissions of the inbound leg
    travel_mode: str | None = None  # mode picked for the inbound leg
    travel_distance_km: float = 0.0  # circuity-adjusted distance of the inbound leg
    inbound_geometry: tuple[tuple[float, float], ...] = ()  # (lat, lon) polyline for the inbound leg
    reasoning_text: str = ""  # human-readable rationale shown inline
    reasoning_scores: str = ""  # numeric trace surfaced behind an info icon
    alternatives_considered: tuple[str, ...] = ()
    signals_influencing: tuple[str, ...] = ()  # which trigger kinds drove this pick

    @property
    def duration_min(self) -> float:
        return (self.depart - self.arrive).total_seconds() / 60.0

    @property
    def total_cost_usd(self) -> float:
        return self.entry_fee_usd + self.travel_cost_usd


@dataclass
class Itinerary:
    """A planned tour. The canonical output of `replanner.initial` and `replanner.replan`."""

    city: str
    user_id: str
    visits: list[POIVisit]
    total_minutes: float
    total_score: float
    plan_id: str
    derived_from: str | None = None  # plan_id of the plan this replans (None for initial)
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def total_cost_usd(self) -> float:
        return sum(v.total_cost_usd for v in self.visits)

    @property
    def total_co2e_kg(self) -> float:
        return sum(v.travel_co2e_kg for v in self.visits)


class DiffOp(str, Enum):
    """Per-POI operation in a plan diff."""

    PRESERVE = "preserve"
    DROP = "drop"
    INSERT = "insert"
    REORDER = "reorder"


@dataclass
class PlanDiffEntry:
    """Single change in a plan diff."""

    op: DiffOp
    poi_id: str
    name: str
    reason: str = ""


@dataclass
class PlanDiff:
    """Difference between two itineraries: what was kept, dropped, inserted, reordered."""

    entries: list[PlanDiffEntry]
    summary: str

    @property
    def n_dropped(self) -> int:
        return sum(1 for e in self.entries if e.op == DiffOp.DROP)

    @property
    def n_inserted(self) -> int:
        return sum(1 for e in self.entries if e.op == DiffOp.INSERT)


TravelMode = Literal["walk", "cycle", "transit", "rideshare", "drive"]


@dataclass
class DayItinerary:
    """One day of a multi-day tour, anchored on an accommodation."""

    day_index: int  # 0-based day of the trip
    date: date
    accommodation: Accommodation | None
    start_time: datetime
    end_time: datetime
    visits: list[POIVisit]
    total_minutes: float
    total_score: float
    plan_id: str

    @property
    def n_visits(self) -> int:
        return len(self.visits)


@dataclass
class MultiDayItinerary:
    """A multi-day tour with optional accommodation anchor across all days."""

    city: str
    user_id: str
    accommodation: Accommodation | None
    days: list[DayItinerary]
    start_datetime: datetime
    end_datetime: datetime
    total_score: float
    plan_id: str
    derived_from: str | None = None
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def n_visits(self) -> int:
        return sum(d.n_visits for d in self.days)

    @property
    def n_days(self) -> int:
        return len(self.days)
