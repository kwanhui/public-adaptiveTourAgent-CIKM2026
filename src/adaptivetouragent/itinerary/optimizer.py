"""Constraint-aware tour optimiser.

Greedy orienteering. Selects the
highest-scoring unvisited POI that fits in the remaining time budget at each
step. Adds a fatigue penalty (POIs late in the day are downweighted) and a
partial-replan entry that takes a fixed prefix.

Tracks money budget alongside time budget: per-stop entry fees +
per-edge transit fares are subtracted from a money cap if one is set.
Per-edge CO2e is recorded on each visit; when `prefer_low_carbon`
is set, the optimiser blends scaled emissions into the picker's score.
"""

from datetime import datetime, timedelta

from adaptivetouragent.itinerary.routing import compute_leg
from adaptivetouragent.itinerary.types import POI, Itinerary, POIVisit
from adaptivetouragent.retrieval.poi_index import POIIndex


def _fatigue_penalty(elapsed_min: float, total_budget_min: float, k: float = 0.4) -> float:
    """Multiplicative penalty in [1-k, 1] applied to scores as the day wears on."""
    if total_budget_min <= 0:
        return 1.0
    progress = min(1.0, max(0.0, elapsed_min / total_budget_min))
    return 1.0 - k * progress


# Pace knob: tunes fatigue coefficient + per-stop buffer (minutes added to
# `avg_duration_min`). Relaxed = fewer but longer stops; packed = more stops.
_PACE_PARAMS: dict[str, tuple[float, float]] = {
    "relaxed": (0.50, 15.0),
    "standard": (0.40, 0.0),
    "packed": (0.25, -5.0),
}


def _pace_params(pace: str) -> tuple[float, float]:
    """Return (fatigue_k, per_stop_buffer_min) for the requested pace."""
    return _PACE_PARAMS.get(pace, _PACE_PARAMS["standard"])


def _build_visit(
    poi: POI,
    arrive: datetime,
    *,
    travel_cost_usd: float = 0.0,
    travel_co2e_kg: float = 0.0,
    travel_mode: str | None = None,
    travel_distance_km: float = 0.0,
    inbound_geometry: tuple[tuple[float, float], ...] = (),
    duration_buffer_min: float = 0.0,
    party_size: int = 1,
    reasoning_text: str = "",
    reasoning_scores: str = "",
    alternatives_considered: tuple[str, ...] = (),
    signals_influencing: tuple[str, ...] = (),
) -> POIVisit:
    effective_duration = max(15.0, poi.avg_duration_min + duration_buffer_min)
    return POIVisit(
        poi_id=poi.poi_id,
        name=poi.name,
        arrive=arrive,
        depart=arrive + timedelta(minutes=effective_duration),
        category=poi.category,
        entry_fee_usd=poi.entry_fee_usd * party_size,
        travel_cost_usd=travel_cost_usd * party_size,
        travel_co2e_kg=travel_co2e_kg * party_size,
        travel_mode=travel_mode,
        travel_distance_km=travel_distance_km,
        inbound_geometry=inbound_geometry,
        reasoning_text=reasoning_text,
        reasoning_scores=reasoning_scores,
        alternatives_considered=alternatives_considered,
        signals_influencing=signals_influencing,
    )


# Friendly labels for the human-readable rationale.
_MODE_PHRASE: dict[str, str] = {
    "walk": "short walk",
    "cycle": "bike-share ride",
    "transit": "metro/bus",
    "rideshare": "rideshare",
    "drive": "taxi",
}

_MODE_DRIVER: dict[str, str] = {
    "walk": "short hop, no fare needed",
    "cycle": "medium hop with low-carbon preference active",
    "transit": "default for longer hops when rideshare isn't budget-justified",
    "rideshare": "fastest option for this distance, budget headroom available",
    "drive": "fastest option for this distance, budget headroom available",
}


def _build_reasoning_text(
    *,
    poi: POI,
    base_score: float,
    adjusted_score: float,
    fatigue: float,
    elapsed_min: float,
    budget_minutes: float,
    runners_up: tuple[str, ...],
    runner_up_gap: float,
    travel_mode: str | None,
    travel_distance_km: float,
    step_cost_usd: float,
    money_budget_usd: float | None,
    spent_usd: float,
    prefer_low_carbon: bool,
    require_wheelchair: bool,
) -> str:
    """Compose a 2-3 sentence rationale from the signals known at pick time.

    The text uses only signals already computed by the greedy step; no extra
    LLM call. Sentences are assembled from three templates: why this POI,
    why now, why this mode.
    """
    parts: list[str] = []

    # Why this POI: relative position vs runners-up.
    if runners_up and runner_up_gap >= 0.10:
        parts.append(
            f"Top fit among remaining {poi.category} candidates: "
            f"scored {runner_up_gap:.2f} above the runner-up ({runners_up[0]})."
        )
    elif runners_up:
        parts.append(f"Strongest near-tie among remaining {poi.category} candidates (versus {runners_up[0]}).")
    else:
        parts.append(f"Highest-scoring remaining {poi.category} stop for this profile.")

    # Why now: fatigue / time progression.
    if budget_minutes > 0:
        progress = elapsed_min / budget_minutes
        if progress > 0.6 and fatigue < 0.85:
            drop = (1.0 - fatigue) * base_score
            parts.append(
                f"Late in the day, so fatigue reduced its base score by ~{drop:.2f} pts; still the best remaining slot."
            )
        elif progress < 0.2:
            parts.append("Opening slot: starts the day on a high-base-score anchor.")
        else:
            parts.append("Fits the remaining time window with margin.")

    # Why this mode.
    if travel_mode and travel_distance_km > 0:
        phrase = _MODE_PHRASE.get(travel_mode, travel_mode)
        driver_bits = []
        if require_wheelchair and travel_mode != "cycle":
            driver_bits.append("step-free routing required")
        if prefer_low_carbon and travel_mode in ("walk", "cycle", "transit"):
            driver_bits.append("low-carbon preference active")
        if travel_mode in ("rideshare", "drive") and money_budget_usd is not None:
            remaining = money_budget_usd - spent_usd
            driver_bits.append(f"budget headroom (~${remaining:.0f} left)")
        if not driver_bits:
            driver_bits.append(_MODE_DRIVER.get(travel_mode, "default mode pick"))
        parts.append(f"Reached by {phrase} ({travel_distance_km:.1f} km): " + ", ".join(driver_bits) + ".")
    else:
        parts.append("First stop of the day, no inbound leg.")

    # Cost-side note when it materially shaped the choice.
    if money_budget_usd is not None and step_cost_usd > 0:
        parts.append(f"Marginal cost ${step_cost_usd:.2f} fits within the remaining money budget.")

    return " ".join(parts)


def greedy_plan(
    *,
    index: POIIndex,
    candidates: list[POI],
    poi_scores: dict[str, float],
    start_time: datetime,
    budget_minutes: float,
    user_id: str,
    plan_id: str,
    derived_from: str | None = None,
    locked_prefix: list[POIVisit] | None = None,
    start_location: POI | None = None,
    money_budget_usd: float | None = None,
    party_size: int = 1,
    prefer_low_carbon: bool = False,
    carbon_weight: float = 0.2,
    require_wheelchair: bool = False,
    pace: str = "standard",
) -> Itinerary:
    """Greedy orienteering planner with fatigue + money budget + carbon awareness.

    `locked_prefix` lets the replanner pin the part of the day already executed.
    `start_location` anchors the first hop on a hotel / accommodation.
    `money_budget_usd` caps cumulative spend (entry fees + transit fares,
    multiplied by `party_size`); set to None to disable.
    `prefer_low_carbon` downweights high-CO2e options by `carbon_weight`.
    `require_wheelchair` filters out cycle as a mode and prefers transit
    over walking for legs > 1 km. POI-level filtering already happens at
    retrieval (see `POIIndex.filter(require_wheelchair=…)`).
    `pace` ∈ {relaxed, standard, packed} tunes the fatigue coefficient and
    a per-stop buffer in minutes; relaxed makes for fewer-but-longer stops.
    """
    fatigue_k, duration_buffer = _pace_params(pace)
    city = index.city
    visits: list[POIVisit] = list(locked_prefix or [])
    visited_ids: set[str] = {v.poi_id for v in visits}

    cursor_from_location: POI | None = None
    if visits:
        cursor_time = visits[-1].depart
        cursor_poi_id: str | None = visits[-1].poi_id
    else:
        cursor_time = start_time
        cursor_poi_id = None
        cursor_from_location = start_location

    deadline = start_time + timedelta(minutes=budget_minutes)
    total_score = sum(poi_scores.get(v.poi_id, 0.0) for v in visits)
    spent_usd = sum(v.total_cost_usd for v in visits)

    candidate_pool: list[POI] = [p for p in candidates if p.poi_id not in visited_ids]

    while candidate_pool:
        best_poi: POI | None = None
        best_score: float = -1.0
        best_arrive: datetime | None = None
        best_travel_cost: float = 0.0
        best_travel_co2e: float = 0.0
        best_travel_mode: str | None = None
        best_travel_distance: float = 0.0
        best_step_cost: float = 0.0
        # Per-step trace of considered alternatives.
        considered: list[tuple[str, str, float]] = []  # (poi_id, name, adjusted_score)

        elapsed = (cursor_time - start_time).total_seconds() / 60.0
        fatigue = _fatigue_penalty(elapsed, budget_minutes, k=fatigue_k)
        # Trip-level remaining money budget for context-aware mode selection.
        remaining_budget = money_budget_usd - spent_usd if money_budget_usd is not None else None

        for poi in candidate_pool:
            travel_min = 0.0
            travel_cost = 0.0
            travel_co2e = 0.0
            travel_mode_choice: str | None = None
            travel_distance = 0.0
            from_poi: POI | None = None
            if cursor_poi_id is not None:
                from_poi = index.pois.get(cursor_poi_id)
            elif cursor_from_location is not None:
                from_poi = cursor_from_location
            if from_poi is not None:
                leg = compute_leg(
                    from_poi,
                    poi,
                    party_size=party_size,
                    remaining_budget_usd=remaining_budget,
                    prefer_low_carbon=prefer_low_carbon,
                    require_wheelchair=require_wheelchair,
                    city=city,
                )
                travel_min = leg.duration_min
                travel_cost = leg.cost_usd
                travel_co2e = leg.co2e_kg
                travel_mode_choice = leg.mode
                travel_distance = leg.realistic_distance_km

            arrive = cursor_time + timedelta(minutes=travel_min)
            if arrive.hour < poi.open_hours[0]:
                arrive = arrive.replace(hour=poi.open_hours[0], minute=0, second=0, microsecond=0)
            effective_duration = max(15.0, poi.avg_duration_min + duration_buffer)
            depart = arrive + timedelta(minutes=effective_duration)

            if depart > deadline:
                continue
            if depart.hour >= poi.open_hours[1]:
                continue

            step_cost = (poi.entry_fee_usd + travel_cost) * party_size
            if money_budget_usd is not None and spent_usd + step_cost > money_budget_usd:
                continue

            base_score = poi_scores.get(poi.poi_id, 0.0)
            adjusted = base_score * fatigue
            if prefer_low_carbon:
                adjusted *= max(0.0, 1.0 - carbon_weight * travel_co2e)
            considered.append((poi.poi_id, poi.name, adjusted))

            if adjusted > best_score:
                best_score = adjusted
                best_poi = poi
                best_arrive = arrive
                best_travel_cost = travel_cost
                best_travel_co2e = travel_co2e
                best_travel_mode = travel_mode_choice
                best_travel_distance = travel_distance
                best_step_cost = step_cost

        if best_poi is None or best_arrive is None:
            break

        # Build per-visit reasoning. The numeric trace stays as
        # `reasoning_scores` (kept for transparency, surfaced behind an info icon
        # in the UI); the human-readable sentence lives in `reasoning_text` and
        # is rendered inline. Both compose from signals already known at this
        # greedy step, no extra LLM call.
        ranked = sorted(considered, key=lambda x: x[2], reverse=True)
        runners_up = tuple(name for pid, name, _ in ranked[1:4] if pid != best_poi.poi_id)
        runner_up_score = ranked[1][2] if len(ranked) > 1 else 0.0
        runner_up_gap = best_score - runner_up_score
        base = poi_scores.get(best_poi.poi_id, 0.0)

        # Numeric trace (machine-readable, displayed via info icon).
        score_bits = [f"score={best_score:.2f} (base={base:.2f}, fatigue={fatigue:.2f})"]
        if best_travel_mode and best_travel_distance > 0:
            score_bits.append(f"{best_travel_mode} {best_travel_distance:.1f}km")
        if prefer_low_carbon:
            score_bits.append(f"co2e={best_travel_co2e:.2f}kg")
        if best_step_cost > 0:
            score_bits.append(f"step_cost=${best_step_cost:.2f}")
        reasoning_scores = "; ".join(score_bits)

        # Human-readable rationale (displayed inline next to "Why this stop").
        reasoning_text = _build_reasoning_text(
            poi=best_poi,
            base_score=base,
            adjusted_score=best_score,
            fatigue=fatigue,
            elapsed_min=elapsed,
            budget_minutes=budget_minutes,
            runners_up=runners_up,
            runner_up_gap=runner_up_gap,
            travel_mode=best_travel_mode,
            travel_distance_km=best_travel_distance,
            step_cost_usd=best_step_cost,
            money_budget_usd=money_budget_usd,
            spent_usd=spent_usd,
            prefer_low_carbon=prefer_low_carbon,
            require_wheelchair=require_wheelchair,
        )

        # Seed `inbound_geometry` with the 2-point straight line. The
        # async post-processor (`populate_geometries`) upgrades this to an
        # OSRM-routed polyline; the seed is what survives if OSRM fails.
        if cursor_poi_id is not None:
            from_poi_obj = index.pois[cursor_poi_id]
            inbound_geom: tuple[tuple[float, float], ...] = (
                (from_poi_obj.lat, from_poi_obj.lon),
                (best_poi.lat, best_poi.lon),
            )
        elif cursor_from_location is not None:
            inbound_geom = (
                (cursor_from_location.lat, cursor_from_location.lon),
                (best_poi.lat, best_poi.lon),
            )
        else:
            inbound_geom = ()

        visit = _build_visit(
            best_poi,
            best_arrive,
            travel_cost_usd=best_travel_cost,
            travel_co2e_kg=best_travel_co2e,
            travel_mode=best_travel_mode,
            travel_distance_km=best_travel_distance,
            inbound_geometry=inbound_geom,
            duration_buffer_min=duration_buffer,
            party_size=party_size,
            reasoning_text=reasoning_text,
            reasoning_scores=reasoning_scores,
            alternatives_considered=runners_up,
        )
        visits.append(visit)
        visited_ids.add(best_poi.poi_id)
        total_score += best_score
        spent_usd += best_step_cost
        cursor_time = visit.depart
        cursor_poi_id = best_poi.poi_id
        cursor_from_location = None
        candidate_pool = [p for p in candidate_pool if p.poi_id != best_poi.poi_id]

    total_minutes = (visits[-1].depart - start_time).total_seconds() / 60.0 if visits else 0.0

    return Itinerary(
        city=index.city,
        user_id=user_id,
        visits=visits,
        total_minutes=total_minutes,
        total_score=total_score,
        plan_id=plan_id,
        derived_from=derived_from,
    )
