"""Typed trigger event schema. This is a frozen contract."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

TriggerKind = Literal[
    "weather_rain_onset",
    "weather_storm",
    "crowd_spike",
    "poi_closed",
    "transit_disruption",
    "fatigue_high",
    "user_request",
]

Severity = Literal["info", "warn", "critical"]


@dataclass(frozen=True)
class TriggerEvent:
    """A single trigger that may or may not warrant replanning.

    The replanner looks at the severity and the affected POIs/edges to decide
    whether to invoke an LLM call. The trigger registry is responsible for
    debouncing and cooldowns; by the time a TriggerEvent reaches the
    replanner, it has already passed those gates.
    """

    kind: TriggerKind
    severity: Severity
    at: datetime
    affects: list[str] = field(default_factory=list)  # poi_ids or "edge:from->to"
    details: dict[str, str] = field(default_factory=dict)  # numeric thresholds crossed (stringified)
    snapshot_id: str = ""  # FK into the JSONL ContextSnapshot log
