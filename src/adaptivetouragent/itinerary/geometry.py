"""Realistic route geometry via OSRM, with an on-disk cache.

The Leaflet map in the UI previously drew straight lines between POIs, which
read as "as the crow flies": fine for haversine distance, misleading for
how a tourist actually moves through a city. This module upgrades each leg's
visual to a routed polyline (snapped to roads/paths) by calling OSRM's
public demo router, then caching the response on disk so the second plan
for the same city pair never re-fetches.

Design choices:

- **Per-mode OSRM profile**. OSRM's public demo supports `foot` (walk),
  `bike` (cycle), and `driving` (rideshare, drive). It has no public-transit
  routing, so `transit` falls back to `driving`, close enough to give the
  metro line a road-following look. The optimiser still uses the transit
  fare table for cost/time; only the visual approximates.
- **Cache key**. `(from_id, to_id, mode)`. Stored as JSON under
  `data/route_cache/{city_slug}.json`. The cache is committed to the repo
  so the demo runs without external network access after the first
  population. Missing entries trigger one OSRM call each (rate-limited via
  a semaphore) and write back on success.
- **Failure mode**. Any network error / non-2xx / timeout falls back to a
  2-point straight line. The UI is unaffected; it always renders whatever
  polyline it gets. This keeps the demo robust when OSRM's free tier is
  down or rate-limits us mid-presentation.
- **Async**. `populate_geometries` runs all per-leg fetches concurrently
  with a small semaphore (default 4) so a 7-stop plan adds ~1s on cold
  cache and zero on warm.
- **Disable for tests**. Set env `ATAU_DISABLE_OSRM=1` to skip the network
  entirely. The conftest sets this so CI never depends on OSRM uptime.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx

from adaptivetouragent.itinerary.types import POI, POIVisit, TravelMode

# OSRM profile per travel mode. Transit falls back to driving (no public
# transit on the demo); rideshare and drive both use driving.
_PROFILE: dict[TravelMode, str] = {
    "walk": "foot",
    "cycle": "bike",
    "transit": "driving",
    "rideshare": "driving",
    "drive": "driving",
}

_OSRM_BASE = "https://router.project-osrm.org/route/v1"
_TIMEOUT_S = 4.0
_CONCURRENCY = 4


def _cache_path(city_slug: str) -> Path:
    """Where this city's route cache lives. Parent dir always exists."""
    return Path(__file__).resolve().parent.parent / "data" / "route_cache" / f"{city_slug}.json"


def _load_cache(city_slug: str) -> dict[str, list[list[float]]]:
    path = _cache_path(city_slug)
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data: dict[str, list[list[float]]] = json.load(f)
            return data
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(city_slug: str, cache: dict[str, list[list[float]]]) -> None:
    path = _cache_path(city_slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cache, f, separators=(",", ":"))
    tmp.replace(path)


def _cache_key(from_id: str, to_id: str, mode: TravelMode | str) -> str:
    return f"{from_id}|{to_id}|{mode}"


async def _fetch_osrm(
    client: httpx.AsyncClient,
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
    mode: TravelMode | str,
) -> list[list[float]] | None:
    """One OSRM call. Returns [[lat, lon], ...] on success, None on any failure."""
    profile = _PROFILE.get(mode, "driving")  # type: ignore[arg-type]
    url = f"{_OSRM_BASE}/{profile}/{from_lon},{from_lat};{to_lon},{to_lat}"
    params = {"overview": "full", "geometries": "geojson"}
    try:
        resp = await client.get(url, params=params, timeout=_TIMEOUT_S)
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    if body.get("code") != "Ok" or not body.get("routes"):
        return None
    coords = body["routes"][0]["geometry"]["coordinates"]
    # GeoJSON is [lon, lat]; convert to [lat, lon] for Leaflet.
    return [[lat, lon] for lon, lat in coords]


def _straight_line(from_lat: float, from_lon: float, to_lat: float, to_lon: float) -> list[list[float]]:
    """Fallback when OSRM is unreachable or disabled."""
    return [[from_lat, from_lon], [to_lat, to_lon]]


def osrm_disabled() -> bool:
    """Tests + offline runs can opt out entirely via env."""
    return os.environ.get("ATAU_DISABLE_OSRM", "0") == "1"


async def populate_geometries(
    visits: list[POIVisit],
    *,
    city_slug: str,
    pois: dict[str, POI],
    start_location: POI | None = None,
) -> None:
    """Fill `inbound_geometry` on each visit with a routed polyline.

    Mutates `visits` in place. The first visit's inbound leg comes from
    `start_location` (typically an accommodation) when provided; otherwise
    it has no inbound geometry. Subsequent visits use the previous visit's
    POI as origin.

    Cache-first: any `(from_id, to_id, mode)` triple already in the on-disk
    cache is used directly. Misses fetch from OSRM (with concurrency limit)
    and write back. On any failure, falls back to a 2-point straight line;
    the UI handles both uniformly.
    """
    if not visits:
        return

    cache = _load_cache(city_slug)
    misses: list[tuple[int, str, float, float, float, float, TravelMode | str]] = []

    # Pass 1: cache lookup + straight-line fallback.
    for i, v in enumerate(visits):
        if i == 0:
            if start_location is None or v.travel_mode is None:
                continue
            from_id = f"start:{start_location.poi_id}"
            from_lat, from_lon = start_location.lat, start_location.lon
        else:
            prev_id = visits[i - 1].poi_id
            prev_poi = pois.get(prev_id)
            if prev_poi is None or v.travel_mode is None:
                continue
            from_id = prev_id
            from_lat, from_lon = prev_poi.lat, prev_poi.lon
        to_poi = pois.get(v.poi_id)
        if to_poi is None:
            continue
        to_lat, to_lon = to_poi.lat, to_poi.lon
        key = _cache_key(from_id, v.poi_id, v.travel_mode)
        cached = cache.get(key)
        if cached:
            v.inbound_geometry = tuple((lat, lon) for lat, lon in cached)
            continue
        # Mark for network fetch. Fall back to straight line for now so the
        # UI has something to draw if the fetch fails or is disabled.
        v.inbound_geometry = tuple([(from_lat, from_lon), (to_lat, to_lon)])
        misses.append((i, key, from_lat, from_lon, to_lat, to_lon, v.travel_mode))

    if not misses or osrm_disabled():
        return

    # Pass 2: bounded-concurrency OSRM fetches.
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def fetch_one(item: tuple[int, str, float, float, float, float, TravelMode | str]) -> None:
        idx, key, f_lat, f_lon, t_lat, t_lon, mode = item
        async with sem:
            try:
                async with httpx.AsyncClient() as client:
                    geom = await _fetch_osrm(client, f_lat, f_lon, t_lat, t_lon, mode)
            except (httpx.HTTPError, RuntimeError):
                geom = None
        if geom is not None:
            cache[key] = geom
            visits[idx].inbound_geometry = tuple((lat, lon) for lat, lon in geom)

    await asyncio.gather(*(fetch_one(m) for m in misses))

    # Persist the cache only when something new landed in it.
    if misses:
        _save_cache(city_slug, cache)
