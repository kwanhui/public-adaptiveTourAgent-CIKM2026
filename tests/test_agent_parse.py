"""Edge cases in the agent's JSON parser."""

from adaptivetouragent.agent.loop import _parse_scores


def test_parse_scores_strips_code_fences() -> None:
    raw = """```json
{"scores": {"POI_1": 0.85, "POI_2": 0.42}}
```"""
    out = _parse_scores(raw, valid_ids={"1", "2"})
    assert out == {"1": 0.85, "2": 0.42}


def test_parse_scores_clamps_to_unit_interval() -> None:
    raw = '{"scores": {"POI_1": 1.5, "POI_2": -0.4}}'
    out = _parse_scores(raw, valid_ids={"1", "2"})
    assert out == {"1": 1.0, "2": 0.0}


def test_parse_scores_drops_unknown_ids() -> None:
    raw = '{"scores": {"POI_1": 0.5, "POI_99": 0.9}}'
    out = _parse_scores(raw, valid_ids={"1"})
    assert out == {"1": 0.5}


def test_parse_scores_handles_malformed_json() -> None:
    raw = "not json at all"
    out = _parse_scores(raw, valid_ids={"1"})
    assert out == {}


def test_parse_scores_handles_top_level_dict() -> None:
    # Some LLMs forget the "scores" wrapper key.
    raw = '{"POI_1": 0.5, "POI_2": 0.7}'
    out = _parse_scores(raw, valid_ids={"1", "2"})
    assert out == {"1": 0.5, "2": 0.7}


def test_parse_scores_handles_non_numeric_values() -> None:
    raw = '{"scores": {"POI_1": "high", "POI_2": 0.5}}'
    out = _parse_scores(raw, valid_ids={"1", "2"})
    assert out == {"2": 0.5}
