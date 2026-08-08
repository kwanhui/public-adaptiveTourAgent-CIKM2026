"""Agentic LLM Rectifier (ALR): verify-and-revise pass.

Follows the ALR pattern from RALLM-POI (Li & Lim, PRICAI 2025): after the
primary LLM produces an output, a second pass critiques it against simple
rules and returns a corrected version. We use it for two things: catching
constraint violations in the produced plan (e.g. visiting a closed POI) and
generating a short human-readable rationale for the chat panel.

This module exposes only `narrate_plan`, which produces the rationale.
The constraint-correction path is wired up once the replanner exists.
"""

import logging

from adaptivetouragent.agent.types import AgentRunStats
from adaptivetouragent.itinerary.types import Itinerary
from adaptivetouragent.llm.provider import LLMProvider

logger = logging.getLogger(__name__)


_NARRATE_SYSTEM = (
    "You are a tour guide assistant. Given a planned itinerary, write a single "
    "short paragraph (under 60 words) explaining why the order is good. "
    "Plain prose. No markdown, no lists, no headings."
)


async def narrate_plan(
    itinerary: Itinerary,
    llm: LLMProvider,
    *,
    triggers_summary: str = "",
    stats: AgentRunStats | None = None,
) -> str:
    """Produce a one-paragraph rationale for the chat panel."""
    if not itinerary.visits:
        return "No POIs fit the available time."

    visit_lines = "\n".join(
        f"- {i+1}. {v.name} ({v.category}) at {v.arrive.strftime('%H:%M')}"
        for i, v in enumerate(itinerary.visits)
    )
    user = (
        f"City: {itinerary.city}\n"
        f"Plan ({len(itinerary.visits)} stops):\n{visit_lines}\n"
    )
    if triggers_summary:
        user += f"\nThis is a replan triggered by: {triggers_summary}\n"
    user += "\nWrite the rationale paragraph now."

    response = await llm.complete(
        [
            {"role": "system", "content": _NARRATE_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0.3,
        max_tokens=160,
    )
    if stats is not None:
        stats.rectifier_calls += 1

    return (response.content or "").strip() or "Itinerary ordered by interest and proximity."
