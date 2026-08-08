"""Geometry pipeline: cache read, straight-line fallback, OSRM-disable env."""

import json
import os
from datetime import datetime
from pathlib import Path

from adaptivetouragent.itinerary.geometry import (
    _cache_key,
    osrm_disabled,
    populate_geometries,
)
from adaptivetouragent.itinerary.types import POI, POIVisit


def _poi(pid: str, lat: float, lon: float) -> POI:
    return POI(
        poi_id=pid,
        name=pid,
        category="x",
        lat=lat,
        lon=lon,
        avg_duration_min=30.0,
        popularity=0.5,
    )


def _visit(pid: str, mode: str | None) -> POIVisit:
    return POIVisit(
        poi_id=pid,
        name=pid,
        arrive=datetime(2026, 6, 1, 9, 0),
        depart=datetime(2026, 6, 1, 9, 30),
        category="x",
        travel_mode=mode,
    )


def test_osrm_disabled_env() -> None:
    """Conftest sets ATAU_DISABLE_OSRM=1; confirm the helper reads it."""
    assert osrm_disabled() is True


async def test_populate_geometries_uses_cached_entry(tmp_path, monkeypatch) -> None:
    """A pre-existing cache entry should be returned without any network call."""
    # Stand up an ephemeral cache file at the path geometry.py will read.
    cache_dir = Path(__file__).resolve().parent.parent / "src" / "adaptivetouragent" / "data" / "route_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "test_city_cache.json"
    fake_geom = [[1.30, 103.85], [1.305, 103.855], [1.31, 103.86]]
    cache_file.write_text(json.dumps({_cache_key("a", "b", "transit"): fake_geom}))

    pois = {"a": _poi("a", 1.30, 103.85), "b": _poi("b", 1.31, 103.86)}
    visits = [_visit("a", None), _visit("b", "transit")]
    try:
        await populate_geometries(visits, city_slug="test_city_cache", pois=pois)
        # Second visit picks up the 3-point cached polyline.
        assert len(visits[1].inbound_geometry) == 3
        assert visits[1].inbound_geometry[1] == (1.305, 103.855)
    finally:
        cache_file.unlink(missing_ok=True)


async def test_populate_geometries_falls_back_to_straight_line() -> None:
    """With OSRM disabled and no cache, every inbound leg gets a 2-point line."""
    # Use a city slug that has no cache file.
    pois = {"a": _poi("a", 1.30, 103.85), "b": _poi("b", 1.31, 103.86)}
    visits = [_visit("a", None), _visit("b", "transit")]
    assert os.environ.get("ATAU_DISABLE_OSRM") == "1"
    await populate_geometries(visits, city_slug="nonexistent_test_city", pois=pois)
    assert visits[0].inbound_geometry == ()
    assert visits[1].inbound_geometry == ((1.30, 103.85), (1.31, 103.86))
