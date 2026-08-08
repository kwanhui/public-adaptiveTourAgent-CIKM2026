"""Smoke tests: every supported city loads, plans, and produces a valid itinerary.

These tests guard the cross-city promise made in the demo paper. If a new
city is added to `data/cities/`, listing it here adds full-pipeline coverage
without rewriting the harness.
"""

from datetime import datetime

import pytest

from adaptivetouragent.accommodations.index import load_accommodations
from adaptivetouragent.agent.types import UserProfile
from adaptivetouragent.replanner.initial import plan_initial
from adaptivetouragent.retrieval.poi_index import load_city

SUPPORTED_CITIES = ["Singapore", "Melbourne", "London", "New York", "Paris"]


@pytest.mark.parametrize("city", SUPPORTED_CITIES)
def test_city_pois_load(city: str) -> None:
    """Each catalogue parses + has at least 10 POIs."""
    index = load_city(city)
    assert len(index) >= 10, f"{city} has only {len(index)} POIs"
    # Every POI must carry the accessibility schema fields populated.
    for poi in index.pois.values():
        assert isinstance(poi.wheelchair_accessible, bool)
        assert isinstance(poi.sensory_low_stimulation, bool)
        assert isinstance(poi.dietary_options, tuple)


@pytest.mark.parametrize("city", SUPPORTED_CITIES)
def test_city_accommodations_load(city: str) -> None:
    """Each city has at least 5 accommodations spanning a price range."""
    accs = load_accommodations(city)
    assert len(accs) >= 5, f"{city} has only {len(accs)} accommodations"
    prices = [a.price_per_night_usd for a in accs]
    # Sanity: at least one budget + one upscale entry; the matcher tests rely
    # on a real spread.
    assert min(prices) < 200, f"{city} has no budget accommodation under $200"
    assert max(prices) > 200, f"{city} has no upscale accommodation over $200"


@pytest.mark.parametrize("city", SUPPORTED_CITIES)
async def test_city_plan_smoke(city: str, stub_llm) -> None:
    """End-to-end planning works for every city with a generic profile."""
    index = load_city(city)
    profile = UserProfile(
        user_id="t",
        name="Tourist",
        family_size=2,
        category_weights={"museum": 0.3, "park": 0.2, "viewpoint": 0.2, "heritage": 0.15, "food": 0.15},
    )
    plan = await plan_initial(
        profile=profile,
        index=index,
        start_time=datetime(2026, 6, 1, 9, 0),
        budget_minutes=480,
        llm=stub_llm,
    )
    assert plan.visits, f"{city} produced an empty plan"
    # The reasoning split must populate both fields for every visit.
    for v in plan.visits:
        assert v.reasoning_text, f"{city} visit {v.poi_id} missing reasoning_text"
        assert v.reasoning_scores, f"{city} visit {v.poi_id} missing reasoning_scores"
        assert "score=" in v.reasoning_scores
