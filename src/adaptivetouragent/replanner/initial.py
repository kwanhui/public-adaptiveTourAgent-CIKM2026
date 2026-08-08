"""Initial-plan entry point.

Pattern: score, then route. The replanner module exists to keep
"first plan" and "mid-trip replan" as named entry points; they share the
same scoring + greedy-orienteering core.

Multi-day planning lives here too (`plan_multi_day`): one LLM scoring call
per day, anchored on a shared accommodation, with cross-day POI dedupe.
"""

import asyncio
import uuid
from datetime import datetime, time, timedelta

from adaptivetouragent.accommodations.types import Accommodation
from adaptivetouragent.agent.loop import score_pois
from adaptivetouragent.agent.types import (
    AccessibilityRequirements,
    AgentRunStats,
    GroupMember,
    UserProfile,
)
from adaptivetouragent.itinerary.geometry import populate_geometries
from adaptivetouragent.itinerary.optimizer import greedy_plan
from adaptivetouragent.itinerary.types import (
    POI,
    DayItinerary,
    Itinerary,
    MultiDayItinerary,
)
from adaptivetouragent.llm.openai_client import OpenAIClient
from adaptivetouragent.llm.provider import LLMProvider
from adaptivetouragent.retrieval.poi_index import POIIndex, _city_slug, load_city
from adaptivetouragent.retrieval.retriever import retrieve_candidates


def _accommodation_as_poi(acc: Accommodation) -> POI:
    """Wrap an accommodation as a phantom POI so the optimiser can route from it."""
    return POI(
        poi_id=f"acc:{acc.accommodation_id}",
        name=acc.name,
        category="accommodation",
        lat=acc.lat,
        lon=acc.lon,
        avg_duration_min=0.0,
        popularity=acc.rating / 5.0,
        open_hours=(0, 24),
        kid_friendly=acc.kid_friendly,
        indoor=True,
    )


def _group_aggregated_weights(profile: UserProfile) -> dict[str, float]:
    """Group-aware category weights (thin wrapper over ``UserProfile.aggregated_weights``).

    A single member's (or live) veto drops a category for the whole group; a
    boost amplifies it by 25%. Kept as a module function for existing callers
    and tests; the logic lives on the profile so retrieval, scoring, and the
    replanner all agree.
    """
    return profile.aggregated_weights()


def _vetoed_poi_ids(index: POIIndex, profile: UserProfile) -> set[str]:
    """POI ids whose category is vetoed by the group, for a hard retrieval exclude.

    Zeroing the category weight is not enough on a small catalogue: a popular
    vetoed POI can still surface on the popularity term. Excluding the ids
    enforces "a single veto blocks the category" as a hard filter.
    """
    vetoed = profile.vetoed_categories()
    if not vetoed:
        return set()
    return {p.poi_id for p in index.pois.values() if p.category in vetoed}


async def plan_initial(
    *,
    profile: UserProfile,
    index: POIIndex,
    start_time: datetime,
    budget_minutes: float,
    llm: LLMProvider,
    top_k_candidates: int = 12,
    require_indoor: bool | None = None,
    exclude_pois: set[str] | None = None,
    start_location: POI | Accommodation | None = None,
    money_budget_usd: float | None = None,
    prefer_low_carbon: bool = False,
    pace: str = "standard",
    stats: AgentRunStats | None = None,
) -> Itinerary:
    """Produce the first itinerary for a profile.

    `exclude_pois` lets the multi-day planner skip POIs already visited on
    earlier days. `start_location` (a POI or an Accommodation) anchors the
    first hop's travel time on the hotel rather than starting at a POI.
    `money_budget_usd` caps cumulative entry-fee + transit-fare spend
    (multiplied by `profile.family_size`); set to None to disable.
    `prefer_low_carbon` biases the optimiser toward low-CO2e legs.
    """
    weights = _group_aggregated_weights(profile)
    accessibility = profile.accessibility
    exclude_pois = set(exclude_pois or set()) | _vetoed_poi_ids(index, profile) | set(profile.excluded_pois)
    # Note: we deliberately do not filter by open_at_hour here. The optimiser
    # already shifts arrival to the POI's opening time when the cursor is
    # earlier; filtering at retrieval would drop POIs that open later in the
    # day (e.g. museums at 10am when the tour starts at 9am), which on
    # multi-day tours starves later days of remaining candidates.
    candidates = retrieve_candidates(
        index,
        category_weights=weights,
        top_k=top_k_candidates,
        require_indoor=require_indoor,
        require_kid_friendly=profile.require_kid_friendly or None,
        exclude=exclude_pois,
        require_wheelchair=accessibility.require_wheelchair or None,
        require_dietary=accessibility.dietary,
        require_low_stimulation=accessibility.require_low_stimulation or None,
    )

    if not candidates:
        return Itinerary(
            city=index.city,
            user_id=profile.user_id,
            visits=[],
            total_minutes=0.0,
            total_score=0.0,
            plan_id=uuid.uuid4().hex[:12],
        )

    poi_scores = await score_pois(
        profile=profile,
        candidates=candidates,
        llm=llm,
        stats=stats,
    )

    anchor: POI | None = None
    if isinstance(start_location, Accommodation):
        anchor = _accommodation_as_poi(start_location)
    elif isinstance(start_location, POI):
        anchor = start_location

    plan = greedy_plan(
        index=index,
        candidates=candidates,
        poi_scores=poi_scores,
        start_time=start_time,
        budget_minutes=budget_minutes,
        user_id=profile.user_id,
        plan_id=uuid.uuid4().hex[:12],
        start_location=anchor,
        money_budget_usd=money_budget_usd,
        party_size=max(1, profile.family_size),
        prefer_low_carbon=prefer_low_carbon,
        require_wheelchair=profile.accessibility.require_wheelchair,
        pace=pace,
    )

    # Upgrade each leg's straight-line inbound geometry to an OSRM-routed
    # polyline so the map visualisation doesn't show "as the crow flies"
    # paths. Cache-first; falls back to the straight line on any failure
    # (or when ATAU_DISABLE_OSRM=1 is set, e.g. in CI).
    await populate_geometries(
        plan.visits,
        city_slug=_city_slug(index.city) or "unknown",
        pois=index.pois,
        start_location=anchor,
    )
    return plan


async def plan_multi_day(
    *,
    profile: UserProfile,
    index: POIIndex,
    start_datetime: datetime,
    end_datetime: datetime,
    llm: LLMProvider,
    accommodation: Accommodation | None = None,
    daily_start_hour: int = 9,
    daily_end_hour: int = 19,
    top_k_candidates: int = 12,
    money_budget_usd: float | None = None,
    prefer_low_carbon: bool = False,
    pace: str = "standard",
    stats: AgentRunStats | None = None,
) -> MultiDayItinerary:
    """Plan a multi-day tour anchored on one accommodation.

    Each day calls `plan_initial` with the day's effective time window
    (clipped to `start_datetime` / `end_datetime`). POIs visited on earlier
    days are excluded from later days. The first hop of each day is routed
    from the accommodation.
    """
    if end_datetime <= start_datetime:
        raise ValueError("end_datetime must be after start_datetime")

    days: list[DayItinerary] = []
    visited: set[str] = set()
    cursor_date = start_datetime.date()
    end_date = end_datetime.date()
    day_index = 0
    total_score = 0.0
    spent_so_far = 0.0

    while cursor_date <= end_date:
        day_start = datetime.combine(cursor_date, time(hour=daily_start_hour))
        day_end = datetime.combine(cursor_date, time(hour=daily_end_hour))

        if cursor_date == start_datetime.date():
            day_start = max(day_start, start_datetime)
        if cursor_date == end_datetime.date():
            day_end = min(day_end, end_datetime)

        if day_end > day_start:
            budget = (day_end - day_start).total_seconds() / 60.0
            day_money_budget = money_budget_usd - spent_so_far if money_budget_usd is not None else None
            day_plan = await plan_initial(
                profile=profile,
                index=index,
                start_time=day_start,
                budget_minutes=budget,
                llm=llm,
                top_k_candidates=top_k_candidates,
                exclude_pois=set(visited),
                start_location=accommodation,
                money_budget_usd=day_money_budget,
                prefer_low_carbon=prefer_low_carbon,
                pace=pace,
                stats=stats,
            )
            spent_so_far += day_plan.total_cost_usd
            days.append(
                DayItinerary(
                    day_index=day_index,
                    date=cursor_date,
                    accommodation=accommodation,
                    start_time=day_start,
                    end_time=day_end,
                    visits=day_plan.visits,
                    total_minutes=day_plan.total_minutes,
                    total_score=day_plan.total_score,
                    plan_id=day_plan.plan_id,
                )
            )
            visited.update(v.poi_id for v in day_plan.visits)
            total_score += day_plan.total_score

        day_index += 1
        cursor_date = cursor_date + timedelta(days=1)

    return MultiDayItinerary(
        city=index.city,
        user_id=profile.user_id,
        accommodation=accommodation,
        days=days,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        total_score=total_score,
        plan_id=uuid.uuid4().hex[:12],
    )


def plan_tour(
    *,
    user_profile: UserProfile | dict | str,
    city: str,
    days: int = 1,
    start_hour: int = 9,
    end_hour: int = 19,
    model: str = "gpt-4o-mini",
) -> Itinerary:
    """Synchronous convenience wrapper used by the README example.

    `user_profile` may be a UserProfile, a dict, or a path to a YAML file.
    """
    profile = _coerce_profile(user_profile)
    index = load_city(city)
    llm = OpenAIClient(model=model)

    today = datetime.now().date()
    start_time = datetime.combine(today, time(hour=start_hour))
    budget_minutes = (end_hour - start_hour) * 60.0 * days

    return asyncio.run(
        plan_initial(
            profile=profile,
            index=index,
            start_time=start_time,
            budget_minutes=budget_minutes,
            llm=llm,
        )
    )


def _coerce_profile(profile: UserProfile | dict | str) -> UserProfile:
    if isinstance(profile, UserProfile):
        return profile
    if isinstance(profile, str):
        # YAML file path
        import yaml

        with open(profile, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return _profile_from_dict(data)
    if isinstance(profile, dict):
        return _profile_from_dict(profile)
    raise TypeError(f"Unsupported profile type: {type(profile)}")


def _profile_from_dict(data: dict) -> UserProfile:
    accessibility_data = data.get("accessibility", {}) or {}
    accessibility = AccessibilityRequirements(
        require_wheelchair=bool(accessibility_data.get("require_wheelchair", False)),
        dietary=tuple(accessibility_data.get("dietary", [])),
        require_low_stimulation=bool(accessibility_data.get("require_low_stimulation", False)),
    )
    members_data = data.get("group_members", []) or []
    group_members = tuple(
        GroupMember(
            member_id=str(m.get("member_id", f"m{i}")),
            name=str(m.get("name", f"Member {i + 1}")),
            category_weights={str(k): float(v) for k, v in (m.get("category_weights") or {}).items()},
            veto_categories=tuple(m.get("veto_categories", [])),
            boost_categories=tuple(m.get("boost_categories", [])),
        )
        for i, m in enumerate(members_data)
    )
    return UserProfile(
        user_id=str(data.get("user_id", "anon")),
        name=str(data.get("name", "Tourist")),
        category_weights={str(k): float(v) for k, v in data.get("category_weights", {}).items()},
        family_size=int(data.get("family_size", 1)),
        require_kid_friendly=bool(data.get("require_kid_friendly", False)),
        notes=str(data.get("notes", "")),
        accessibility=accessibility,
        group_members=group_members,
    )
