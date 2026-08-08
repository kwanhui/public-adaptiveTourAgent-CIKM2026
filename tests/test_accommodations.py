"""Accommodation catalogue, filter, and LLM matcher tests."""

import pytest

from adaptivetouragent.accommodations.agent import pick_accommodation, score_accommodations
from adaptivetouragent.accommodations.index import (
    filter_by_hard_constraints,
    load_accommodations,
)
from adaptivetouragent.accommodations.types import Accommodation, AccommodationRequest
from adaptivetouragent.agent.types import UserProfile


def _profile() -> UserProfile:
    return UserProfile(
        user_id="t",
        name="T",
        category_weights={"park": 0.4, "museum": 0.3, "viewpoint": 0.3},
        family_size=4,
        require_kid_friendly=True,
    )


def test_load_accommodations_singapore() -> None:
    acc = load_accommodations("Singapore")
    assert len(acc) >= 8
    # Spot-check a known entry.
    names = {a.name for a in acc}
    assert "Marina Bay Sands Hotel" in names


def test_filter_by_max_price() -> None:
    acc = load_accommodations("Singapore")
    filtered = filter_by_hard_constraints(
        acc, AccommodationRequest(max_price_per_night_usd=100.0)
    )
    assert filtered  # at least one budget option exists
    for a in filtered:
        assert a.price_per_night_usd <= 100.0


def test_filter_by_kid_friendly() -> None:
    acc = load_accommodations("Singapore")
    filtered = filter_by_hard_constraints(
        acc, AccommodationRequest(require_kid_friendly=True)
    )
    for a in filtered:
        assert a.kid_friendly


def test_filter_by_near_mrt() -> None:
    acc = load_accommodations("Singapore")
    filtered = filter_by_hard_constraints(
        acc, AccommodationRequest(require_near_mrt=True)
    )
    for a in filtered:
        assert a.near_mrt


def test_filter_by_proximity() -> None:
    acc = load_accommodations("Singapore")
    # Center on Marina Bay; 1 km radius should be tight enough to drop most entries.
    filtered = filter_by_hard_constraints(
        acc,
        AccommodationRequest(near_lat=1.2834, near_lon=103.8607, near_radius_km=1.0),
    )
    assert len(filtered) < len(acc)
    # Marina Bay Sands itself should be in the result.
    assert any(a.name == "Marina Bay Sands Hotel" for a in filtered)


def test_filter_returns_empty_when_constraints_impossible() -> None:
    acc = load_accommodations("Singapore")
    impossible = AccommodationRequest(
        max_price_per_night_usd=10.0, min_rating=4.9, require_kid_friendly=True
    )
    assert filter_by_hard_constraints(acc, impossible) == []


@pytest.mark.asyncio
async def test_score_accommodations_returns_score_per_candidate(stub_llm) -> None:
    acc = load_accommodations("Singapore")
    request = AccommodationRequest(max_price_per_night_usd=300, require_kid_friendly=True)
    candidates = filter_by_hard_constraints(acc, request)
    assert candidates

    scores = await score_accommodations(
        candidates=candidates, request=request, profile=_profile(), llm=stub_llm
    )
    assert set(scores.keys()) == {a.accommodation_id for a in candidates}
    for s in scores.values():
        assert 0.0 <= s <= 1.0


@pytest.mark.asyncio
async def test_pick_accommodation_returns_choice(stub_llm) -> None:
    acc = load_accommodations("Singapore")
    request = AccommodationRequest(max_price_per_night_usd=200, min_rating=4.0)
    candidates = filter_by_hard_constraints(acc, request)
    assert candidates

    choice = await pick_accommodation(
        candidates=candidates, request=request, profile=_profile(), llm=stub_llm
    )
    assert choice is not None
    assert choice.accommodation in candidates
    assert 0.0 <= choice.score <= 1.0
    assert choice.rationale  # non-empty


@pytest.mark.asyncio
async def test_pick_accommodation_handles_empty_candidates(stub_llm) -> None:
    choice = await pick_accommodation(
        candidates=[], request=AccommodationRequest(), profile=_profile(), llm=stub_llm
    )
    assert choice is None


def test_amenities_filter() -> None:
    """Required amenities must be a subset of the candidate's amenities."""
    pool_required = AccommodationRequest(amenities=("pool", "breakfast"))
    acc = load_accommodations("Singapore")
    filtered = filter_by_hard_constraints(acc, pool_required)
    for a in filtered:
        assert "pool" in a.amenities
        assert "breakfast" in a.amenities


def test_synthetic_accommodation_filter_contract() -> None:
    """Spot-check the filter using a synthesised accommodation list."""
    a1 = Accommodation("a1", "Cheap", 1.30, 103.85, 50, 3.5, True, True)
    a2 = Accommodation("a2", "Mid", 1.30, 103.85, 200, 4.5, True, True)
    a3 = Accommodation("a3", "Lux", 1.30, 103.85, 500, 4.9, False, True)

    request = AccommodationRequest(
        max_price_per_night_usd=300, min_rating=4.0, require_kid_friendly=True
    )
    filtered = filter_by_hard_constraints([a1, a2, a3], request)
    assert filtered == [a2]
