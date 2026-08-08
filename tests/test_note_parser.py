"""Note-to-signal parser tests.

Verifies that free-text trigger notes and chat messages are translated into
typed weather / transit signals + fatigue offset, so the rest of the fusion
pipeline can react. Anything the parser misses still drives a `user_request`
trigger via `pref_changes`; these tests cover only the keyword-mapped path.
"""

from datetime import datetime

import pytest

from adaptivetouragent.signals.note_parser import (
    FATIGUE_BOOST_PER_INJECTION,
    interpret_note,
    note_to_signals,
)


def _at() -> datetime:
    return datetime(2026, 5, 16, 12, 0)


@pytest.mark.parametrize(
    "note,expected_substring",
    [
        ("it started pouring", "rain"),
        ("it's way too hot to walk", "heat"),
        ("the kids are hungry, find dinner", "food"),
        ("can we do something cheaper", "spend"),
        ("skip the next museum", "dropping"),
        ("MRT delay reported", "transit"),
        ("we're exhausted", "pace"),
    ],
)
def test_interpret_note_reflects_intent(note: str, expected_substring: str) -> None:
    assert expected_substring in interpret_note(note).lower()


def test_interpret_note_never_silent_on_nonempty() -> None:
    # Even an unrecognised note gets a non-empty, honest interpretation.
    assert interpret_note("the vibe is off today") != ""
    assert interpret_note("   ") == ""


def test_rain_keyword_yields_rain_weather() -> None:
    batch, fatigue = note_to_signals(note="It started raining heavily", at=_at(), upcoming_poi_ids=["a", "b"])
    assert batch.weather is not None
    assert batch.weather.condition == "rain"
    assert batch.weather.precip_mm_per_h > 0
    assert batch.weather.source == "manual"
    assert fatigue == 0.0


def test_storm_keyword_yields_storm_weather() -> None:
    batch, _ = note_to_signals(note="Big thunderstorm rolling in", at=_at(), upcoming_poi_ids=["a", "b"])
    assert batch.weather is not None
    assert batch.weather.condition == "storm"


def test_tired_keyword_yields_fatigue_boost() -> None:
    batch, fatigue = note_to_signals(note="The kids are tired", at=_at(), upcoming_poi_ids=["a", "b"])
    assert fatigue == FATIGUE_BOOST_PER_INJECTION
    assert batch.weather is None
    assert not batch.transit


def test_delay_keyword_yields_transit_disruption_on_next_edge() -> None:
    batch, _ = note_to_signals(note="MRT delay reported", at=_at(), upcoming_poi_ids=["a", "b", "c"])
    assert ("a", "b") in batch.transit
    reading = batch.transit[("a", "b")]
    assert reading.disruption
    assert reading.source == "manual"


def test_delay_skipped_when_no_upcoming_edge() -> None:
    batch, _ = note_to_signals(note="MRT delay reported", at=_at(), upcoming_poi_ids=[])
    assert not batch.transit
    batch_single, _ = note_to_signals(note="MRT delay reported", at=_at(), upcoming_poi_ids=["a"])
    assert not batch_single.transit


def test_unmatched_note_is_empty() -> None:
    batch, fatigue = note_to_signals(
        note="skip the next museum, find dinner instead",
        at=_at(),
        upcoming_poi_ids=["a", "b"],
    )
    assert batch.weather is None
    assert not batch.transit
    assert fatigue == 0.0


def test_empty_note_is_no_op() -> None:
    batch, fatigue = note_to_signals(note="   ", at=_at(), upcoming_poi_ids=["a", "b"])
    assert batch.weather is None
    assert not batch.transit
    assert fatigue == 0.0


@pytest.mark.parametrize(
    "phrase",
    ["pouring outside", "downpour", "drizzle starting", "wet weather"],
)
def test_rain_variants(phrase: str) -> None:
    batch, _ = note_to_signals(note=phrase, at=_at(), upcoming_poi_ids=["a", "b"])
    assert batch.weather is not None
    assert batch.weather.condition == "rain"
