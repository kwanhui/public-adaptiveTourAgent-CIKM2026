"""Fusion tests: merge SignalBatches, apply ablations."""

from datetime import datetime

from adaptivetouragent.fusion.fuser import fuse
from adaptivetouragent.fusion.snapshot import (
    CrowdReading,
    UserState,
    WeatherReading,
)
from adaptivetouragent.fusion.user_state import estimate_user_state
from adaptivetouragent.itinerary.types import POIVisit
from adaptivetouragent.signals.sources.base import SignalBatch


def _user() -> UserState:
    return UserState(fatigue_0_1=0.0, elapsed_min=0.0, pois_visited=0, last_break_min_ago=None)


def test_fuse_overrides_with_later_batch() -> None:
    at = datetime(2026, 5, 2, 12, 0)
    early = SignalBatch(at=at, weather=WeatherReading(28, 0.0, "clear", at, "src1"))
    late = SignalBatch(at=at, weather=WeatherReading(25, 5.0, "rain", at, "src2"))
    snap = fuse([early, late], user=_user(), city="Singapore", at=at)
    assert snap.weather is not None and snap.weather.condition == "rain"
    assert "src2" in snap.sources_used


def test_fuse_disable_zeroes_signal() -> None:
    at = datetime(2026, 5, 2, 12, 0)
    batch = SignalBatch(
        at=at,
        weather=WeatherReading(25, 5.0, "rain", at, "src1"),
        crowd={"sg05": CrowdReading("sg05", "high", 50.0, at, "src1")},
    )
    snap = fuse([batch], user=_user(), city="Singapore", at=at, disable=("crowd",))
    assert snap.weather is not None
    assert snap.crowd == {}


def test_fuse_records_unique_sources() -> None:
    at = datetime(2026, 5, 2, 12, 0)
    a = SignalBatch(at=at, weather=WeatherReading(28, 0, "clear", at, "openmeteo"))
    b = SignalBatch(at=at, crowd={"x": CrowdReading("x", "low", 5.0, at, "synthetic-crowd")})
    c = SignalBatch(at=at, crowd={"y": CrowdReading("y", "med", 15.0, at, "synthetic-crowd")})
    snap = fuse([a, b, c], user=_user(), city="Singapore", at=at)
    assert "openmeteo" in snap.sources_used
    assert "synthetic-crowd" in snap.sources_used
    # No duplicates.
    assert len(snap.sources_used) == len(set(snap.sources_used))


def test_user_state_grows_with_visits() -> None:
    start = datetime(2026, 5, 2, 9, 0)
    later = datetime(2026, 5, 2, 14, 0)
    visit = POIVisit("sg01", "X", datetime(2026, 5, 2, 10, 0), datetime(2026, 5, 2, 11, 0), "park")
    state = estimate_user_state(
        start_time=start, now=later, executed_visits=[visit] * 3, family_size=2
    )
    # 5h elapsed * 0.06/h = 0.30, plus 3 visits * 0.04 = 0.12, scaled by 1.05 = ~0.44
    assert 0.3 < state.fatigue_0_1 < 0.7
    assert state.pois_visited == 3


def test_user_state_break_reduces_fatigue() -> None:
    start = datetime(2026, 5, 2, 9, 0)
    later = datetime(2026, 5, 2, 17, 0)
    visit = POIVisit("sg01", "X", datetime(2026, 5, 2, 10, 0), datetime(2026, 5, 2, 11, 0), "park")
    no_break = estimate_user_state(start_time=start, now=later, executed_visits=[visit] * 4)
    with_break = estimate_user_state(
        start_time=start, now=later, executed_visits=[visit] * 4, last_break_min_ago=10.0
    )
    assert with_break.fatigue_0_1 < no_break.fatigue_0_1
