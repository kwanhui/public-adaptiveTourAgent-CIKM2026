"""Live replan driver.

The driver:
  1. Polls all configured signal sources at simulated wall-clock times.
  2. Estimates user state from the executed prefix.
  3. Fuses the readings into a ContextSnapshot.
  4. Runs the trigger registry; if anything fires, calls `replan.replan`.
  5. Logs every step as a JSONL event.

The driver is reusable: the demo CLI uses it with a RecordedSource; the
FastAPI server (M3) uses it with mixed live + synthetic sources.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from adaptivetouragent.agent.types import AgentRunStats, UserProfile
from adaptivetouragent.fusion.fuser import fuse
from adaptivetouragent.fusion.user_state import estimate_user_state
from adaptivetouragent.itinerary.types import Itinerary, POIVisit
from adaptivetouragent.llm.provider import LLMProvider
from adaptivetouragent.logging_.event_log import EventLog
from adaptivetouragent.replanner.replan import replan
from adaptivetouragent.replanner.types import ReplanRequest, ReplanResponse
from adaptivetouragent.retrieval.poi_index import POIIndex
from adaptivetouragent.signals.sources.base import SignalSource
from adaptivetouragent.signals.sources.manual import ManualSignalSource
from adaptivetouragent.signals.triggers.registry import TriggerRegistry
from adaptivetouragent.signals.triggers.types import TriggerEvent

logger = logging.getLogger(__name__)


@dataclass
class LoopConfig:
    profile: UserProfile
    index: POIIndex
    llm: LLMProvider
    sources: list[SignalSource]
    start_time: datetime
    budget_minutes: float
    registry: TriggerRegistry = field(default_factory=TriggerRegistry)
    log: EventLog = field(default_factory=lambda: EventLog(None))
    ablations: list[str] = field(default_factory=list)
    # Planning constraints forwarded to replans so a mid-trip re-decision
    # honours the same money cap, carbon preference, and pace as the
    # initial plan. Defaults match `plan_initial` defaults.
    money_budget_usd: float | None = None
    prefer_low_carbon: bool = False
    pace: str = "standard"


@dataclass
class LoopState:
    plan: Itinerary
    executed_prefix: list[POIVisit] = field(default_factory=list)
    n_replans: int = 0
    stats: AgentRunStats = field(default_factory=AgentRunStats)
    # Previous itineraries, newest last. Pushed before each replan so the UI
    # can offer an "undo last change" that restores the prior revision.
    history: list[Itinerary] = field(default_factory=list)


def _executed_prefix_at(plan: Itinerary, now: datetime) -> list[POIVisit]:
    """Visits that have already departed by `now` are considered executed."""
    return [v for v in plan.visits if v.depart <= now]


async def step(
    cfg: LoopConfig,
    state: LoopState,
    *,
    at: datetime,
    pref_changes: list[str] | None = None,
    on_replan: Callable[[ReplanResponse], Awaitable[None]] | None = None,
) -> list[TriggerEvent]:
    """Run one tick: fetch signals, fuse, evaluate triggers, optionally replan."""
    state.executed_prefix = _executed_prefix_at(state.plan, at)
    upcoming_ids = [v.poi_id for v in state.plan.visits if v.depart > at]

    batches = []
    for src in cfg.sources:
        try:
            batches.append(await src.fetch(at))
        except Exception as e:
            logger.warning("source %s failed: %s", src.name, e)

    # Drain any fatigue boost the manual source has accumulated since the
    # last tick (e.g. user clicked the "I'm tired" button). The boost is
    # consumed once and added on top of the time/visit-based fatigue model.
    fatigue_offset = 0.0
    for src in cfg.sources:
        if isinstance(src, ManualSignalSource):
            fatigue_offset += src.pop_fatigue_boost()

    user = estimate_user_state(
        start_time=cfg.start_time,
        now=at,
        executed_visits=state.executed_prefix,
        family_size=cfg.profile.family_size,
        pref_changes=pref_changes,
        fatigue_offset=fatigue_offset,
    )

    snapshot = fuse(
        batches,
        user=user,
        city=cfg.index.city,
        at=at,
        disable=cfg.ablations,
    )

    cfg.log.write(
        "snapshot",
        snapshot_id=snapshot.snapshot_id,
        at=at.isoformat(),
        weather=snapshot.weather,
        n_crowd=len(snapshot.crowd),
        n_transit=len(snapshot.transit),
        fatigue=snapshot.user.fatigue_0_1,
        sources_used=snapshot.sources_used,
    )

    triggers = cfg.registry.evaluate(snapshot, upcoming_poi_ids=upcoming_ids)
    for t in triggers:
        cfg.log.write("trigger", kind=t.kind, severity=t.severity, affects=t.affects, details=t.details)

    if not triggers:
        return triggers

    request = ReplanRequest(
        current=state.plan,
        executed_prefix=state.executed_prefix,
        snapshot=snapshot,
        triggers=triggers,
        now=at,
    )
    response = await replan(
        request,
        profile=cfg.profile,
        index=cfg.index,
        llm=cfg.llm,
        budget_minutes=cfg.budget_minutes,
        start_time=cfg.start_time,
        stats=state.stats,
        money_budget_usd=cfg.money_budget_usd,
        prefer_low_carbon=cfg.prefer_low_carbon,
        pace=cfg.pace,
    )

    state.history.append(state.plan)
    state.plan = response.updated
    state.n_replans += 1
    cfg.log.write(
        "replan",
        n_replans=state.n_replans,
        triggers=[t.kind for t in triggers],
        diff_summary=response.diff.summary,
        n_visits=len(response.updated.visits),
        rationale=response.rationale,
        cost_usd=response.cost_usd,
    )

    if on_replan is not None:
        await on_replan(response)

    return triggers
