"""Manual itinerary edits.

Distinct from the agentic replanner: when a traveller clicks "remove" on a
stop they want it gone, not swapped for another POI. `remove_and_reroute`
drops the stop and reschedules the remaining stops in their existing order
around the gap: same leg/dwell logic as the optimiser, but no candidate
selection and no LLM call, so nothing new is ever added.
"""

import uuid
from datetime import datetime, timedelta

from adaptivetouragent.itinerary.geometry import populate_geometries
from adaptivetouragent.itinerary.optimizer import _build_visit, _pace_params
from adaptivetouragent.itinerary.routing import compute_leg
from adaptivetouragent.itinerary.types import Itinerary, POIVisit
from adaptivetouragent.retrieval.poi_index import POIIndex, _city_slug


async def remove_and_reroute(
    *,
    plan: Itinerary,
    index: POIIndex,
    removed_poi_id: str,
    at: datetime,
    start_time: datetime,
    budget_minutes: float,
    money_budget_usd: float | None = None,
    party_size: int = 1,
    prefer_low_carbon: bool = False,
    require_wheelchair: bool = False,
    pace: str = "standard",
) -> Itinerary:
    """Drop `removed_poi_id` and reschedule the rest in their existing order.

    Stops that already departed before `at` keep their recorded times; the
    tail is re-timed sequentially from the prefix end. Travel legs, dwell, and
    costs are recomputed exactly as the optimiser would, but the POI set is
    fixed; removal only frees time, so every remaining stop still fits.
    """
    _, duration_buffer = _pace_params(pace)
    city = index.city

    kept = [v for v in plan.visits if v.poi_id != removed_poi_id]
    prefix = [v for v in kept if v.depart <= at]
    tail = [v for v in kept if v.depart > at]
    original = {v.poi_id: v for v in plan.visits}

    visits: list[POIVisit] = list(prefix)
    if visits:
        cursor_time = visits[-1].depart
        cursor_poi_id: str | None = visits[-1].poi_id
    else:
        cursor_time = start_time
        cursor_poi_id = None
    spent = sum(v.total_cost_usd for v in visits)

    for v in tail:
        poi = index.pois.get(v.poi_id)
        if poi is None:
            continue
        travel_min = travel_cost = travel_co2e = travel_distance = 0.0
        travel_mode: str | None = None
        inbound_geom: tuple[tuple[float, float], ...] = ()
        from_poi = index.pois.get(cursor_poi_id) if cursor_poi_id is not None else None
        if from_poi is not None:
            remaining_budget = (money_budget_usd - spent) if money_budget_usd is not None else None
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
            travel_mode = leg.mode
            travel_distance = leg.realistic_distance_km
            inbound_geom = ((from_poi.lat, from_poi.lon), (poi.lat, poi.lon))

        arrive = cursor_time + timedelta(minutes=travel_min)
        if arrive.hour < poi.open_hours[0]:
            arrive = arrive.replace(hour=poi.open_hours[0], minute=0, second=0, microsecond=0)

        ov = original.get(v.poi_id)
        nv = _build_visit(
            poi,
            arrive,
            travel_cost_usd=travel_cost,
            travel_co2e_kg=travel_co2e,
            travel_mode=travel_mode,
            travel_distance_km=travel_distance,
            inbound_geometry=inbound_geom,
            duration_buffer_min=duration_buffer,
            party_size=party_size,
            reasoning_text=ov.reasoning_text if ov else "",
            reasoning_scores=ov.reasoning_scores if ov else "",
            alternatives_considered=ov.alternatives_considered if ov else (),
            signals_influencing=ov.signals_influencing if ov else (),
        )
        visits.append(nv)
        spent += nv.total_cost_usd
        cursor_time = nv.depart
        cursor_poi_id = poi.poi_id

    # Upgrade the re-timed legs from straight-line seeds to routed polylines
    # (cache-first; falls back to the seed when OSRM is disabled/unreachable).
    await populate_geometries(visits, city_slug=_city_slug(index.city) or "unknown", pois=index.pois)

    total_minutes = (visits[-1].depart - start_time).total_seconds() / 60.0 if visits else 0.0
    return Itinerary(
        city=index.city,
        user_id=plan.user_id,
        visits=visits,
        total_minutes=total_minutes,
        total_score=plan.total_score,
        plan_id=uuid.uuid4().hex[:12],
        derived_from=plan.plan_id,
    )
