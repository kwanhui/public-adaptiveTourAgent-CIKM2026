"""SignalSource Protocol and shared types.

A signal source produces zero or more Readings whenever the loop driver polls
it. Readings are typed unions over the four kinds the demo handles (weather,
crowd, transit, user). Sources are async to keep the loop responsive when one
backend is slow.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from adaptivetouragent.fusion.snapshot import (
    CrowdReading,
    TransitReading,
    UserState,
    WeatherReading,
)

Reading = WeatherReading | CrowdReading | TransitReading | UserState


@dataclass
class SignalBatch:
    """A snapshot of fresh readings returned by a single poll of a source."""

    at: datetime
    weather: WeatherReading | None = None
    crowd: dict[str, CrowdReading] = field(default_factory=dict)
    transit: dict[tuple[str, str], TransitReading] = field(default_factory=dict)
    user: UserState | None = None


class SignalSource(Protocol):
    """Pull-style signal source. Returns whatever is current at `at`.

    Live sources may ignore `at` and use the wall clock; recorded sources use
    `at` to pick the right point in the trace.
    """

    name: str

    async def fetch(self, at: datetime) -> SignalBatch: ...

    async def close(self) -> None: ...
