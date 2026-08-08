"""Mid-trip replanner.

Distinct entry from `replanner.initial`. Takes a current itinerary, the
already-executed prefix (locked), a ContextSnapshot, and the triggers that
fired. Produces a `ReplanResponse` with the updated plan, a `PlanDiff`, and
a short LLM-generated rationale.
"""

import logging
import uuid
from datetime import datetime

from adaptivetouragent.agent.loop import score_pois
from adaptivetouragent.agent.rectifier import narrate_plan
from adaptivetouragent.agent.types import AgentRunStats, UserProfile
from adaptivetouragent.fusion.snapshot import ContextSnapshot
from adaptivetouragent.itinerary.geometry import populate_geometries
from adaptivetouragent.itinerary.optimizer import greedy_plan
from adaptivetouragent.itinerary.types import (
    DiffOp,
    Itinerary,
    PlanDiff,
    PlanDiffEntry,
    POIVisit,
)
from adaptivetouragent.llm.provider import LLMProvider
from adaptivetouragent.replanner.types import ReplanRequest, ReplanResponse
from adaptivetouragent.retrieval.poi_index import POIIndex, _city_slug
from adaptivetouragent.retrieval.retriever import retrieve_candidates
from adaptivetouragent.signals.triggers.types import TriggerEvent

logger = logging.getLogger(__name__)


def diff_plans(old: Itinerary, new: Itinerary, executed_prefix: list[POIVisit]) -> PlanDiff:
    """Compute the diff between two itineraries."""
    old_ids = [v.poi_id for v in old.visits]
    new_ids = [v.poi_id for v in new.visits]
    prefix_ids = {v.poi_id for v in executed_prefix}

    old_set = set(old_ids)
    new_set = set(new_ids)

    entries: list[PlanDiffEntry] = []

    for v in old.visits:
        if v.poi_id in prefix_ids:
            entries.append(PlanDiffEntry(op=DiffOp.PRESERVE, poi_id=v.poi_id, name=v.name, reason="executed"))
            continue
        if v.poi_id in new_set:
            old_pos = old_ids.index(v.poi_id)
            new_pos = new_ids.index(v.poi_id)
            if old_pos != new_pos:
                entries.append(PlanDiffEntry(op=DiffOp.REORDER, poi_id=v.poi_id, name=v.name, reason="reorder"))
            else:
                entries.append(PlanDiffEntry(op=DiffOp.PRESERVE, poi_id=v.poi_id, name=v.name))
        else:
            entries.append(PlanDiffEntry(op=DiffOp.DROP, poi_id=v.poi_id, name=v.name, reason="dropped"))

    for v in new.visits:
        if v.poi_id not in old_set:
            entries.append(PlanDiffEntry(op=DiffOp.INSERT, poi_id=v.poi_id, name=v.name, reason="inserted"))

    summary_parts = []
    n_dropped = sum(1 for e in entries if e.op == DiffOp.DROP)
    n_inserted = sum(1 for e in entries if e.op == DiffOp.INSERT)
    n_reorder = sum(1 for e in entries if e.op == DiffOp.REORDER)
    if n_dropped:
        summary_parts.append(f"{n_dropped} dropped")
    if n_inserted:
        summary_parts.append(f"{n_inserted} inserted")
    if n_reorder:
        summary_parts.append(f"{n_reorder} reordered")
    summary = ", ".join(summary_parts) if summary_parts else "no change"

    return PlanDiff(entries=entries, summary=summary)


def _trigger_summary(triggers: list[TriggerEvent]) -> str:
    if not triggers:
        return ""
    bits = []
    for t in triggers:
        if t.affects:
            bits.append(f"{t.kind}({','.join(t.affects)})")
        else:
            bits.append(t.kind)
    return ", ".join(bits)


def _replan_context_notes(snapshot: ContextSnapshot, triggers: list[TriggerEvent]) -> str:
    """Render the snapshot + triggers as a short prompt-friendly note."""
    parts: list[str] = []
    if snapshot.weather is not None:
        w = snapshot.weather
        parts.append(f"weather: {w.condition} at {w.temp_c:.0f}C, precip {w.precip_mm_per_h:.1f}mm/h")
    crowded = [pid for pid, cr in snapshot.crowd.items() if cr.crowd_level == "high"]
    if crowded:
        parts.append(f"crowded POIs (avoid): {', '.join(crowded)}")
    closed = [pid for pid, cr in snapshot.crowd.items() if cr.crowd_level == "closed"]
    if closed:
        parts.append(f"closed POIs (skip): {', '.join(closed)}")
    disrupted = [f"{a}->{b}" for (a, b), tr in snapshot.transit.items() if tr.disruption]
    if disrupted:
        parts.append(f"transit disruption on: {', '.join(disrupted)}")
    if snapshot.user.fatigue_0_1 >= 0.7:
        parts.append(f"user fatigue is high ({snapshot.user.fatigue_0_1:.2f})")
    # The user's literal note is the highest-signal context; it carries
    # intent the keyword parser may have missed ("skip the next museum",
    # "find dinner instead"). Surface it verbatim so the scoring LLM can
    # react.
    if snapshot.user.explicit_pref_changes:
        parts.append('user said: "' + " | ".join(snapshot.user.explicit_pref_changes) + '"')
    if triggers:
        parts.append(f"triggers fired: {_trigger_summary(triggers)}")
    return "; ".join(parts)


async def replan(
    request: ReplanRequest,
    *,
    profile: UserProfile,
    index: POIIndex,
    llm: LLMProvider,
    budget_minutes: float,
    start_time: datetime,
    top_k_candidates: int = 12,
    stats: AgentRunStats | None = None,
    money_budget_usd: float | None = None,
    prefer_low_carbon: bool = False,
    pace: str = "standard",
) -> ReplanResponse:
    """Run one replan step.

    The replan keeps the executed prefix fixed and only re-decides the tail.
    Closed POIs are excluded from candidates; rainy-weather triggers force
    `require_indoor=True` and downweight outdoor categories.

    `money_budget_usd`, `prefer_low_carbon`, and `pace` mirror the same
    knobs on `plan_initial` so the replan honours the user's original
    constraints. `profile.family_size` and `profile.accessibility.
    require_wheelchair` are forwarded from the profile itself.
    """
    snap = request.snapshot
    triggers = request.triggers

    closed_ids = {pid for pid, cr in snap.crowd.items() if cr.crowd_level == "closed"}
    crowded_ids = {pid for pid, cr in snap.crowd.items() if cr.crowd_level == "high"}
    visited_ids = {v.poi_id for v in request.executed_prefix}
    # Group dynamics, re-applied live: a member (or mid-trip) veto hard-excludes
    # its whole category from the replanned tail, and a boost lifts its score.
    # This is the part that makes vetoes/boosts live constraints rather than
    # plan-time-only aggregation.
    vetoed_categories = profile.vetoed_categories()
    boosted_categories = profile.boosted_categories()
    vetoed_ids = {p.poi_id for p in index.pois.values() if p.category in vetoed_categories}
    # Stops the traveller removed by hand stay out of every subsequent revision.
    removed_ids = set(profile.excluded_pois)
    exclude = closed_ids | visited_ids | vetoed_ids | removed_ids

    require_indoor: bool | None = None
    if snap.weather is not None and snap.weather.condition in ("rain", "storm"):
        require_indoor = True

    # We deliberately do NOT pass `open_at_hour=request.now.hour` here. The
    # optimiser already shifts arrival to each POI's opening time when the
    # cursor is earlier, so filtering at retrieval would drop POIs that
    # open later in the day (e.g. museums at 10am when a rainy 9am replan
    # fires) and starve the indoor tail. Same rule as `plan_initial`.
    candidates = retrieve_candidates(
        index,
        category_weights=profile.aggregated_weights(),
        top_k=top_k_candidates,
        require_indoor=require_indoor,
        require_kid_friendly=profile.require_kid_friendly or None,
        exclude=exclude,
    )

    notes = _replan_context_notes(snap, triggers)
    poi_scores = await score_pois(
        profile=profile,
        candidates=candidates,
        llm=llm,
        context_notes=notes,
        stats=stats,
    )

    # Penalise crowded POIs in the LLM-provided scores so the optimiser steers around them.
    for pid in crowded_ids:
        if pid in poi_scores:
            poi_scores[pid] *= 0.5

    # Lift boosted-category POIs so a group member's "loves this" preference
    # survives the replan (vetoes are already hard-excluded above).
    if boosted_categories:
        for poi in candidates:
            if poi.category in boosted_categories and poi.poi_id in poi_scores:
                poi_scores[poi.poi_id] = min(1.0, poi_scores[poi.poi_id] * 1.25)

    new_plan = greedy_plan(
        index=index,
        candidates=candidates,
        poi_scores=poi_scores,
        start_time=start_time,
        budget_minutes=budget_minutes,
        user_id=profile.user_id,
        plan_id=uuid.uuid4().hex[:12],
        derived_from=request.current.plan_id,
        locked_prefix=list(request.executed_prefix),
        money_budget_usd=money_budget_usd,
        party_size=max(1, profile.family_size),
        prefer_low_carbon=prefer_low_carbon,
        require_wheelchair=profile.accessibility.require_wheelchair,
        pace=pace,
    )

    # Upgrade each new-tail leg's straight-line `inbound_geometry` to an
    # OSRM-routed polyline. Same call as `plan_initial`; without this the
    # map shows "as the crow flies" lines for every replanned leg while
    # the original-plan legs follow real streets. Cache-first: repeated
    # legs reuse the on-disk geometry. Falls back to straight lines when
    # OSRM is disabled (CI) or unreachable.
    await populate_geometries(
        new_plan.visits,
        city_slug=_city_slug(index.city) or "unknown",
        pois=index.pois,
    )

    diff = diff_plans(request.current, new_plan, request.executed_prefix)
    rationale = await narrate_plan(
        new_plan,
        llm,
        triggers_summary=_trigger_summary(triggers),
        stats=stats,
    )

    cost = 0.0
    summary = llm.get_usage_summary()
    if isinstance(summary, dict):
        cost = float(summary.get("estimated_cost_usd", 0.0))

    return ReplanResponse(
        updated=new_plan,
        diff=diff,
        rationale=rationale,
        cost_usd=cost,
    )
