"""Coverage for the six iteration features (money, accessibility, reasoning,
group, booking, carbon)."""

from datetime import datetime
from pathlib import Path

import pytest

from adaptivetouragent.accommodations.types import Accommodation
from adaptivetouragent.agent.types import (
    AccessibilityRequirements,
    GroupMember,
    UserProfile,
)
from adaptivetouragent.booking.actuator import BookingActuator
from adaptivetouragent.itinerary.routing import (
    CO2E_KG_PER_KM,
    FARES_PER_KM_USD,
    travel_co2e_kg,
    travel_cost_usd,
)
from adaptivetouragent.itinerary.types import POI
from adaptivetouragent.replanner.initial import _group_aggregated_weights, plan_initial

# ----- money budget --------------------------------------------------


@pytest.mark.asyncio
async def test_money_budget_caps_total_spend(stub_llm, singapore_index) -> None:
    profile = UserProfile(
        user_id="cheap",
        name="Budget",
        family_size=2,
        category_weights={"museum": 1.0},
    )
    plan = await plan_initial(
        profile=profile,
        index=singapore_index,
        start_time=datetime(2026, 5, 2, 10, 0),
        budget_minutes=600,
        llm=stub_llm,
        money_budget_usd=40.0,
    )
    # Family of 2 with $40 cap: total cost should not exceed $40.
    assert plan.total_cost_usd <= 40.0


@pytest.mark.asyncio
async def test_no_money_budget_allows_pricier_picks(stub_llm, singapore_index) -> None:
    profile = UserProfile(
        user_id="rich",
        name="Unlimited",
        category_weights={"theme_park": 1.0, "museum": 0.5},
    )
    plan = await plan_initial(
        profile=profile,
        index=singapore_index,
        start_time=datetime(2026, 5, 2, 10, 0),
        budget_minutes=600,
        llm=stub_llm,
        money_budget_usd=None,
    )
    # Without a cap, expensive POIs (Sentosa $60, Zoo $39) are fair game.
    assert plan.total_cost_usd > 0  # at least some POI has a fee


def test_routing_fare_table_consistency() -> None:
    assert FARES_PER_KM_USD["walk"] == 0.0
    assert FARES_PER_KM_USD["transit"] > 0
    assert FARES_PER_KM_USD["drive"] > FARES_PER_KM_USD["transit"]


def test_travel_cost_zero_when_walking_short_hop() -> None:
    a = POI("a", "A", "x", 1.30, 103.85, 30, 0.5)
    b = POI("b", "B", "x", 1.301, 103.851, 30, 0.5)  # ~150 m
    assert travel_cost_usd(a, b) == 0.0  # walked


# ----- accessibility ------------------------------------------------


@pytest.mark.asyncio
async def test_wheelchair_filter_drops_inaccessible_pois(stub_llm, singapore_index) -> None:
    profile = UserProfile(
        user_id="wc",
        name="Wheelchair",
        category_weights={"neighbourhood": 1.0},
        accessibility=AccessibilityRequirements(require_wheelchair=True),
    )
    plan = await plan_initial(
        profile=profile,
        index=singapore_index,
        start_time=datetime(2026, 5, 2, 10, 0),
        budget_minutes=300,
        llm=stub_llm,
    )
    for v in plan.visits:
        poi = singapore_index.pois[v.poi_id]
        assert poi.wheelchair_accessible


@pytest.mark.asyncio
async def test_dietary_filter_requires_all_listed_options(stub_llm, singapore_index) -> None:
    profile = UserProfile(
        user_id="vh",
        name="Vegan+Halal",
        category_weights={"food": 0.5, "neighbourhood": 0.5},
        accessibility=AccessibilityRequirements(dietary=("vegan", "halal")),
    )
    plan = await plan_initial(
        profile=profile,
        index=singapore_index,
        start_time=datetime(2026, 5, 2, 10, 0),
        budget_minutes=400,
        llm=stub_llm,
    )
    for v in plan.visits:
        poi = singapore_index.pois[v.poi_id]
        assert "vegan" in poi.dietary_options and "halal" in poi.dietary_options


# ----- per-visit reasoning ------------------------------------------


@pytest.mark.asyncio
async def test_visits_carry_structured_reasoning(stub_llm, singapore_index) -> None:
    profile = UserProfile(user_id="r", name="Reasoning", category_weights={"park": 1.0})
    plan = await plan_initial(
        profile=profile,
        index=singapore_index,
        start_time=datetime(2026, 5, 2, 9, 0),
        budget_minutes=600,
        llm=stub_llm,
    )
    assert plan.visits
    first = plan.visits[0]
    # Numeric trace stays machine-readable.
    assert first.reasoning_scores
    assert "score=" in first.reasoning_scores
    # Human-readable rationale is a separate field shown inline in the UI.
    assert first.reasoning_text
    assert any(word in first.reasoning_text.lower() for word in ("stop", "fit", "slot"))
    # Alternatives_considered may be empty for the last pick but should
    # be populated for the first when several candidates exist.
    assert isinstance(first.alternatives_considered, tuple)


# ----- group dynamics -----------------------------------------------


def test_group_aggregation_averages_weights() -> None:
    profile = UserProfile(
        user_id="g",
        name="Group",
        category_weights={},
        group_members=(
            GroupMember("m1", "Alice", {"museum": 1.0, "park": 0.0}),
            GroupMember("m2", "Bob", {"museum": 0.0, "park": 1.0}),
        ),
    )
    weights = _group_aggregated_weights(profile)
    # Equal voice: both museum and park get 0.5 average.
    assert weights["museum"] == pytest.approx(0.5)
    assert weights["park"] == pytest.approx(0.5)


def test_group_veto_drops_category() -> None:
    profile = UserProfile(
        user_id="g",
        name="Group",
        category_weights={},
        group_members=(
            GroupMember("m1", "A", {"museum": 1.0, "zoo": 0.5}, veto_categories=("zoo",)),
            GroupMember("m2", "B", {"museum": 0.5, "zoo": 1.0}),
        ),
    )
    weights = _group_aggregated_weights(profile)
    assert "zoo" not in weights  # vetoed
    assert weights["museum"] > 0


def test_group_boost_amplifies_category() -> None:
    plain = UserProfile(
        user_id="g1",
        name="Plain",
        category_weights={},
        group_members=(
            GroupMember("m1", "A", {"food": 1.0}),
            GroupMember("m2", "B", {"food": 1.0}),
        ),
    )
    boosted = UserProfile(
        user_id="g2",
        name="Boosted",
        category_weights={},
        group_members=(
            GroupMember("m1", "A", {"food": 1.0}, boost_categories=("food",)),
            GroupMember("m2", "B", {"food": 1.0}),
        ),
    )
    assert _group_aggregated_weights(boosted)["food"] > _group_aggregated_weights(plain)["food"]


# ----- booking actuation --------------------------------------------


def test_booking_actuator_dry_run_confirms(tmp_path: Path) -> None:
    actuator = BookingActuator(audit_log_path=tmp_path / "bookings.jsonl", dry_run=True)
    poi = POI("sg04", "Singapore Zoo", "zoo", 1.40, 103.79, 180, 0.92, entry_fee_usd=39.0)
    from adaptivetouragent.itinerary.types import POIVisit

    visit = POIVisit(
        poi_id=poi.poi_id,
        name=poi.name,
        arrive=datetime(2026, 6, 1, 10, 0),
        depart=datetime(2026, 6, 1, 13, 0),
        category=poi.category,
        entry_fee_usd=poi.entry_fee_usd * 2,
    )
    record = actuator.book_poi_visit(visit, party_size=2)
    assert record.status == "confirmed"
    assert record.confirmation_code and record.confirmation_code.startswith("DRYRUN-")
    assert record.amount_usd == 78.0  # 39 * 2

    # Audit log written.
    log_text = (tmp_path / "bookings.jsonl").read_text()
    assert record.booking_id in log_text


def test_booking_actuator_caps_per_session(tmp_path: Path) -> None:
    actuator = BookingActuator(
        audit_log_path=tmp_path / "bookings.jsonl",
        dry_run=True,
        max_bookings_per_session=2,
    )
    acc = Accommodation("acc01", "Test", 1.30, 103.85, 100, 4.0, True, True)
    for _ in range(2):
        r = actuator.book_accommodation(acc, datetime(2026, 6, 1), nights=1, party_size=1)
        assert r.status == "confirmed"
    blocked = actuator.book_accommodation(acc, datetime(2026, 6, 1), nights=1, party_size=1)
    assert blocked.status == "failed"
    assert "rate_limit" in blocked.notes


# ----- sustainability / carbon --------------------------------------


def test_co2e_table_is_consistent_with_modes() -> None:
    assert CO2E_KG_PER_KM["walk"] == 0.0
    assert 0 < CO2E_KG_PER_KM["transit"] < CO2E_KG_PER_KM["drive"]


def test_travel_co2e_zero_for_short_walk() -> None:
    a = POI("a", "A", "x", 1.30, 103.85, 30, 0.5)
    b = POI("b", "B", "x", 1.301, 103.851, 30, 0.5)
    assert travel_co2e_kg(a, b) == 0.0


@pytest.mark.asyncio
async def test_prefer_low_carbon_changes_visit_co2e(stub_llm, singapore_index) -> None:
    """Plans built with prefer_low_carbon=True should record per-leg CO2e."""
    profile = UserProfile(user_id="green", name="Green", category_weights={"park": 0.5, "viewpoint": 0.5})
    plan = await plan_initial(
        profile=profile,
        index=singapore_index,
        start_time=datetime(2026, 5, 2, 9, 0),
        budget_minutes=600,
        llm=stub_llm,
        prefer_low_carbon=True,
    )
    # Sum of per-visit CO2e equals plan.total_co2e_kg.
    visit_sum = sum(v.travel_co2e_kg for v in plan.visits)
    assert plan.total_co2e_kg == pytest.approx(visit_sum)
