# Preference surface

The UI form carries six user-facing knobs that shape what the planner
returns. Each maps onto a specific layer of the pipeline. This note is
the authoritative reference for which knob acts where.

## Knob table

| UI knob | Wire field | Layer where it acts | Effect |
|---|---|---|---|
| Profile preset (Solo/Couple/Friends/Family) | `profile.category_weights`, `family_size`, `require_kid_friendly`, `notes` | Retrieval + LLM scoring + greedy_plan | Category bias, party-size multiplier on fees, hard kid-friendly filter |
| Money cap | `money_budget_usd` | `greedy_plan` | Hard cap on cumulative entry_fee + transit_fare (× party). Optimiser refuses any step that would exceed it. |
| Prefer low-carbon routes | `prefer_low_carbon` | `pick_mode` + score blend | Walks > cycles > transit; CO₂e blended into score with weight 0.2 |
| **Require accessible route** | `profile.require_wheelchair` | `POIIndex.filter` + `pick_mode` | (1) Drops POIs flagged `wheelchair_accessible=false`. (2) Never picks cycle; prefers transit over walks > 1 km. |
| **Quiet / sensory-friendly mode** | `profile.require_low_stimulation` | `POIIndex.filter` | Drops POIs flagged `sensory_low_stimulation=false`. |
| **Pace** | `pace` ∈ {relaxed, standard, packed} | `greedy_plan` | Tunes fatigue coefficient + per-stop buffer (see below). |

## Accessibility surface (two layers)

Accessibility flags compose with each other and with the rest of the
profile. The internal type is `AccessibilityRequirements`
(`agent/types.py`):

```python
@dataclass(frozen=True)
class AccessibilityRequirements:
    require_wheelchair: bool = False
    dietary: tuple[str, ...] = ()
    require_low_stimulation: bool = False
```

Two filters apply:

1. **POI-level**: `POIIndex.filter(require_wheelchair=…,
   require_dietary=…, require_low_stimulation=…)` drops POIs whose
   catalogue fields disagree. This runs once at retrieval.
2. **Routing-level**: `pick_mode(require_wheelchair=…)` excludes `cycle`
   as a candidate mode (bike-share is not wheelchair-compatible) and
   prefers `transit` over `walk` for legs longer than `WALK_WHEELCHAIR_KM`
   = 1 km. `rideshare`/`drive` remain available; the demo assumes the
   user requests an accessible vehicle implicitly at booking time.

The wheelchair POI-level filter is empirically asserted in
`tests/test_initial_plan.py::test_wheelchair_filter_keeps_only_accessible_pois`
(POIs with `wheelchair_accessible=false`, e.g. Singapore's `sg08` Little
India, never appear in produced plans).

The routing-level filter is asserted by
`tests/test_routing.py::test_wheelchair_never_picks_cycle` and
`test_wheelchair_prefers_transit_over_long_walk`.

## Pace mechanism

Three discrete settings, each binding two values:

| Pace | Fatigue `k` | Per-stop buffer (min) | Effect |
|---|---:|---:|---|
| relaxed | 0.50 | +15 | Fewer stops; each one longer; stronger end-of-day penalty |
| standard | 0.40 | 0 | Default; matches the behaviour before the pace selector existed |
| packed | 0.25 | -5 | More stops; shorter per stop; weaker fatigue penalty |

The fatigue `k` controls the linear score decay across the day:

```
adjusted_score = base_score x (1 - k x elapsed_min / budget_minutes)
```

So at the end of a relaxed day a POI is worth 50% of its base score; at
the end of a packed day, 75%. The buffer is added to each POI's
`avg_duration_min` before the depart time is computed.

The pace mechanism is verified by
`tests/test_initial_plan.py::test_pace_packed_fits_more_stops_than_relaxed`
on a Singapore 12-hour window:

```
pace=relaxed   visits=5  total_min=680
pace=standard  visits=6  total_min=680
pace=packed    visits=6  total_min=650
```

## Why these picks (and not others)

The expanded preference surface exposes several knobs that each map to a
distinct pipeline layer.

Considered but **not** added, to keep the surface focused:

- **Dietary requirements multi-select**: the catalogue already carries
  `dietary_options`, and the filter exists. The chip-style multi-select UI
  is deferred.
- **Live crowd-aware initial scoring**: would reuse the synthetic crowd
  source at plan time. Defers because the trigger-driven mid-trip flow
  already exercises the same source.
- **Per-stop time budget cap**: small and useful, but kept out to avoid
  surface bloat.

## How to add a new toggle

For a binary accessibility-style flag:

1. Add to `ProfileIn` in `ui/schemas.py`.
2. Plumb through `_profile_from_in` in `ui/server.py` into
   `AccessibilityRequirements`.
3. Add a filter line in `POIIndex.filter` (if it gates POI selection) or
   in `pick_mode` (if it gates mode selection).
4. Add a checkbox to `ui/static/index.html` and collect it in
   `generatePlan()` in `ui/static/app.js`.

For a discrete preference (like pace):

1. Add to `PlanRequest` in `ui/schemas.py` (next to `prefer_low_carbon`).
2. Add parameter to `plan_initial`, `plan_multi_day`, `greedy_plan`.
3. Implement the effect inside the optimiser; expose a `_param(...)`
   helper if multiple values bind to the setting.
4. Add a `<select>` to the UI form.
