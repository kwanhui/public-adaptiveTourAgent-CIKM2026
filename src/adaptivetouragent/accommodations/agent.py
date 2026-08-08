"""LLM-driven accommodation matcher.

Mirrors `agent.score_pois`: one batched LLM call assigns 0..1 scores to
candidates given the user profile + a stringified requirements summary.
"""

import json
import logging

from adaptivetouragent.accommodations.types import (
    Accommodation,
    AccommodationChoice,
    AccommodationRequest,
)
from adaptivetouragent.agent.prompts import profile_summary as render_profile_summary
from adaptivetouragent.agent.types import AgentRunStats, UserProfile
from adaptivetouragent.llm.provider import LLMProvider

logger = logging.getLogger(__name__)


_SCORE_SYSTEM = (
    "You are an accommodation matcher for a tour planner. "
    "Score each accommodation from 0.0 to 1.0 based on how well it matches the "
    "tourist's profile and stated requirements. "
    "Respond with JSON only: no commentary, no markdown code fences."
)


def _requirements_summary(request: AccommodationRequest) -> str:
    parts: list[str] = []
    if request.max_price_per_night_usd is not None:
        parts.append(f"budget: <=${request.max_price_per_night_usd:.0f}/night")
    if request.min_rating > 0:
        parts.append(f"min rating: {request.min_rating:.1f}/5")
    if request.require_kid_friendly:
        parts.append("must be kid-friendly")
    if request.require_near_mrt:
        parts.append("must be near MRT")
    if request.near_lat is not None and request.near_lon is not None:
        parts.append(
            f"near ({request.near_lat:.4f}, {request.near_lon:.4f}) within {request.near_radius_km:.1f} km"
        )
    if request.amenities:
        parts.append("required amenities: " + ", ".join(request.amenities))
    if request.notes:
        parts.append(f"notes: {request.notes}")
    return "; ".join(parts) if parts else "(no hard requirements stated)"


def _build_prompt(
    profile_text: str,
    request: AccommodationRequest,
    candidates: list[Accommodation],
) -> list[dict[str, str]]:
    lines = []
    for a in candidates:
        amen = ",".join(a.amenities) if a.amenities else "-"
        lines.append(
            f"ACC_{a.accommodation_id}: {a.name} | ${a.price_per_night_usd:.0f}/night, "
            f"rating {a.rating:.1f}/5, kid_friendly={a.kid_friendly}, near_mrt={a.near_mrt}, "
            f"amenities=[{amen}] | {a.description}"
        )

    user = (
        f"TOURIST PROFILE:\n{profile_text}\n\n"
        f"REQUIREMENTS: {_requirements_summary(request)}\n\n"
        "AVAILABLE ACCOMMODATIONS:\n" + "\n".join(lines) + "\n\n"
        "Score each accommodation from 0.0 (poor fit) to 1.0 (perfect fit) for this tourist. "
        "Weight rating, value-for-money, fit to the tourist's stated interests, and proximity. "
        "Return a JSON object exactly in this shape:\n"
        '{"scores": {"ACC_<id>": <float>, ...}}'
    )
    return [
        {"role": "system", "content": _SCORE_SYSTEM},
        {"role": "user", "content": user},
    ]


def _parse_scores(raw: str, valid_ids: set[str]) -> dict[str, float]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(line for line in lines if not line.strip().startswith("```"))

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("accommodations.agent: failed to parse JSON from LLM response")
        return {}

    raw_scores = data.get("scores", data) if isinstance(data, dict) else {}
    if not isinstance(raw_scores, dict):
        return {}

    out: dict[str, float] = {}
    for key, val in raw_scores.items():
        aid = str(key).removeprefix("ACC_").strip()
        if aid not in valid_ids:
            continue
        try:
            out[aid] = max(0.0, min(1.0, float(val)))
        except (TypeError, ValueError):
            continue
    return out


async def score_accommodations(
    *,
    candidates: list[Accommodation],
    request: AccommodationRequest,
    profile: UserProfile,
    llm: LLMProvider,
    stats: AgentRunStats | None = None,
) -> dict[str, float]:
    """Single-pass LLM scoring of accommodation candidates."""
    if not candidates:
        return {}

    profile_text = render_profile_summary(profile.normalised_weights(), profile.notes)
    messages = _build_prompt(profile_text, request, candidates)

    response = await llm.complete(messages, temperature=0.0, max_tokens=1024)
    if stats is not None:
        stats.llm_calls += 1

    valid_ids = {a.accommodation_id for a in candidates}
    scores = _parse_scores(response.content or "", valid_ids)

    # Fallback: deterministic default for any unscored entry (rating + value-for-money).
    budget = request.max_price_per_night_usd or (max(a.price_per_night_usd for a in candidates) or 1.0)
    for a in candidates:
        if a.accommodation_id in scores:
            continue
        rating_part = a.rating / 5.0
        price_part = max(0.0, 1.0 - a.price_per_night_usd / max(budget, 1.0))
        scores[a.accommodation_id] = round(0.6 * rating_part + 0.4 * price_part, 3)

    return scores


async def pick_accommodation(
    *,
    candidates: list[Accommodation],
    request: AccommodationRequest,
    profile: UserProfile,
    llm: LLMProvider,
    stats: AgentRunStats | None = None,
) -> AccommodationChoice | None:
    """Score + pick the top-1 accommodation. Returns None if `candidates` is empty."""
    if not candidates:
        return None

    scores = await score_accommodations(
        candidates=candidates, request=request, profile=profile, llm=llm, stats=stats
    )

    ranked = sorted(candidates, key=lambda a: scores.get(a.accommodation_id, 0.0), reverse=True)
    chosen = ranked[0]
    chosen_score = scores.get(chosen.accommodation_id, 0.0)
    rationale = (
        f"Selected {chosen.name} (rating {chosen.rating:.1f}/5, "
        f"${chosen.price_per_night_usd:.0f}/night, "
        f"{'near MRT' if chosen.near_mrt else 'not near MRT'}, "
        f"{'kid-friendly' if chosen.kid_friendly else 'adults-focused'}). "
        f"LLM match score {chosen_score:.2f} of 1.00."
    )

    cost = 0.0
    summary = llm.get_usage_summary()
    if isinstance(summary, dict):
        cost = float(summary.get("estimated_cost_usd", 0.0))

    return AccommodationChoice(
        accommodation=chosen,
        score=chosen_score,
        rationale=rationale,
        cost_usd=cost,
    )
