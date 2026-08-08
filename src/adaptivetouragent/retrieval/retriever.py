"""POI relevance scoring + structured filters.

Alpha-weighted
category alignment + popularity. The user-similarity branch is dropped
(the demo is single-user) and structured filters (open-now, kid-friendly,
indoor) are layered on top.
"""

from adaptivetouragent.itinerary.types import POI
from adaptivetouragent.retrieval.poi_index import POIIndex


def _poi_relevance(
    poi: POI,
    category_weights: dict[str, float],
    max_popularity: float,
    alpha: float = 0.7,
) -> float:
    """Alpha-weighted category alignment + normalised popularity."""
    weight_sum = sum(category_weights.values())
    category_score = (category_weights.get(poi.category, 0.0) / weight_sum) if weight_sum > 0 else 0.0
    popularity_score = (poi.popularity / max_popularity) if max_popularity > 0 else 0.0
    return alpha * category_score + (1 - alpha) * popularity_score


def retrieve_candidates(
    index: POIIndex,
    category_weights: dict[str, float],
    *,
    top_k: int = 12,
    alpha: float = 0.7,
    require_indoor: bool | None = None,
    require_kid_friendly: bool | None = None,
    open_at_hour: int | None = None,
    exclude: set[str] | None = None,
    require_wheelchair: bool | None = None,
    require_dietary: tuple[str, ...] = (),
    require_low_stimulation: bool | None = None,
) -> list[POI]:
    """Top-k POIs ranked by relevance, after applying structured filters."""
    excluded = exclude or set()

    pool = index.filter(
        indoor=require_indoor,
        kid_friendly=require_kid_friendly,
        open_at_hour=open_at_hour,
        require_wheelchair=require_wheelchair,
        require_dietary=require_dietary,
        require_low_stimulation=require_low_stimulation,
    )

    scored: list[tuple[float, POI]] = []
    for poi in pool:
        if poi.poi_id in excluded:
            continue
        score = _poi_relevance(poi, category_weights, index.max_popularity, alpha)
        scored.append((score, poi))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [poi for _, poi in scored[:top_k]]
