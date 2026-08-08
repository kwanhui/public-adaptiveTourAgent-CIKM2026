"""Recorded signal source: replay a JSONL trace deterministically."""

from datetime import datetime
from pathlib import Path

import pytest

from adaptivetouragent.signals.sources.recorded import RecordedSource

TRACE_PATH = Path(__file__).resolve().parent.parent / "demo/sample-inputs/scenarios/family-rainy-day.jsonl"


@pytest.mark.asyncio
async def test_recorded_returns_clear_then_rain() -> None:
    src = RecordedSource(TRACE_PATH)

    early = await src.fetch(datetime(2026, 5, 2, 10, 30))
    assert early.weather is not None
    assert early.weather.condition in ("clear", "cloud")

    later = await src.fetch(datetime(2026, 5, 2, 12, 0))
    assert later.weather is not None
    assert later.weather.condition == "rain"
    assert later.weather.precip_mm_per_h >= 4.0


@pytest.mark.asyncio
async def test_recorded_crowd_emerges_over_time() -> None:
    src = RecordedSource(TRACE_PATH)
    early = await src.fetch(datetime(2026, 5, 2, 9, 0))
    assert "sg05" not in early.crowd

    after_spike = await src.fetch(datetime(2026, 5, 2, 12, 0))
    assert "sg05" in after_spike.crowd
    assert after_spike.crowd["sg05"].crowd_level == "high"


def test_recorded_lists_all_event_times() -> None:
    src = RecordedSource(TRACE_PATH)
    times = src.all_event_times
    assert len(times) > 5
    assert times == sorted(times)
