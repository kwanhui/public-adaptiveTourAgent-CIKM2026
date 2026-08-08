"""Travel-time, cost, and emissions routing.

Provides:
- five named modes (walk, cycle, transit, rideshare, drive) covering the
  realistic tourist transport mix in each supported city (MRT/Tube/métro/
  subway/tram, Grab/Uber/Lyft/Bolt, Anywheel/HelloRide/Lime/Citi Bike/Vélib);
- per-city fare overrides: Singapore is the baseline, but London Tube,
  NYC subway, Paris métro, and Melbourne myki have meaningfully different
  per-km economics. `CITY_FARE_OVERRIDES` layers on top of the global
  defaults so a per-city cost number reads as realistic in each city;
- per-mode circuity factors that multiply the raw haversine distance to
  approximate actual routed distance; straight lines underestimate transit
  and bus distances by 30-50% in dense urban grids;
- per-mode wait times (calling a Grab takes ~5 min, finding a bike ~2 min);
- an accessibility-aware mode picker: when `require_wheelchair` is set,
  cycle is excluded entirely and walking is downgraded to transit beyond
  1 km (modern transit in all five supported cities is largely step-free
  on the trunk network);
- a context-aware `pick_mode` that considers distance, the remaining money
  budget, the user's sustainability preference, and accessibility needs.
  The aggregated `compute_leg` returns time, cost, CO2e, and chosen mode
  in one shot so the optimiser does not duplicate work.

The tabular factors keep the planning loop fully offline-capable. For the
visualised polylines on the map, see `itinerary/geometry.py` which calls
OSRM's public road-routing service to upgrade straight Leaflet lines to
realistic routed paths (with on-disk cache + graceful fallback).
"""

import math
from dataclasses import dataclass

from adaptivetouragent.itinerary.types import POI, TravelMode


@dataclass(frozen=True)
class RouteSegment:
    """A single edge in a route, after mode selection."""

    from_poi_id: str
    to_poi_id: str
    distance_km: float
    duration_min: float
    mode: TravelMode


@dataclass(frozen=True)
class Leg:
    """All derived quantities for one inter-POI leg."""

    raw_distance_km: float
    realistic_distance_km: float
    duration_min: float
    cost_usd: float  # per passenger
    co2e_kg: float  # per passenger
    mode: TravelMode


# ---------------------------------------------------------------------------
# Per-mode tables
# ---------------------------------------------------------------------------
# Average speeds (km/h). Walk is conservative for tourist gait; transit
# averages MRT door-to-door including platform time on a typical leg.
SPEEDS: dict[TravelMode, float] = {
    "walk": 4.5,
    "cycle": 14.0,
    "transit": 22.0,
    "rideshare": 28.0,
    "drive": 35.0,
}

# Per-km fares (USD, per passenger). Singapore-anchored:
# - walk: free
# - cycle: bike-share blended (Anywheel / HelloRide ~$0.10/min, ~14 km/h)
# - transit: MRT distance fare averages ~$0.10/km
# - rideshare: Grab averages ~$0.95/km after surge blending
# - drive: average taxi rate
FARES_PER_KM_USD: dict[TravelMode, float] = {
    "walk": 0.0,
    "cycle": 0.10,
    "transit": 0.10,
    "rideshare": 0.95,
    "drive": 1.20,
}

# Flag-down / unlock fees (USD, per ride, NOT per passenger for transit).
# Transit and walk have none. Cycle is the bike-share unlock; rideshare is
# the booking + minimum fare.
FLAG_DOWN_USD: dict[TravelMode, float] = {
    "walk": 0.0,
    "cycle": 1.00,
    "transit": 0.0,
    "rideshare": 3.50,
    "drive": 4.00,
}

# Operational CO2e (kg) per passenger-km. Sources: Banerjee et al. 2025
# (ITT, Best Journal Paper), IPCC AR6 transport chapter, and national
# average grid factors. Walk and cycle are zero by convention.
CO2E_KG_PER_KM: dict[TravelMode, float] = {
    "walk": 0.0,
    "cycle": 0.0,
    "transit": 0.041,
    "rideshare": 0.171,
    "drive": 0.171,
}

# Circuity factor: actual routed distance is straight-line × factor.
# Walking and cycling follow paths but mostly direct; transit detours via
# rail lines and transfers; rideshare follows the road grid.
CIRCUITY_FACTOR: dict[TravelMode, float] = {
    "walk": 1.20,
    "cycle": 1.25,
    "transit": 1.45,
    "rideshare": 1.30,
    "drive": 1.30,
}

# Per-mode wait time (minutes) added to in-vehicle travel.
WAIT_TIME_MIN: dict[TravelMode, float] = {
    "walk": 0.0,
    "cycle": 2.0,
    "transit": 6.0,
    "rideshare": 5.0,
    "drive": 0.0,
}

# Selection thresholds (km).
WALK_ONLY_KM = 0.5  # always walk this short, no point in any vehicle
WALK_PREFERRED_KM = 1.5  # prefer walking under sustainability flag
WALK_WHEELCHAIR_KM = 1.0  # beyond this, wheelchair users prefer transit
CYCLE_RANGE_KM = 4.0  # cycling is comfortable up to here
RIDESHARE_RANGE = (1.0, 8.0)  # rideshare candidate window


# ---------------------------------------------------------------------------
# Per-city economics
# ---------------------------------------------------------------------------
# Sparse overrides on top of the global defaults above. Each city sets only
# the values that differ. Calibrated to 2024-2026 public averages:
#   - Singapore: MRT distance fare ~$0.10/km, Grab ~$0.95/km + $3.50 booking
#   - Melbourne: myki tram/bus zone-flat averaged to ~$0.25/km, Uber ~$1.20/km
#   - London:    Tube zonal ~$0.30/km, Uber ~$1.70/km, Lime cycle £1 unlock
#   - New York:  subway flat $2.90 ≈ $0.30/km across typical trips,
#                Uber/Lyft ~$2.20/km, Citi Bike single-ride $4.50
#   - Paris:     Métro single ticket €2.15 / ~3 km avg ≈ $0.65/km blended,
#                Uber ~$1.50/km, Vélib day-pass amortised to $1.50 unlock
# Per-city CO2e grid factors are deliberately out of scope (kept global);
# revisit when a reviewer asks about the cleaner UK/FR grids.
CITY_FARE_OVERRIDES: dict[str, dict[str, dict[TravelMode, float]]] = {
    "singapore": {
        "fares_per_km": {"transit": 0.10, "rideshare": 0.95, "drive": 1.20, "cycle": 0.10},
        "flag_down": {"transit": 0.0, "rideshare": 3.50, "drive": 4.00, "cycle": 1.00},
    },
    "melbourne": {
        "fares_per_km": {"transit": 0.25, "rideshare": 1.20, "drive": 1.40, "cycle": 0.12},
        "flag_down": {"transit": 0.0, "rideshare": 3.00, "drive": 4.00, "cycle": 1.00},
    },
    "london": {
        "fares_per_km": {"transit": 0.30, "rideshare": 1.70, "drive": 2.00, "cycle": 0.15},
        "flag_down": {"transit": 0.0, "rideshare": 3.00, "drive": 4.50, "cycle": 1.30},
    },
    "new_york": {
        "fares_per_km": {"transit": 0.30, "rideshare": 2.20, "drive": 2.40, "cycle": 0.20},
        "flag_down": {"transit": 0.0, "rideshare": 3.00, "drive": 4.50, "cycle": 4.50},
    },
    "paris": {
        "fares_per_km": {"transit": 0.65, "rideshare": 1.50, "drive": 1.80, "cycle": 0.10},
        "flag_down": {"transit": 0.0, "rideshare": 3.00, "drive": 4.00, "cycle": 1.50},
    },
}


def _city_slug(city: str | None) -> str | None:
    """Lower-case, underscore-separated key matching the data file names."""
    if city is None:
        return None
    return city.lower().strip().replace(" ", "_").replace("-", "_")


def _fare_table(city: str | None, key: str, mode: TravelMode) -> float:
    """Resolve a per-mode fare value, picking city override when available."""
    defaults = FARES_PER_KM_USD if key == "fares_per_km" else FLAG_DOWN_USD
    slug = _city_slug(city)
    if slug and slug in CITY_FARE_OVERRIDES:
        overrides = CITY_FARE_OVERRIDES[slug].get(key, {})
        if mode in overrides:
            return overrides[mode]
    return defaults.get(mode, 0.0)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    earth_radius_km = 6371.0
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return earth_radius_km * c


# ---------------------------------------------------------------------------
# Mode selection
# ---------------------------------------------------------------------------
def _rideshare_total_cost(distance_km: float, party_size: int, city: str | None = None) -> float:
    """Estimated rideshare cost for a leg, per booking (party shares the ride)."""
    realistic = distance_km * CIRCUITY_FACTOR["rideshare"]
    per_km = _fare_table(city, "fares_per_km", "rideshare")
    flag = _fare_table(city, "flag_down", "rideshare")
    return realistic * per_km + flag


def pick_mode(
    distance_km: float,
    *,
    party_size: int = 1,
    remaining_budget_usd: float | None = None,
    prefer_low_carbon: bool = False,
    require_wheelchair: bool = False,
    city: str | None = None,
) -> TravelMode:
    """Pick the best mode for this leg given budget + sustainability + accessibility.

    Order of precedence:
      1. Very short hops (< 0.5 km) → walk, always.
      2. Wheelchair-required traveller → no cycle, prefer transit beyond 1 km.
         Rideshare/drive remain available for medium hops with budget headroom.
      3. Sustainability-focused traveller → walk if short, cycle if medium,
         transit otherwise.
      4. Comfortable money-budget headroom → rideshare for medium hops.
      5. Default → public transit (universal fallback).

    `remaining_budget_usd` is the *trip-level* money budget left, not the
    per-leg budget; the picker does not pick rideshare unless the booking
    would leave at least 3× its own cost remaining (so one rideshare doesn't
    starve the rest of the day).
    """
    if distance_km < WALK_ONLY_KM:
        return "walk"

    lo, hi = RIDESHARE_RANGE
    rideshare_eligible = lo <= distance_km <= hi and remaining_budget_usd is not None

    if require_wheelchair:
        if distance_km < WALK_WHEELCHAIR_KM:
            return "walk"
        # Try rideshare first if budget allows (accessible-vehicle request
        # is implicit at booking); otherwise fall back to step-free transit.
        if rideshare_eligible:
            rs_cost = _rideshare_total_cost(distance_km, party_size, city)
            if (remaining_budget_usd or 0.0) > rs_cost * 3:
                return "rideshare"
        return "transit"

    if prefer_low_carbon:
        if distance_km < WALK_PREFERRED_KM:
            return "walk"
        if distance_km <= CYCLE_RANGE_KM:
            return "cycle"
        return "transit"

    if rideshare_eligible:
        rs_cost = _rideshare_total_cost(distance_km, party_size, city)
        if (remaining_budget_usd or 0.0) > rs_cost * 3:
            return "rideshare"

    return "transit"


# ---------------------------------------------------------------------------
# Aggregated leg calculation
# ---------------------------------------------------------------------------
def compute_leg(
    from_poi: POI,
    to_poi: POI,
    *,
    mode: TravelMode | None = None,
    party_size: int = 1,
    remaining_budget_usd: float | None = None,
    prefer_low_carbon: bool = False,
    require_wheelchair: bool = False,
    city: str | None = None,
) -> Leg:
    """Return time, cost, CO2e, and chosen mode for a single inter-POI leg.

    When `mode` is None, picks via `pick_mode`. `cost_usd` is per passenger
    (the optimiser multiplies by `party_size` separately so single-bill
    items like rideshare and per-passenger items like transit fares are
    handled consistently at one layer). `city` selects the per-city fare
    override table; defaults to the global table when None.
    """
    raw_distance = haversine_km(from_poi.lat, from_poi.lon, to_poi.lat, to_poi.lon)
    chosen: TravelMode = (
        mode
        if mode is not None
        else pick_mode(
            raw_distance,
            party_size=party_size,
            remaining_budget_usd=remaining_budget_usd,
            prefer_low_carbon=prefer_low_carbon,
            require_wheelchair=require_wheelchair,
            city=city,
        )
    )

    circuity = CIRCUITY_FACTOR.get(chosen, 1.30)
    realistic = raw_distance * circuity

    speed = SPEEDS.get(chosen, SPEEDS["walk"])
    duration = (realistic / speed) * 60.0 + WAIT_TIME_MIN.get(chosen, 0.0)

    fare = realistic * _fare_table(city, "fares_per_km", chosen) + _fare_table(city, "flag_down", chosen)
    # Rideshare and drive are charged per booking (party shares the ride);
    # walk, cycle, transit are per-passenger. Express both as per-passenger so
    # the optimiser can multiply by party_size uniformly.
    if chosen in ("rideshare", "drive") and party_size > 0:
        fare = fare / party_size

    co2e = realistic * CO2E_KG_PER_KM.get(chosen, 0.0)

    return Leg(
        raw_distance_km=raw_distance,
        realistic_distance_km=realistic,
        duration_min=duration,
        cost_usd=fare,
        co2e_kg=co2e,
        mode=chosen,
    )


# ---------------------------------------------------------------------------
# Backward-compatible scalar wrappers (existing call sites)
# ---------------------------------------------------------------------------
def travel_time_min(
    from_poi: POI,
    to_poi: POI,
    mode: TravelMode | None = None,
    city: str | None = None,
) -> float:
    """Estimated travel time in minutes between two POIs."""
    return compute_leg(from_poi, to_poi, mode=mode, city=city).duration_min


def travel_cost_usd(
    from_poi: POI,
    to_poi: POI,
    mode: TravelMode | None = None,
    city: str | None = None,
) -> float:
    """Estimated travel cost (per passenger)."""
    return compute_leg(from_poi, to_poi, mode=mode, city=city).cost_usd


def travel_co2e_kg(
    from_poi: POI,
    to_poi: POI,
    mode: TravelMode | None = None,
    city: str | None = None,
) -> float:
    """Estimated operational CO2e (kg per passenger)."""
    return compute_leg(from_poi, to_poi, mode=mode, city=city).co2e_kg


def build_cost_matrix(
    pois: list[POI],
    mode: TravelMode | None = None,
    city: str | None = None,
) -> dict[tuple[str, str], float]:
    """All-pairs travel-time matrix for a list of POIs."""
    matrix: dict[tuple[str, str], float] = {}
    for a in pois:
        for b in pois:
            if a.poi_id == b.poi_id:
                continue
            matrix[(a.poi_id, b.poi_id)] = travel_time_min(a, b, mode, city=city)
    return matrix
