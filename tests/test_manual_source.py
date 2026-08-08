"""ManualSignalSource queue + fatigue boost tests."""

from datetime import datetime

import pytest

from adaptivetouragent.fusion.snapshot import TransitReading, WeatherReading
from adaptivetouragent.signals.sources.base import SignalBatch
from adaptivetouragent.signals.sources.manual import ManualSignalSource


@pytest.mark.asyncio
async def test_fetch_returns_empty_when_nothing_injected() -> None:
    src = ManualSignalSource()
    batch = await src.fetch(datetime(2026, 5, 16, 12, 0))
    assert batch.weather is None
    assert not batch.crowd
    assert not batch.transit


@pytest.mark.asyncio
async def test_fetch_drains_queue_and_merges() -> None:
    src = ManualSignalSource()
    at = datetime(2026, 5, 16, 12, 0)
    rain = WeatherReading(temp_c=25.0, precip_mm_per_h=4.0, condition="rain", fetched_at=at, source="manual")
    disruption = TransitReading(
        from_poi="a",
        to_poi="b",
        mode="transit",
        duration_min=45.0,
        disruption=True,
        fetched_at=at,
        source="manual",
    )
    src.inject(SignalBatch(at=at, weather=rain))
    src.inject(SignalBatch(at=at, transit={("a", "b"): disruption}))

    merged = await src.fetch(at)
    assert merged.weather is not None and merged.weather.condition == "rain"
    assert ("a", "b") in merged.transit

    # Second fetch sees nothing; queue was drained.
    second = await src.fetch(at)
    assert second.weather is None
    assert not second.transit


def test_fatigue_boost_accumulates_and_pops_once() -> None:
    src = ManualSignalSource()
    src.add_fatigue(0.2)
    src.add_fatigue(0.3)
    assert src.pop_fatigue_boost() == pytest.approx(0.5)
    # Pop is destructive; next read is 0.
    assert src.pop_fatigue_boost() == 0.0


def test_fatigue_boost_clamps_to_unit_interval() -> None:
    src = ManualSignalSource()
    src.add_fatigue(1.5)
    assert src.pop_fatigue_boost() == 1.0
    src.add_fatigue(-0.3)
    assert src.pop_fatigue_boost() == 0.0
