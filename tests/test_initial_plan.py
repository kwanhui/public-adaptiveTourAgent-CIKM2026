"""End-to-end test: profile -> initial itinerary using the stub LLM."""

from datetime import datetime

import pytest

from adaptivetouragent.agent.types import AccessibilityRequirements, UserProfile
from adaptivetouragent.replanner.initial import plan_initial


@pytest.mark.asyncio
async def test_plan_initial_produces_visits(stub_llm, singapore_index) -> None:
    profile = UserProfile(
        user_id="test01",
        name="Tester",
        category_weights={"park": 0.5, "viewpoint": 0.3, "museum": 0.2},
        family_size=2,
        require_kid_friendly=True,
    )
    start = datetime(2026, 5, 2, 9, 0)
    plan = await plan_initial(
        profile=profile,
        index=singapore_index,
        start_time=start,
        budget_minutes=600,
        llm=stub_llm,
        top_k_candidates=10,
    )
    assert plan.city == "Singapore"
    assert plan.user_id == "test01"
    assert len(plan.visits) >= 2
    assert plan.total_minutes <= 600
    # Visits are time-ordered.
    arrives = [v.arrive for v in plan.visits]
    assert arrives == sorted(arrives)
    # The stub LLM was called exactly once.
    assert len(stub_llm.calls) == 1


@pytest.mark.asyncio
async def test_plan_initial_skips_closed_pois(stub_llm, singapore_index) -> None:
    profile = UserProfile(
        user_id="late",
        name="Late",
        category_weights={"museum": 1.0},
    )
    # Start at 18:00; most museums close by 19:00.
    start = datetime(2026, 5, 2, 18, 0)
    plan = await plan_initial(
        profile=profile,
        index=singapore_index,
        start_time=start,
        budget_minutes=180,
        llm=stub_llm,
    )
    for visit in plan.visits:
        # No visit should depart after the POI's close hour.
        poi = singapore_index.pois[visit.poi_id]
        assert visit.depart.hour < poi.open_hours[1] or visit.depart.hour == poi.open_hours[1]


@pytest.mark.asyncio
async def test_plan_initial_handles_zero_candidates(stub_llm, singapore_index) -> None:
    profile = UserProfile(
        user_id="impossible",
        name="Impossible",
        # Category weights match nothing in Singapore data.
        category_weights={"deepsea_diving": 1.0},
    )
    start = datetime(2026, 5, 2, 9, 0)
    plan = await plan_initial(
        profile=profile,
        index=singapore_index,
        start_time=start,
        budget_minutes=600,
        llm=stub_llm,
    )
    # Still returns a plan (alpha-weighted relevance picks fallbacks).
    assert plan.city == "Singapore"


@pytest.mark.asyncio
async def test_pace_packed_fits_more_stops_than_relaxed(stub_llm, singapore_index) -> None:
    """Pace knob: same profile, same window; packed should fit at least as
    many stops as relaxed (often strictly more)."""
    profile = UserProfile(
        user_id="t",
        name="Tester",
        family_size=2,
        category_weights={"park": 0.3, "viewpoint": 0.2, "museum": 0.2, "heritage": 0.15, "food": 0.15},
    )
    start = datetime(2026, 6, 1, 8, 0)
    relaxed = await plan_initial(
        profile=profile,
        index=singapore_index,
        start_time=start,
        budget_minutes=720,
        llm=stub_llm,
        pace="relaxed",
    )
    packed = await plan_initial(
        profile=profile,
        index=singapore_index,
        start_time=start,
        budget_minutes=720,
        llm=stub_llm,
        pace="packed",
    )
    assert len(packed.visits) >= len(relaxed.visits)


@pytest.mark.asyncio
async def test_sensory_filter_keeps_only_low_stim_pois(stub_llm, singapore_index) -> None:
    """`require_low_stimulation=True` must drop POIs flagged otherwise."""
    profile = UserProfile(
        user_id="t",
        name="Tester",
        family_size=1,
        category_weights={"museum": 0.4, "park": 0.3, "heritage": 0.3},
        accessibility=AccessibilityRequirements(require_low_stimulation=True),
    )
    plan = await plan_initial(
        profile=profile,
        index=singapore_index,
        start_time=datetime(2026, 6, 1, 9, 0),
        budget_minutes=600,
        llm=stub_llm,
    )
    for v in plan.visits:
        poi = singapore_index.pois[v.poi_id]
        assert poi.sensory_low_stimulation, (
            f"sensory filter let {poi.name} through (sensory_low_stimulation={poi.sensory_low_stimulation})"
        )


@pytest.mark.asyncio
async def test_wheelchair_filter_keeps_only_accessible_pois(stub_llm, singapore_index) -> None:
    """POI-level wheelchair filter removes inaccessible neighbourhoods
    (sg08 Little India, sg14 Kampong Glam)."""
    profile = UserProfile(
        user_id="t",
        name="Tester",
        family_size=2,
        category_weights={"neighbourhood": 0.5, "park": 0.5},
        accessibility=AccessibilityRequirements(require_wheelchair=True),
    )
    plan = await plan_initial(
        profile=profile,
        index=singapore_index,
        start_time=datetime(2026, 6, 1, 9, 0),
        budget_minutes=600,
        llm=stub_llm,
    )
    for v in plan.visits:
        poi = singapore_index.pois[v.poi_id]
        assert poi.wheelchair_accessible, f"wheelchair filter let {poi.name} through"


@pytest.mark.asyncio
async def test_inbound_geometry_falls_back_to_straight_line(stub_llm, singapore_index) -> None:
    """With OSRM disabled (CI default), every leg should have a 2-point
    straight-line inbound_geometry (the fallback). First visit has none."""
    profile = UserProfile(
        user_id="t",
        name="Tester",
        family_size=2,
        category_weights={"park": 0.3, "viewpoint": 0.3, "museum": 0.4},
    )
    plan = await plan_initial(
        profile=profile,
        index=singapore_index,
        start_time=datetime(2026, 6, 1, 9, 0),
        budget_minutes=480,
        llm=stub_llm,
    )
    assert len(plan.visits) >= 2
    # First visit had no anchor, so geometry is empty.
    assert plan.visits[0].inbound_geometry == ()
    # Subsequent visits have at least a 2-point straight line.
    for v in plan.visits[1:]:
        assert len(v.inbound_geometry) >= 2
