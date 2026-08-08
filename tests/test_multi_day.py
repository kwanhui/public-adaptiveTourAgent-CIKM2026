"""Multi-day planner tests: anchored on accommodation, no cross-day duplicates."""

from datetime import datetime

import pytest

from adaptivetouragent.accommodations.types import Accommodation
from adaptivetouragent.agent.types import UserProfile
from adaptivetouragent.replanner.initial import plan_multi_day


def _profile() -> UserProfile:
    return UserProfile(
        user_id="md_tester",
        name="Multi-Day",
        category_weights={"park": 0.25, "museum": 0.2, "viewpoint": 0.2,
                          "heritage": 0.15, "food": 0.1, "neighbourhood": 0.1},
        family_size=2,
    )


def _hotel() -> Accommodation:
    return Accommodation(
        accommodation_id="acc_test",
        name="Test Hotel",
        lat=1.2900,  # Singapore city centre
        lon=103.8500,
        price_per_night_usd=180.0,
        rating=4.3,
        kid_friendly=True,
        near_mrt=True,
        description="Test hotel near city centre",
    )


@pytest.mark.asyncio
async def test_plan_multi_day_produces_one_day_per_calendar_day(stub_llm, singapore_index) -> None:
    plan = await plan_multi_day(
        profile=_profile(),
        index=singapore_index,
        start_datetime=datetime(2026, 6, 1, 9, 0),
        end_datetime=datetime(2026, 6, 3, 18, 0),
        llm=stub_llm,
        accommodation=_hotel(),
    )
    assert plan.n_days == 3
    assert plan.accommodation is not None
    assert plan.accommodation.name == "Test Hotel"


@pytest.mark.asyncio
async def test_plan_multi_day_dedupes_pois_across_days(stub_llm, singapore_index) -> None:
    plan = await plan_multi_day(
        profile=_profile(),
        index=singapore_index,
        start_datetime=datetime(2026, 6, 1, 9, 0),
        end_datetime=datetime(2026, 6, 3, 18, 0),
        llm=stub_llm,
        accommodation=_hotel(),
    )
    seen: set[str] = set()
    for day in plan.days:
        for visit in day.visits:
            assert visit.poi_id not in seen, f"POI {visit.poi_id} appears on multiple days"
            seen.add(visit.poi_id)


@pytest.mark.asyncio
async def test_plan_multi_day_clips_first_and_last_day(stub_llm, singapore_index) -> None:
    """If the trip starts mid-afternoon and ends mid-morning, those days are short."""
    plan = await plan_multi_day(
        profile=_profile(),
        index=singapore_index,
        start_datetime=datetime(2026, 6, 1, 14, 0),  # arrive 2pm day 1
        end_datetime=datetime(2026, 6, 3, 11, 0),   # depart 11am day 3
        llm=stub_llm,
        accommodation=_hotel(),
    )
    assert plan.n_days == 3
    # First day starts after the trip's start, not at daily_start_hour.
    assert plan.days[0].start_time == datetime(2026, 6, 1, 14, 0)
    # Last day ends before the trip's end.
    assert plan.days[-1].end_time == datetime(2026, 6, 3, 11, 0)


@pytest.mark.asyncio
async def test_plan_multi_day_works_without_accommodation(stub_llm, singapore_index) -> None:
    plan = await plan_multi_day(
        profile=_profile(),
        index=singapore_index,
        start_datetime=datetime(2026, 6, 1, 9, 0),
        end_datetime=datetime(2026, 6, 2, 18, 0),
        llm=stub_llm,
        accommodation=None,
    )
    assert plan.n_days == 2
    assert plan.accommodation is None
    # Each day should still produce visits.
    assert all(d.n_visits > 0 for d in plan.days)


@pytest.mark.asyncio
async def test_plan_multi_day_rejects_inverted_window(stub_llm, singapore_index) -> None:
    with pytest.raises(ValueError):
        await plan_multi_day(
            profile=_profile(),
            index=singapore_index,
            start_datetime=datetime(2026, 6, 3, 18, 0),
            end_datetime=datetime(2026, 6, 1, 9, 0),
            llm=stub_llm,
        )


@pytest.mark.asyncio
async def test_accommodation_anchor_affects_first_visit_arrival(stub_llm, singapore_index) -> None:
    """The first visit each day arrives later than `start_time` because of travel from the hotel."""
    far_hotel = Accommodation(
        accommodation_id="acc_far",
        name="Far Hotel",
        lat=1.4500,  # north of the city, far from any POI
        lon=103.7800,
        price_per_night_usd=80.0,
        rating=3.5,
        kid_friendly=True,
        near_mrt=False,
        description="Way out north",
    )
    plan = await plan_multi_day(
        profile=_profile(),
        index=singapore_index,
        start_datetime=datetime(2026, 6, 1, 9, 0),
        end_datetime=datetime(2026, 6, 1, 18, 0),
        llm=stub_llm,
        accommodation=far_hotel,
    )
    if plan.days and plan.days[0].visits:
        # The first visit should arrive after 9:00 because of the hotel-to-POI commute.
        first_arrival = plan.days[0].visits[0].arrive
        assert first_arrival > datetime(2026, 6, 1, 9, 0)
