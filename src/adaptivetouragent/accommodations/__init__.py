"""Accommodation catalogue + LLM-driven matcher."""

from adaptivetouragent.accommodations.agent import pick_accommodation, score_accommodations
from adaptivetouragent.accommodations.index import filter_by_hard_constraints, load_accommodations
from adaptivetouragent.accommodations.types import (
    Accommodation,
    AccommodationChoice,
    AccommodationRequest,
)

__all__ = [
    "Accommodation",
    "AccommodationChoice",
    "AccommodationRequest",
    "filter_by_hard_constraints",
    "load_accommodations",
    "pick_accommodation",
    "score_accommodations",
]
