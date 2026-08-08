"""Trigger registry: thresholds, debounce, cooldown."""

from datetime import datetime, timedelta

from adaptivetouragent.fusion.snapshot import (
    ContextSnapshot,
    CrowdReading,
    UserState,
    WeatherReading,
)
from adaptivetouragent.signals.triggers.registry import (
    DEFAULT_RULES,
    TriggerRegistry,
    overridden_rules,
    short_window_rules,
)


def _snap(at: datetime, *, weather=None, crowd=None, user=None) -> ContextSnapshot:
    return ContextSnapshot(
        snapshot_id=at.isoformat(),
        at=at,
        city="Singapore",
        weather=weather,
        crowd=crowd or {},
        transit={},
        user=user or UserState(0.0, 0.0, 0, None),
    )


def _rain(at: datetime, precip: float = 4.0) -> WeatherReading:
    return WeatherReading(temp_c=26.0, precip_mm_per_h=precip, condition="rain", fetched_at=at, source="t")


def test_rain_onset_fires_under_short_window() -> None:
    reg = TriggerRegistry(rules=short_window_rules())
    at = datetime(2026, 5, 2, 11, 30)
    snap = _snap(at, weather=_rain(at))
    fired = reg.evaluate(snap)
    assert any(t.kind == "weather_rain_onset" for t in fired)


def test_rain_below_threshold_does_not_fire() -> None:
    reg = TriggerRegistry(rules=short_window_rules())
    at = datetime(2026, 5, 2, 11, 30)
    light_drizzle = WeatherReading(28, 0.5, "rain", at, "t")  # below 1.0 mm/h threshold
    fired = reg.evaluate(_snap(at, weather=light_drizzle))
    assert not any(t.kind == "weather_rain_onset" for t in fired)


def test_debounce_blocks_first_observation() -> None:
    # Default debounce is 60s for rain onset.
    reg = TriggerRegistry()  # default rules
    at0 = datetime(2026, 5, 2, 11, 30, 0)
    fired0 = reg.evaluate(_snap(at0, weather=_rain(at0)))
    assert not fired0  # debounce not yet satisfied

    at1 = at0 + timedelta(seconds=DEFAULT_RULES["weather_rain_onset"].debounce_s + 5)
    fired1 = reg.evaluate(_snap(at1, weather=_rain(at1)))
    assert any(t.kind == "weather_rain_onset" for t in fired1)


def test_cooldown_suppresses_repeats() -> None:
    rules = overridden_rules(weather_rain_onset=(0.0, 600.0))  # no debounce, 10min cooldown
    reg = TriggerRegistry(rules=rules)
    at0 = datetime(2026, 5, 2, 11, 30)

    fired0 = reg.evaluate(_snap(at0, weather=_rain(at0)))
    assert any(t.kind == "weather_rain_onset" for t in fired0)

    # 1 minute later, within cooldown.
    at1 = at0 + timedelta(minutes=1)
    fired1 = reg.evaluate(_snap(at1, weather=_rain(at1)))
    assert not any(t.kind == "weather_rain_onset" for t in fired1)

    # 11 minutes later, outside cooldown.
    at2 = at0 + timedelta(minutes=11)
    fired2 = reg.evaluate(_snap(at2, weather=_rain(at2)))
    assert any(t.kind == "weather_rain_onset" for t in fired2)


def test_crowd_spike_fires_only_for_upcoming_pois() -> None:
    reg = TriggerRegistry(rules=short_window_rules())
    at = datetime(2026, 5, 2, 12, 30)
    crowd = {
        "sg05": CrowdReading("sg05", "high", 60.0, at, "t"),
        "sg11": CrowdReading("sg11", "high", 45.0, at, "t"),
    }
    fired = reg.evaluate(_snap(at, crowd=crowd), upcoming_poi_ids=["sg05"])
    spikes = [t for t in fired if t.kind == "crowd_spike"]
    assert len(spikes) == 1
    assert spikes[0].affects == ["sg05"]


def test_noisy_trace_does_not_thrash() -> None:
    """Critical risk-mitigation test: many borderline events must not produce many replans."""
    reg = TriggerRegistry()  # default rules: 600s cooldown on rain
    at = datetime(2026, 5, 2, 11, 0)
    fired_total: list = []
    # 60 ticks every 30 seconds (30 minutes of noisy data, all rain).
    for i in range(60):
        t = at + timedelta(seconds=30 * i)
        fired_total += reg.evaluate(_snap(t, weather=_rain(t)))
    rain_fires = [f for f in fired_total if f.kind == "weather_rain_onset"]
    # In 30 minutes of noisy rain, with 60s debounce + 600s cooldown,
    # expect at most 4 fires (one per cooldown window).
    assert 0 < len(rain_fires) <= 4


def test_fatigue_high_fires_at_threshold() -> None:
    reg = TriggerRegistry(rules=short_window_rules())
    at = datetime(2026, 5, 2, 17, 0)
    user = UserState(fatigue_0_1=0.9, elapsed_min=480, pois_visited=5, last_break_min_ago=None)
    fired = reg.evaluate(_snap(at, user=user))
    assert any(t.kind == "fatigue_high" for t in fired)
