"""Routing math sanity checks."""

import pytest

from adaptivetouragent.itinerary.routing import (
    WALK_ONLY_KM,
    WALK_PREFERRED_KM,
    build_cost_matrix,
    compute_leg,
    haversine_km,
    pick_mode,
    travel_time_min,
)
from adaptivetouragent.itinerary.types import POI


def _poi(pid: str, lat: float, lon: float) -> POI:
    return POI(
        poi_id=pid,
        name=pid,
        category="x",
        lat=lat,
        lon=lon,
        avg_duration_min=30.0,
        popularity=0.5,
    )


def test_haversine_known_distance() -> None:
    # Singapore Marina Bay Sands -> Gardens by the Bay is ~1 km
    distance = haversine_km(1.2834, 103.8607, 1.2816, 103.8636)
    assert 0.2 < distance < 1.0


def test_travel_time_walk_faster_than_zero() -> None:
    a = _poi("a", 1.30, 103.85)
    b = _poi("b", 1.31, 103.86)
    walk = travel_time_min(a, b, "walk")
    transit = travel_time_min(a, b, "transit")
    assert walk > 0
    assert transit < walk  # transit is faster


def test_cost_matrix_excludes_self_loops() -> None:
    pois = [_poi("a", 1.30, 103.85), _poi("b", 1.31, 103.86), _poi("c", 1.32, 103.84)]
    matrix = build_cost_matrix(pois)
    assert ("a", "a") not in matrix
    assert ("a", "b") in matrix
    assert ("b", "a") in matrix
    assert len(matrix) == 6  # 3 * 2 directed pairs


def test_haversine_zero_for_identical_points() -> None:
    assert haversine_km(1.3, 103.8, 1.3, 103.8) == pytest.approx(0.0)


def test_pick_mode_walk_below_threshold() -> None:
    assert pick_mode(0.0) == "walk"
    assert pick_mode(WALK_ONLY_KM - 0.01) == "walk"


def test_pick_mode_default_above_walk_only_is_transit() -> None:
    # Without a budget signal, default fallback is transit.
    assert pick_mode(WALK_ONLY_KM) == "transit"
    assert pick_mode(5.0) == "transit"
    assert pick_mode(50.0) == "transit"


def test_pick_mode_low_carbon_picks_cycle_for_medium() -> None:
    # 2.5 km with low-carbon preference should cycle (under the 4 km cap).
    assert pick_mode(2.5, prefer_low_carbon=True) == "cycle"
    # Sub-1.5 km low-carbon prefers walk.
    assert pick_mode(WALK_PREFERRED_KM - 0.1, prefer_low_carbon=True) == "walk"
    # Above cycle range falls back to transit.
    assert pick_mode(10.0, prefer_low_carbon=True) == "transit"


def test_pick_mode_picks_rideshare_when_budget_is_comfortable() -> None:
    # 3 km hop, party of 2: rideshare cost ~$3.50 + 3*1.30*0.95 = ~$7.21.
    # Comfortable budget threshold is 3x = ~$22; pass $50 to trigger rideshare.
    assert pick_mode(3.0, party_size=2, remaining_budget_usd=50.0) == "rideshare"


def test_pick_mode_skips_rideshare_when_budget_tight() -> None:
    # Same 3 km hop, but only $5 remaining, far too tight for rideshare.
    assert pick_mode(3.0, party_size=2, remaining_budget_usd=5.0) == "transit"


def test_default_mode_picks_walk_for_close_pois() -> None:
    a = _poi("mbs", 1.2834, 103.8607)
    b = _poi("gbb", 1.2816, 103.8636)
    auto = travel_time_min(a, b)
    walk_explicit = travel_time_min(a, b, "walk")
    assert auto == pytest.approx(walk_explicit)


def test_default_mode_picks_transit_for_far_pois() -> None:
    # Gardens by the Bay -> Botanic Gardens is ~6 km; default picks transit.
    a = _poi("gbb", 1.2816, 103.8636)
    b = _poi("bg", 1.3138, 103.8159)
    auto = travel_time_min(a, b)
    walk_explicit = travel_time_min(a, b, "walk")
    transit_explicit = travel_time_min(a, b, "transit")
    assert auto == pytest.approx(transit_explicit)
    assert auto < walk_explicit  # transit is much faster than walking


def test_cost_matrix_uses_per_edge_mode_by_default() -> None:
    close_a = _poi("close_a", 1.300, 103.850)
    close_b = _poi("close_b", 1.301, 103.851)  # ~0.15 km away
    far = _poi("far", 1.404, 103.793)  # ~13 km from the city center
    matrix = build_cost_matrix([close_a, close_b, far])

    walk_close = travel_time_min(close_a, close_b, "walk")
    assert matrix[("close_a", "close_b")] == pytest.approx(walk_close)

    transit_far = travel_time_min(close_a, far, "transit")
    walk_far = travel_time_min(close_a, far, "walk")
    assert matrix[("close_a", "far")] == pytest.approx(transit_far)
    assert matrix[("close_a", "far")] < walk_far


def test_compute_leg_returns_chosen_mode_and_circuity() -> None:
    """compute_leg surfaces the chosen mode and the circuity-corrected distance."""
    a = _poi("a", 1.30, 103.85)
    b = _poi("b", 1.32, 103.85)  # ~2.2 km north
    leg = compute_leg(a, b)
    assert leg.mode == "transit"
    # Circuity factor for transit is 1.45; realistic distance is larger.
    assert leg.realistic_distance_km > leg.raw_distance_km
    # Wait time adds at least 6 minutes for transit on top of in-vehicle time.
    assert leg.duration_min > (leg.realistic_distance_km / 22.0) * 60.0


def test_compute_leg_low_carbon_prefers_cycle() -> None:
    a = _poi("a", 1.30, 103.85)
    b = _poi("b", 1.32, 103.85)
    leg = compute_leg(a, b, prefer_low_carbon=True)
    assert leg.mode == "cycle"
    assert leg.co2e_kg == 0.0  # cycling is zero-emission


def test_compute_leg_rideshare_charges_per_booking_not_per_passenger() -> None:
    """Rideshare cost is per-booking; per-passenger cost shrinks with party size."""
    a = _poi("a", 1.30, 103.85)
    b = _poi("b", 1.34, 103.85)  # ~4.4 km
    solo = compute_leg(a, b, mode="rideshare", party_size=1)
    quartet = compute_leg(a, b, mode="rideshare", party_size=4)
    # Per-passenger fare for the quartet is roughly 1/4 of the solo fare.
    assert quartet.cost_usd == pytest.approx(solo.cost_usd / 4, rel=0.05)


def test_compute_leg_transit_fare_is_per_passenger() -> None:
    """Transit, walk, cycle prices stay constant per-passenger regardless of party size."""
    a = _poi("a", 1.30, 103.85)
    b = _poi("b", 1.32, 103.85)
    solo = compute_leg(a, b, mode="transit", party_size=1)
    quartet = compute_leg(a, b, mode="transit", party_size=4)
    assert solo.cost_usd == pytest.approx(quartet.cost_usd)


# --- Per-city fare overrides ----------------------------------------------


def test_per_city_transit_fares_differ() -> None:
    """Singapore MRT, London Tube, Paris métro should produce distinct fares
    for the same physical hop. Cheapest → most expensive: SG < Melbourne <
    London/NYC < Paris."""
    a = _poi("a", 1.30, 103.85)
    b = _poi("b", 1.32, 103.85)  # ~2 km
    sg = compute_leg(a, b, mode="transit", city="Singapore")
    mel = compute_leg(a, b, mode="transit", city="Melbourne")
    lon = compute_leg(a, b, mode="transit", city="London")
    par = compute_leg(a, b, mode="transit", city="Paris")
    assert sg.cost_usd < mel.cost_usd < lon.cost_usd < par.cost_usd
    # The CO2e factor is global, so all should be equal.
    assert sg.co2e_kg == pytest.approx(mel.co2e_kg)
    assert lon.co2e_kg == pytest.approx(par.co2e_kg)


def test_per_city_rideshare_nyc_more_expensive_than_singapore() -> None:
    """NYC rideshare per-km (~$2.20) should price ~2× the Singapore baseline."""
    a = _poi("a", 1.30, 103.85)
    b = _poi("b", 1.34, 103.85)  # ~4.4 km
    sg = compute_leg(a, b, mode="rideshare", party_size=2, city="Singapore")
    nyc = compute_leg(a, b, mode="rideshare", party_size=2, city="New York")
    assert nyc.cost_usd > sg.cost_usd * 1.5


def test_unknown_city_falls_back_to_global_defaults() -> None:
    """A city without an override entry should still produce a sensible leg."""
    a = _poi("a", 1.30, 103.85)
    b = _poi("b", 1.32, 103.85)
    fallback = compute_leg(a, b, mode="transit", city="UnknownCity")
    baseline = compute_leg(a, b, mode="transit")  # no city param
    assert fallback.cost_usd == pytest.approx(baseline.cost_usd)


# --- Wheelchair routing ----------------------------------------------------


def test_wheelchair_never_picks_cycle() -> None:
    """`require_wheelchair=True` should disable cycle even when low-carbon
    preference would otherwise pick it (cycle range = 0.5-4 km hops)."""
    for distance_km in (0.6, 1.0, 2.0, 3.0, 4.0):
        mode = pick_mode(
            distance_km,
            party_size=2,
            remaining_budget_usd=200.0,
            prefer_low_carbon=True,
            require_wheelchair=True,
        )
        assert mode != "cycle", f"wheelchair flag did not block cycle at {distance_km}km"


def test_wheelchair_prefers_transit_over_long_walk() -> None:
    """Beyond 1 km, wheelchair users should be routed onto transit rather
    than asked to walk."""
    # Distance where the default pick would still be walking under low-carbon.
    mode = pick_mode(
        1.2,
        party_size=1,
        remaining_budget_usd=None,
        require_wheelchair=True,
    )
    assert mode == "transit"


def test_wheelchair_still_allows_rideshare_with_budget() -> None:
    """Rideshare stays a valid wheelchair-compatible option when budget allows."""
    mode = pick_mode(
        3.0,
        party_size=1,
        remaining_budget_usd=500.0,
        require_wheelchair=True,
    )
    assert mode == "rideshare"
