"""Replan request and response. This is a frozen contract."""

from dataclasses import dataclass
from datetime import datetime

from adaptivetouragent.fusion.snapshot import ContextSnapshot
from adaptivetouragent.itinerary.types import Itinerary, PlanDiff, POIVisit
from adaptivetouragent.signals.triggers.types import TriggerEvent


@dataclass
class ReplanRequest:
    """Request to revise an itinerary mid-trip."""

    current: Itinerary
    executed_prefix: list[POIVisit]  # locked, will not be reordered
    snapshot: ContextSnapshot
    triggers: list[TriggerEvent]  # what fired this replan
    now: datetime


@dataclass
class ReplanResponse:
    """Result of a replan."""

    updated: Itinerary
    diff: PlanDiff
    rationale: str  # short LLM-generated text for the chat panel
    cost_usd: float  # bookkeeping for the demo
