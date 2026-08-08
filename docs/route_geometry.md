# Realistic route geometry

The Leaflet map previously drew straight haversine lines between each pair
of consecutive visits. Visually this read as "as the crow flies": accurate
for the optimiser's distance math but misleading for how a tourist actually
moves through a city. This note describes how `itinerary/geometry.py`
upgrades the map visualisation to road-snapped polylines while keeping the
planning loop fully offline-capable.

## Pipeline

```
greedy_plan (sync)
  └─► POIVisit.inbound_geometry = ((from.lat, from.lon), (to.lat, to.lon))
                                   2-point straight-line seed

plan_initial (async, post-process)
  └─► populate_geometries(visits, city_slug, pois, start_location)
        ├─► load on-disk cache: data/route_cache/{city_slug}.json
        ├─► for each leg:
        │     ├─► cache hit  → replace seed with cached polyline
        │     └─► cache miss → mark for fetch (seed stays as fallback)
        ├─► if ATAU_DISABLE_OSRM=1 (tests/CI) → return
        ├─► bounded-concurrency OSRM fetches (semaphore=4)
        ├─► on success → write back to cache + replace seed
        └─► persist cache to disk
```

## Why this shape

- **Two-stage** (sync greedy seed → async post-process upgrade) lets the
  optimiser stay synchronous. The geometry call would otherwise block the
  hot inner loop on network I/O for every candidate considered.
- **Straight-line seed in `greedy_plan`** means the UI always has something
  to draw. If OSRM is down, rate-limited, or disabled, the visit cards still
  show a sensible (if blocky) inbound geometry.
- **On-disk cache** (`src/adaptivetouragent/data/route_cache/{slug}.json`)
  is committed to the repo. After the first plan in each city the cache
  populates incrementally, and subsequent plans are zero-network.

## OSRM profile mapping

OSRM's public demo router supports `foot`, `bike`, and `driving`. The five
named travel modes map as:

| Travel mode | OSRM profile | Visual style              |
|-------------|--------------|---------------------------|
| `walk`      | `foot`       | solid green               |
| `cycle`     | `bike`       | solid light green         |
| `transit`   | `driving`    | **dashed** blue           |
| `rideshare` | `driving`    | solid amber               |
| `drive`     | `driving`    | solid red                 |

Transit uses the `driving` profile because OSRM has no public-transit
routing on its demo server. The polyline therefore follows the road grid,
which is wrong in detail (a metro line doesn't follow roads) but visually
correct in shape (transit corridors mostly parallel arterial roads).
The **dashed** style is a deliberate visual cue that this leg is approximate.

## Failure semantics

Every step in the pipeline has a fallback:

| Failure | Fallback |
|---------|----------|
| OSRM HTTP error / timeout (4s) | Straight-line seed retained |
| OSRM returns non-`Ok` body | Straight-line seed retained |
| Cache file corrupt / unparseable | Empty cache + fetch as if cold |
| `ATAU_DISABLE_OSRM=1` set | All fetches skipped, straight lines used |
| Network entirely unreachable | Same as OSRM error: straight lines |

Tests set `ATAU_DISABLE_OSRM=1` via `conftest.py` so CI never depends on
OSRM uptime. The fallback path is exercised by
`tests/test_geometry.py::test_populate_geometries_falls_back_to_straight_line`.

## Caching considerations

- **Key**: `f"{from_id}|{to_id}|{mode}"`. The accommodation start gets the
  prefix `start:{accommodation_id}`.
- **Symmetry**: NOT exploited. `(a→b, walk)` and `(b→a, walk)` are stored
  separately. OSRM responses can differ slightly in either direction (one-way
  streets, bridge approach selection), so this is correct.
- **Size**: an OSRM `full` overview returns ~100-500 coordinate pairs per
  leg in dense cities. The Singapore cache is ~15 KB after 3 entries; a
  fully-populated 15-POI city cache across 3 modes is bounded by
  15·14·3 = 630 entries ≈ 3 MB.
- **Reproducibility**: the cache is committed to the repo so a reviewer
  cloning fresh and running `make demo` offline gets the same routes as
  someone running with network access (after the first run that populates).

## Where it appears in the paper

This module supports two specific paper claims:

1. **The demo is offline-capable**. Even with OSRM unreachable, the system
   produces a complete plan with a coherent visualisation.
2. **The cost/CO₂e numbers and the visualised path agree**. The optimiser
   uses haversine × circuity for time/cost; the visualisation uses OSRM
   road-snap. Both reduce to a routed path that respects the road grid;
   the per-mode circuity factor (1.20-1.45×) was calibrated against OSRM
   foot/bike/driving totals so the planner and the visualisation tell the
   same story.

## Limitations

- OSRM driving routes for "transit" legs are wrong in the trunk-network
  sense (metro lines, not roads). For the demo paper this is a known
  approximation; a future iteration could plug in a public-transit
  routing service (Citymapper/Google Maps API, both paid).
- The OSRM public demo is "for testing only". A production deployment
  should host its own OSRM instance or use a paid routing provider. The
  cache committed in this repo plus the graceful fallback means the live
  HF Space demo is functional even when the public OSRM is rate-limiting.
