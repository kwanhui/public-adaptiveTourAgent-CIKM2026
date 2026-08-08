"""Integration test: replan after rain trigger fires.

Proves the replan loop closes: given a snapshot with rain, the new plan
prefers indoor POIs over the outdoor ones in the original.
"""

from datetime import datetime

import pytest

from adaptivetouragent.agent.types import UserProfile
from adaptivetouragent.fusion.fuser import fuse
from adaptivetouragent.fusion.snapshot import UserState, WeatherReading
from adaptivetouragent.replanner.initial import plan_initial
from adaptivetouragent.replanner.replan import diff_plans, replan
from adaptivetouragent.replanner.types import ReplanRequest
from adaptivetouragent.signals.sources.base import SignalBatch
from adaptivetouragent.signals.triggers.types import TriggerEvent


@pytest.mark.asyncio
async def test_replan_after_rain_prefers_indoor(stub_llm, singapore_index) -> None:
    profile = UserProfile(
        user_id="family",
        name="Family",
        category_weights={"park": 0.4, "viewpoint": 0.3, "museum": 0.3},
        family_size=4,
    )
    start = datetime(2026, 5, 2, 9, 0)
    initial = await plan_initial(
        profile=profile,
        index=singapore_index,
        start_time=start,
        budget_minutes=600,
        llm=stub_llm,
    )
    assert initial.visits, "initial plan should have at least one visit"

    rain_at = datetime(2026, 5, 2, 11, 30)
    rainy = WeatherReading(temp_c=25.0, precip_mm_per_h=5.0, condition="rain", fetched_at=rain_at, source="t")
    snap = fuse(
        [SignalBatch(at=rain_at, weather=rainy)],
        user=UserState(fatigue_0_1=0.2, elapsed_min=150, pois_visited=2, last_break_min_ago=None),
        city="Singapore",
        at=rain_at,
    )
    triggers = [
        TriggerEvent(
            kind="weather_rain_onset",
            severity="warn",
            at=rain_at,
            affects=[],
            details={"precip_mm_per_h": "5.0"},
            snapshot_id=snap.snapshot_id,
        )
    ]
    executed_prefix = [v for v in initial.visits if v.depart <= rain_at]

    request = ReplanRequest(
        current=initial,
        executed_prefix=executed_prefix,
        snapshot=snap,
        triggers=triggers,
        now=rain_at,
    )

    response = await replan(
        request,
        profile=profile,
        index=singapore_index,
        llm=stub_llm,
        budget_minutes=600,
        start_time=start,
    )
    new_plan = response.updated

    # The non-prefix tail should be all indoor (rain-driven require_indoor).
    tail = [v for v in new_plan.visits if v.depart > rain_at]
    for visit in tail:
        poi = singapore_index.pois[visit.poi_id]
        assert poi.indoor, f"{visit.name} is outdoor, should not appear after rain"

    assert response.diff.summary != "no change" or not tail
    assert response.rationale  # non-empty
    assert new_plan.derived_from == initial.plan_id


@pytest.mark.asyncio
async def test_diff_marks_executed_prefix(stub_llm, singapore_index) -> None:
    profile = UserProfile(
        user_id="solo", name="Solo", category_weights={"museum": 1.0}
    )
    start = datetime(2026, 5, 2, 10, 0)
    initial = await plan_initial(
        profile=profile, index=singapore_index, start_time=start, budget_minutes=540, llm=stub_llm
    )
    if not initial.visits:
        pytest.skip("LLM scoring fallback produced empty plan")

    # Treat the first visit as executed.
    executed = initial.visits[:1]

    diff = diff_plans(initial, initial, executed)
    # The first visit must be PRESERVE/executed.
    first_entry = diff.entries[0]
    assert first_entry.poi_id == executed[0].poi_id
    assert first_entry.reason == "executed"


@pytest.mark.asyncio
async def test_replan_does_not_filter_by_open_at_request_now(stub_llm, singapore_index) -> None:
    """Regression: a 9am rainy replan must keep museums (which open at 10am).

    Previously `replan()` passed `open_at_hour=request.now.hour` to the
    retriever, which dropped every POI not yet open. With `require_indoor=True`
    on top, Singapore's 7 indoor candidates collapsed to 2 or 3, leaving a
    near-empty tail. The optimiser already shifts arrival to opening time;
    the retrieval-level filter was redundant and harmful.
    """
    profile = UserProfile(
        user_id="rain_open",
        name="Tourist",
        category_weights={"museum": 0.6, "heritage": 0.2, "food": 0.2},
    )
    start = datetime(2026, 5, 2, 9, 0)
    initial = await plan_initial(
        profile=profile,
        index=singapore_index,
        start_time=start,
        budget_minutes=600,
        llm=stub_llm,
    )

    rain_at = start
    rainy = WeatherReading(
        temp_c=25.0, precip_mm_per_h=4.0, condition="rain", fetched_at=rain_at, source="manual"
    )
    snap = fuse(
        [SignalBatch(at=rain_at, weather=rainy)],
        user=UserState(fatigue_0_1=0.0, elapsed_min=0, pois_visited=0, last_break_min_ago=None),
        city="Singapore",
        at=rain_at,
    )
    triggers = [
        TriggerEvent(
            kind="weather_rain_onset",
            severity="warn",
            at=rain_at,
            affects=[],
            details={"precip_mm_per_h": "4.0"},
            snapshot_id=snap.snapshot_id,
        )
    ]

    request = ReplanRequest(
        current=initial,
        executed_prefix=[],
        snapshot=snap,
        triggers=triggers,
        now=rain_at,
    )
    response = await replan(
        request,
        profile=profile,
        index=singapore_index,
        llm=stub_llm,
        budget_minutes=600,
        start_time=start,
    )

    # Singapore has 7 indoor POIs. With museums opening at 10am and a 9am
    # replan, the old bug bottomed out at 2 visits. The fix should give the
    # planner at least 4 stops to choose across an 8-hour rainy day.
    assert len(response.updated.visits) >= 4, (
        f"rainy replan starved by open_at_hour filter: got "
        f"{len(response.updated.visits)} visits: "
        f"{[v.name for v in response.updated.visits]}"
    )
    # Every visit on the new plan must still be indoor.
    for v in response.updated.visits:
        assert singapore_index.pois[v.poi_id].indoor, (
            f"{v.name} is outdoor, replanner must keep require_indoor=True"
        )


@pytest.mark.asyncio
async def test_replan_upgrades_inbound_geometry(stub_llm, singapore_index, monkeypatch) -> None:
    """Regression: replan() must call populate_geometries on the new plan.

    Without this call, every replanned leg's `inbound_geometry` was the
    2-point straight line seeded by `greedy_plan` (the OSRM upgrade only
    ran on the initial plan). The map then showed "as the crow flies"
    lines for the rain-replanned tail while the original plan's legs
    followed real streets.
    """
    from adaptivetouragent.replanner import replan as replan_module

    calls: list[dict] = []
    real = replan_module.populate_geometries

    async def spy(visits, *, city_slug, pois, start_location=None):
        calls.append({"n_visits": len(visits), "city_slug": city_slug})
        await real(visits, city_slug=city_slug, pois=pois, start_location=start_location)

    monkeypatch.setattr(replan_module, "populate_geometries", spy)

    profile = UserProfile(
        user_id="geom",
        name="Tourist",
        category_weights={"park": 0.4, "viewpoint": 0.3, "museum": 0.3},
    )
    start = datetime(2026, 5, 2, 9, 0)
    initial = await plan_initial(
        profile=profile,
        index=singapore_index,
        start_time=start,
        budget_minutes=600,
        llm=stub_llm,
    )
    rain_at = start
    rainy = WeatherReading(
        temp_c=25.0, precip_mm_per_h=4.0, condition="rain", fetched_at=rain_at, source="manual"
    )
    snap = fuse(
        [SignalBatch(at=rain_at, weather=rainy)],
        user=UserState(fatigue_0_1=0, elapsed_min=0, pois_visited=0, last_break_min_ago=None),
        city="Singapore",
        at=rain_at,
    )
    triggers = [
        TriggerEvent(
            kind="weather_rain_onset",
            severity="warn",
            at=rain_at,
            affects=[],
            details={},
            snapshot_id=snap.snapshot_id,
        )
    ]
    request = ReplanRequest(
        current=initial,
        executed_prefix=[],
        snapshot=snap,
        triggers=triggers,
        now=rain_at,
    )
    response = await replan(
        request,
        profile=profile,
        index=singapore_index,
        llm=stub_llm,
        budget_minutes=600,
        start_time=start,
    )

    assert len(calls) == 1, "populate_geometries was not invoked by replan()"
    assert calls[0]["n_visits"] == len(response.updated.visits)
    assert calls[0]["city_slug"] == "singapore"

    # Every non-first visit on the new plan should now carry inbound geometry,
    # at minimum the 2-point straight-line fallback (OSRM is disabled in CI).
    for v in response.updated.visits[1:]:
        assert len(v.inbound_geometry) >= 2
