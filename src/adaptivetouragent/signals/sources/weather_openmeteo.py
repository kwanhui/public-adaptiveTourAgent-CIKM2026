"""Open-Meteo weather adapter (no API key required).

Used as the one real signal in M4. In tests this is exercised through `respx`
cassettes (see tests/test_weather_openmeteo.py) to keep CI offline-clean.
"""

import logging
from datetime import datetime

import httpx

from adaptivetouragent.fusion.snapshot import WeatherCondition, WeatherReading
from adaptivetouragent.signals.sources.base import SignalBatch

logger = logging.getLogger(__name__)

OPENMETEO_BASE = "https://api.open-meteo.com/v1/forecast"


def _wmo_to_condition(wmo_code: int, precip: float) -> WeatherCondition:
    """Open-Meteo WMO weather codes -> our 5-way condition."""
    if wmo_code in (95, 96, 99):
        return "storm"
    if wmo_code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if wmo_code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82) or precip > 0.1:
        return "rain"
    if wmo_code in (1, 2, 3, 45, 48):
        return "cloud"
    return "clear"


class OpenMeteoSource:
    """Pulls current weather for a fixed lat/lon."""

    name = "openmeteo"

    def __init__(self, lat: float, lon: float, *, timeout_s: float = 5.0):
        self.lat = lat
        self.lon = lon
        self._client = httpx.AsyncClient(timeout=timeout_s)
        self._cache: tuple[datetime, WeatherReading] | None = None
        self._cache_ttl_s = 60.0

    async def fetch(self, at: datetime) -> SignalBatch:
        if self._cache is not None:
            cached_at, cached = self._cache
            if (at - cached_at).total_seconds() < self._cache_ttl_s:
                return SignalBatch(at=at, weather=cached)

        params: dict[str, str | float] = {
            "latitude": self.lat,
            "longitude": self.lon,
            "current": "temperature_2m,precipitation,weather_code",
        }
        try:
            resp = await self._client.get(OPENMETEO_BASE, params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            logger.warning("openmeteo: fetch failed (%s); returning empty batch", e)
            return SignalBatch(at=at)

        current = data.get("current", {})
        temp = float(current.get("temperature_2m", 28.0))
        precip = float(current.get("precipitation", 0.0))
        code = int(current.get("weather_code", 0))

        reading = WeatherReading(
            temp_c=temp,
            precip_mm_per_h=precip,
            condition=_wmo_to_condition(code, precip),
            fetched_at=at,
            source=self.name,
        )
        self._cache = (at, reading)
        return SignalBatch(at=at, weather=reading)

    async def close(self) -> None:
        await self._client.aclose()
