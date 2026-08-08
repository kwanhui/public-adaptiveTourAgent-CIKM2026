"""LLM scoring loop using a scoring-plus-greedy hybrid pattern.

The agent scores POIs in one batched LLM call. Routing and budget enforcement
stay in `itinerary.optimizer`; the LLM does not see travel time or the time
budget directly. This is the "scoring + greedy" hybrid that the reused work
established as the right division of labour.

The agent is a single-pass scorer. A later stage wraps it in a verify-and-revise
rectifier (see `agent.rectifier`) and threads it into the replan loop.
"""

import json
import logging

from adaptivetouragent.agent.prompts import build_score_prompt, profile_summary
from adaptivetouragent.agent.types import AgentRunStats, UserProfile
from adaptivetouragent.itinerary.types import POI
from adaptivetouragent.llm.provider import LLMProvider

logger = logging.getLogger(__name__)


def _parse_scores(raw: str, valid_ids: set[str]) -> dict[str, float]:
    """Pull a `{"scores": {"POI_<id>": float, ...}}` payload out of an LLM response."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(line for line in lines if not line.strip().startswith("```"))

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("agent: failed to parse JSON from LLM response")
        return {}

    raw_scores = data.get("scores", data) if isinstance(data, dict) else {}
    if not isinstance(raw_scores, dict):
        return {}

    out: dict[str, float] = {}
    for key, val in raw_scores.items():
        pid = str(key).removeprefix("POI_").strip()
        if pid not in valid_ids:
            continue
        try:
            out[pid] = max(0.0, min(1.0, float(val)))
        except (TypeError, ValueError):
            continue
    return out


async def score_pois(
    *,
    profile: UserProfile,
    candidates: list[POI],
    llm: LLMProvider,
    context_notes: str = "",
    stats: AgentRunStats | None = None,
) -> dict[str, float]:
    """Single-pass POI scoring. Returns scores keyed by `poi_id`.

    POIs the LLM does not return get a fallback score equal to their
    normalised popularity weighted by category alignment; this prevents
    the optimiser from seeing zero-score holes when the LLM truncates.
    """
    summary = profile_summary(profile.aggregated_weights(), profile.notes)
    messages = build_score_prompt(
        profile_summary=summary,
        candidates=candidates,
        context_notes=context_notes,
    )

    valid_ids = {p.poi_id for p in candidates}
    response = await llm.complete(messages, temperature=0.0, max_tokens=2048)
    if stats is not None:
        stats.llm_calls += 1

    scores = _parse_scores(response.content or "", valid_ids)

    # Fallback: any POI not scored by the LLM gets a deterministic default.
    weights = profile.aggregated_weights()
    for poi in candidates:
        if poi.poi_id in scores:
            continue
        category_score = weights.get(poi.category, 0.0)
        scores[poi.poi_id] = 0.5 * category_score + 0.5 * poi.popularity

    return scores
