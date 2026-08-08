"""Synthesised transit readings.

Returns the routing-table baseline duration for each requested edge with no
disruption, using the same distance-aware mode selection as
`itinerary.routing`. A real backend would replace this without changing
the consumers.
"""

from datetime import datetime

from adaptivetouragent.fusion.snapshot import TransitReading
from adaptivetouragent.itinerary.routing import haversine_km, pick_mode, travel_time_min
from adaptivetouragent.itinerary.types import POI
from adaptivetouragent.signals.sources.base import SignalBatch


class SyntheticTransitSource:
    """Returns mode-aware travel-time estimates for each edge, with no disruption."""

    name = "synthetic-transit"

    def __init__(self, pois: list[POI]):
        self._pois = {p.poi_id: p for p in pois}

    async def fetch(self, at: datetime) -> SignalBatch:
        return SignalBatch(at=at)

    def lookup(self, from_id: str, to_id: str) -> TransitReading | None:
        a = self._pois.get(from_id)
        b = self._pois.get(to_id)
        if a is None or b is None:
            return None
        chosen_mode = pick_mode(haversine_km(a.lat, a.lon, b.lat, b.lon))
        return TransitReading(
            from_poi=from_id,
            to_poi=to_id,
            mode=chosen_mode,
            duration_min=travel_time_min(a, b),
            disruption=False,
            fetched_at=datetime.now(),
            source=self.name,
        )

    async def close(self) -> None:
        return None
