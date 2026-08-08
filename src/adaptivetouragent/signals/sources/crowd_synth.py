"""Synthesised crowd readings: no reliable free crowd API.

Crowd level is a function of (popularity, time-of-day) with a deterministic
sinusoidal peak around lunchtime + late afternoon. Documented as synthetic
in the source field so reviewers know what is and is not real.
"""

import math
from datetime import datetime

from adaptivetouragent.fusion.snapshot import CrowdLevel, CrowdReading
from adaptivetouragent.itinerary.types import POI
from adaptivetouragent.signals.sources.base import SignalBatch


def _crowd_level(popularity: float, hour: float) -> tuple[CrowdLevel, float | None]:
    # Two daily peaks at 12:00 and 16:00.
    peak1 = math.exp(-((hour - 12.0) ** 2) / 4.0)
    peak2 = math.exp(-((hour - 16.0) ** 2) / 6.0)
    intensity = popularity * max(peak1, peak2)

    if intensity < 0.25:
        return "low", 5.0
    if intensity < 0.55:
        return "med", 15.0
    if intensity < 0.85:
        return "high", 35.0
    return "high", 60.0


class SyntheticCrowdSource:
    """Returns a deterministic crowd reading per POI based on hour-of-day."""

    name = "synthetic-crowd"

    def __init__(self, pois: list[POI]):
        self._pois = {p.poi_id: p for p in pois}

    async def fetch(self, at: datetime) -> SignalBatch:
        hour = at.hour + at.minute / 60.0
        readings: dict[str, CrowdReading] = {}
        for pid, poi in self._pois.items():
            level, queue = _crowd_level(poi.popularity, hour)
            readings[pid] = CrowdReading(
                poi_id=pid,
                crowd_level=level,
                queue_minutes=queue,
                fetched_at=at,
                source=self.name,
            )
        return SignalBatch(at=at, crowd=readings)

    async def close(self) -> None:
        return None
