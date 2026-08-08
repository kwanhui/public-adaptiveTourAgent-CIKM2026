"""Prompt assembly.

System prompts are short and single-purpose; user prompts carry all the
task data. Keep prompts deterministic; they are covered by the tests.
"""

from adaptivetouragent.itinerary.types import POI

SCORE_SYSTEM = (
    "You are a Points-of-Interest scoring assistant for a tour planner. "
    "Score POIs from 0.0 to 1.0 by how well they match the tourist's profile. "
    "Respond with JSON only: no commentary, no markdown code fences."
)


def build_score_prompt(
    *,
    profile_summary: str,
    candidates: list[POI],
    context_notes: str = "",
) -> list[dict[str, str]]:
    """Assemble the messages for a POI-scoring LLM call."""
    poi_lines = [
        f"POI_{p.poi_id}: {p.name} ({p.category}, popularity={p.popularity:.2f}, "
        f"duration={p.avg_duration_min:.0f}min, indoor={'yes' if p.indoor else 'no'})"
        for p in candidates
    ]
    user = (
        f"TOURIST PROFILE:\n{profile_summary}\n\n"
        f"AVAILABLE POIS:\n" + "\n".join(poi_lines) + "\n\n"
    )
    if context_notes:
        user += f"CONTEXT NOTES:\n{context_notes}\n\n"
    user += (
        "Score each POI from 0.0 (irrelevant) to 1.0 (perfect match) for this tourist. "
        "Return a JSON object exactly in this shape:\n"
        '{"scores": {"POI_<id>": <float>, ...}}'
    )
    return [
        {"role": "system", "content": SCORE_SYSTEM},
        {"role": "user", "content": user},
    ]


def profile_summary(category_weights: dict[str, float], notes: str = "") -> str:
    """Render a profile as a short prompt-friendly string."""
    items = sorted(category_weights.items(), key=lambda x: x[1], reverse=True)
    weight_str = ", ".join(f"{cat}={w:.2f}" for cat, w in items if w > 0.001)
    out = f"Interests: {weight_str}"
    if notes:
        out += f"\nNotes: {notes}"
    return out
