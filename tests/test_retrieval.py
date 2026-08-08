"""Retrieval module tests against the bundled Singapore POI catalogue."""

from adaptivetouragent.retrieval.retriever import retrieve_candidates


def test_index_loads(singapore_index) -> None:
    assert singapore_index.city == "Singapore"
    assert len(singapore_index) >= 10
    # Cost matrix is fully populated.
    n = len(singapore_index)
    assert len(singapore_index.cost_matrix) == n * (n - 1)


def test_retrieve_respects_top_k(singapore_index) -> None:
    weights = {"park": 1.0}
    result = retrieve_candidates(singapore_index, category_weights=weights, top_k=5)
    assert len(result) == 5


def test_retrieve_prefers_matching_category(singapore_index) -> None:
    # With heavy zoo weighting, the top result should be the zoo (sg04).
    weights = {"zoo": 1.0}
    result = retrieve_candidates(singapore_index, category_weights=weights, top_k=3)
    assert any(p.poi_id == "sg04" for p in result)


def test_kid_friendly_filter(singapore_index) -> None:
    weights = {"theme_park": 1.0}
    only_kid_friendly = retrieve_candidates(
        singapore_index,
        category_weights=weights,
        require_kid_friendly=True,
        top_k=20,
    )
    for p in only_kid_friendly:
        assert p.kid_friendly is True


def test_indoor_filter_yields_indoor_only(singapore_index) -> None:
    weights = {"museum": 1.0}
    indoor = retrieve_candidates(
        singapore_index,
        category_weights=weights,
        require_indoor=True,
        top_k=20,
    )
    for p in indoor:
        assert p.indoor is True


def test_open_at_hour_filter(singapore_index) -> None:
    weights = {"museum": 1.0}
    open_at_22 = retrieve_candidates(
        singapore_index,
        category_weights=weights,
        open_at_hour=22,
        top_k=20,
    )
    # Most museums close by 19:00, so the 22:00 filter should be sparse.
    assert all(p.open_hours[1] > 22 for p in open_at_22)
