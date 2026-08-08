# Multi-city support

The demo ships five city catalogues:

| City        | Slug          | POIs | Accommodations | Map centre (lat, lon) |
|-------------|---------------|-----:|---------------:|-----------------------|
| Singapore   | `singapore`   |   15 |             10 | 1.3000, 103.8500      |
| Melbourne   | `melbourne`   |   15 |              5 | -37.8136, 144.9631    |
| London      | `london`      |   15 |              5 | 51.5074, -0.1278      |
| New York    | `new_york`    |   15 |              5 | 40.7580, -73.9855     |
| Paris       | `paris`       |   15 |              5 | 48.8566, 2.3522       |

POI catalogues live in `src/adaptivetouragent/data/cities/{slug}.json` and
follow a uniform schema (one POI per line for clean diffs). The loader is
`retrieval.poi_index.load_city(name)`; the slug derivation is
`name.lower().strip().replace(" ", "_").replace("-", "_")` (exposed as
`retrieval.poi_index._city_slug`).

## POI schema

| Field | Type | Notes |
|---|---|---|
| `poi_id` | str | Globally unique. Prefix matches city: `sg`, `mel`, `lon`, `nyc`, `par`. |
| `name` | str | Display name. |
| `category` | str | One of: `park`, `viewpoint`, `museum`, `zoo`, `theme_park`, `heritage`, `neighbourhood`, `food`, `arts`. Shared across cities so category weights generalise. |
| `lat`, `lon` | float | WGS84. Used by routing + the OSRM geometry fetcher. |
| `avg_duration_min` | float | Typical visit duration; pace knob applies a buffer on top. |
| `popularity` | float | 0..1 prior used by the retriever before LLM scoring. |
| `open_hours` | tuple[int, int] | 24h `(open, close)`. Optimiser shifts arrival forward when before open and skips when after close. |
| `kid_friendly` | bool | Hard filter when profile carries `require_kid_friendly`. |
| `indoor` | bool | Used by the rain trigger. |
| `entry_fee_usd` | float | Per-person admission; optimiser multiplies by `party_size`. |
| `wheelchair_accessible` | bool | Hard filter when profile carries `require_wheelchair`. |
| `dietary_options` | tuple[str, ...] | Subset of `{"vegetarian", "vegan", "halal", "kosher", "gluten_free"}`. |
| `sensory_low_stimulation` | bool | Hard filter when profile carries `require_low_stimulation`. |

The accommodation schema is parallel (`accommodations/types.py`); each city
has at least one budget (< $200/night) and one upscale (> $200/night)
entry, asserted in `tests/test_city_catalogues.py`.

## Per-city routing economics

`itinerary/routing.py` defines a global default fare table plus per-city
overrides keyed by the slug. The overrides are calibrated to 2024-2026
public averages and document only the values that differ from the global
defaults:

| City      | Transit $/km | Rideshare $/km | Rideshare flag-down | Cycle unlock |
|-----------|-------------:|---------------:|--------------------:|-------------:|
| Singapore |       $0.10 |          $0.95 |               $3.50 |        $1.00 |
| Melbourne |       $0.25 |          $1.20 |               $3.00 |        $1.00 |
| London    |       $0.30 |          $1.70 |               $3.00 |        $1.30 |
| New York  |       $0.30 |          $2.20 |               $3.00 |        $4.50 |
| Paris     |       $0.65 |          $1.50 |               $3.00 |        $1.50 |

CO₂e factors stay global (`CO2E_KG_PER_KM`); per-city grid factors
(cleaner UK/FR grids) are explicitly out of scope. The wheelchair routing
rules (`require_wheelchair` disables cycle, prefers transit over walks
> 1 km) apply uniformly across cities; modern transit is assumed step-free
on the trunk network of all five.

When an unknown city is passed, the fare table falls back to the global
defaults (verified by `tests/test_routing.py::test_unknown_city_falls_back_to_global_defaults`).

## Why these five

- **Singapore**: anchor city; smallest
  geographic spread of the five, exercises the optimiser's dense-grid case.
- **Melbourne**: Southern-hemisphere counterpoint; tram-heavy transit
  exercises the per-city fare table.
- **London**: most expensive transit on the list; reviewers familiar
  with the Tube will spot if numbers are off.
- **New York**: flat-fare subway averaged into a $/km value; reviewers
  familiar with NYC will recognise the rideshare premium.
- **Paris**: métro single-ticket pricing produces the highest per-km
  transit number; cross-city Disneyland Paris (47 km) exercises the long
  rideshare/drive code path.

## Adding a sixth city

Three files plus one test addition:

1. `src/adaptivetouragent/data/cities/{slug}.json`: POI catalogue.
2. `src/adaptivetouragent/data/cities/{slug}_accommodations.json`:
   accommodations.
3. (Optional) `CITY_FARE_OVERRIDES["{slug}"]` in `itinerary/routing.py`:
   per-city fare values.
4. Add the city to `SUPPORTED_CITIES` in
   `tests/test_city_catalogues.py` and to the dropdown
   `<select id="city">` in `ui/static/index.html`.
5. Add the map centre to `CITY_CENTERS` in `ui/static/app.js`.
