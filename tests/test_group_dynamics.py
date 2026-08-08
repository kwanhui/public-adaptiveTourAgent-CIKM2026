"""Group dynamics: per-member vetoes/boosts as live constraints.

The veto/boost contract the paper claims:
  - a single member's veto blocks that category for the whole party;
  - a boost amplifies a category;
  - both are re-applied on every mid-trip replan (not just at plan time);
  - a veto cast mid-trip (live_veto_categories) re-routes the tail.
"""

from datetime import datetime

import pytest

from adaptivetouragent.agent.types import GroupMember, UserProfile
from adaptivetouragent.fusion.fuser import fuse
from adaptivetouragent.fusion.snapshot import UserState
from adaptivetouragent.replanner.initial import plan_initial
from adaptivetouragent.replanner.replan import replan
from adaptivetouragent.replanner.types import ReplanRequest
from adaptivetouragent.signals.sources.base import SignalBatch
from adaptivetouragent.signals.triggers.types import TriggerEvent


def test_aggregated_weights_drops_member_veto_and_applies_boost() -> None:
    a = GroupMember("m0", "A", {"museum": 1.0})
    b = GroupMember("m1", "B", {"park": 1.0, "museum": 1.0}, veto_categories=("museum",), boost_categories=("park",))
    profile = UserProfile(
        user_id="g", name="Group", category_weights={"museum": 1.0}, group_members=(a, b)
    )
    weights = profile.aggregated_weights()
    assert "museum" not in weights, "a single member's veto must drop the category for the group"
    # park share: A contributes 0, B contributes 0.5 -> average 0.25, then the
    # boost multiplies by 1.25 -> 0.3125. This checks both averaging and boost.
    assert weights["park"] == pytest.approx(0.25 * 1.25)


def test_live_veto_drops_category_for_single_user() -> None:
    profile = UserProfile(
        user_id="s",
        name="Solo",
        category_weights={"museum": 0.5, "park": 0.5},
        live_veto_categories=("museum",),
    )
    weights = profile.aggregated_weights()
    assert "museum" not in weights
    assert "park" in weights
    assert "museum" in profile.vetoed_categories()


@pytest.mark.asyncio
async def test_initial_plan_excludes_vetoed_category(stub_llm, singapore_index) -> None:
    """A group member vetoing 'museum' must keep every museum out of the plan."""
    members = (
        GroupMember("m0", "A", {"museum": 1.0}),
        GroupMember("m1", "B", {"park": 1.0}, veto_categories=("museum",)),
    )
    profile = UserProfile(
        user_id="g", name="Group", category_weights={"museum": 0.5, "park": 0.5},
        family_size=2, group_members=members,
    )
    start = datetime(2026, 5, 2, 9, 0)
    plan = await plan_initial(
        profile=profile, index=singapore_index, start_time=start, budget_minutes=600, llm=stub_llm
    )
    for v in plan.visits:
        assert singapore_index.pois[v.poi_id].category != "museum", (
            f"{v.name} is a museum but museums were vetoed"
        )


@pytest.mark.asyncio
async def test_replan_reapplies_live_veto(stub_llm, singapore_index) -> None:
    """A veto cast mid-trip drops that category from the replanned tail.

    Vetoes 'park': Gardens by the Bay (popularity 0.95) is the most popular
    POI and is reliably picked, so the no-veto run contains a park and the
    veto run does not, proving the replanner re-applies the live veto.
    """
    base_weights = {"park": 0.4, "viewpoint": 0.3, "food": 0.3}
    start = datetime(2026, 5, 2, 9, 0)

    async def run(live_veto: tuple[str, ...]) -> list[str]:
        profile = UserProfile(
            user_id="v", name="Tourist", category_weights=base_weights,
            live_veto_categories=live_veto,
        )
        initial = await plan_initial(
            profile=profile, index=singapore_index, start_time=start, budget_minutes=600, llm=stub_llm
        )
        snap = fuse(
            [SignalBatch(at=start)],
            user=UserState(fatigue_0_1=0.0, elapsed_min=0, pois_visited=0, last_break_min_ago=None),
            city="Singapore",
            at=start,
        )
        triggers = [
            TriggerEvent(
                kind="user_request", severity="info", at=start, affects=[], details={},
                snapshot_id=snap.snapshot_id,
            )
        ]
        request = ReplanRequest(
            current=initial,
            executed_prefix=[],
            snapshot=snap,
            triggers=triggers,
            now=start,
        )
        response = await replan(
            request, profile=profile, index=singapore_index, llm=stub_llm,
            budget_minutes=600, start_time=start,
        )
        return [singapore_index.pois[v.poi_id].category for v in response.updated.visits]

    no_veto = await run(())
    with_veto = await run(("park",))
    assert "park" in no_veto, "sanity: a high-popularity park should be picked without a veto"
    assert "park" not in with_veto, "a live veto must keep parks out of the replanned tail"
