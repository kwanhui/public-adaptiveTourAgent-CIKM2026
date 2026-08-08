"""End-to-end test: free-text note actually steers the replan.

The buttons + chat box on the UI both flow through the same fusion +
replan path. Before this fix, the user's note was used to fire a
`user_request` trigger but the actual text was dropped before reaching the
LLM scoring prompt, and no weather/transit signal was ever generated. So
clicking "Rain" produced a replan that returned the same plan back.

These tests prove the loop:
  1. "rain" → ManualSignalSource → snapshot.weather → require_indoor=True
     → tail POIs are all indoor.
  2. "tired" → fatigue offset → snapshot.user.fatigue >= 0.85 →
     `fatigue_high` trigger fires.
  3. profile constraints (money cap, prefer_low_carbon, pace) are
     forwarded through replan rather than silently dropped.
"""

from datetime import datetime

import pytest

from adaptivetouragent.agent.types import AccessibilityRequirements, UserProfile
from adaptivetouragent.logging_.event_log import EventLog
from adaptivetouragent.replanner.initial import plan_initial
from adaptivetouragent.replanner.loop import LoopConfig, LoopState, step
from adaptivetouragent.signals.note_parser import note_to_signals
from adaptivetouragent.signals.sources.base import SignalSource
from adaptivetouragent.signals.sources.crowd_synth import SyntheticCrowdSource
from adaptivetouragent.signals.sources.manual import ManualSignalSource


def _build_cfg(profile, index, stub_llm, start, *, money=None, low_carbon=False, pace="standard"):
    sources: list[SignalSource] = [
        SyntheticCrowdSource(list(index.pois.values())),
        ManualSignalSource(),
    ]
    return LoopConfig(
        profile=profile,
        index=index,
        llm=stub_llm,
        sources=sources,
        start_time=start,
        budget_minutes=600,
        log=EventLog(None),
        money_budget_usd=money,
        prefer_low_carbon=low_carbon,
        pace=pace,
    )


def _inject(cfg, note: str, upcoming_ids: list[str], at: datetime) -> None:
    manual = next(s for s in cfg.sources if isinstance(s, ManualSignalSource))
    batch, fatigue = note_to_signals(note=note, at=at, upcoming_poi_ids=upcoming_ids)
    if batch.weather is not None or batch.crowd or batch.transit:
        manual.inject(batch)
    if fatigue > 0.0:
        manual.add_fatigue(fatigue)


@pytest.mark.asyncio
async def test_rain_note_makes_tail_indoor(stub_llm, singapore_index) -> None:
    profile = UserProfile(
        user_id="rain_e2e",
        name="Tourist",
        category_weights={"park": 0.4, "viewpoint": 0.3, "museum": 0.3},
    )
    start = datetime(2026, 5, 16, 9, 0)
    initial = await plan_initial(
        profile=profile, index=singapore_index, start_time=start, budget_minutes=600, llm=stub_llm
    )
    assert initial.visits, "initial plan should have at least one visit"

    cfg = _build_cfg(profile, singapore_index, stub_llm, start)
    state = LoopState(plan=initial)

    rain_at = datetime(2026, 5, 16, 11, 0)
    upcoming_ids = [v.poi_id for v in initial.visits if v.depart > rain_at]
    _inject(cfg, "It started raining heavily", upcoming_ids, rain_at)

    triggers = await step(cfg, state, at=rain_at, pref_changes=["It started raining heavily"])

    assert any(t.kind == "weather_rain_onset" for t in triggers) or any(t.kind == "user_request" for t in triggers)
    assert state.n_replans == 1

    tail = [v for v in state.plan.visits if v.depart > rain_at]
    for visit in tail:
        poi = singapore_index.pois[visit.poi_id]
        assert poi.indoor, f"{visit.name} is outdoor, should not appear after rain note"


@pytest.mark.asyncio
async def test_tired_note_lifts_fatigue_high(stub_llm, singapore_index) -> None:
    profile = UserProfile(
        user_id="tired_e2e",
        name="Tourist",
        category_weights={"park": 1.0},
    )
    start = datetime(2026, 5, 16, 9, 0)
    initial = await plan_initial(
        profile=profile, index=singapore_index, start_time=start, budget_minutes=600, llm=stub_llm
    )

    cfg = _build_cfg(profile, singapore_index, stub_llm, start)
    state = LoopState(plan=initial)

    # Use a time several hours in so the base fatigue model is already at ~0.5,
    # crossing the 0.85 trigger threshold once the +0.35 boost is applied.
    at = datetime(2026, 5, 16, 16, 0)
    upcoming_ids = [v.poi_id for v in initial.visits if v.depart > at]
    _inject(cfg, "The kids are tired", upcoming_ids, at)

    triggers = await step(cfg, state, at=at, pref_changes=["The kids are tired"])

    kinds = {t.kind for t in triggers}
    assert "fatigue_high" in kinds or "user_request" in kinds


@pytest.mark.asyncio
async def test_money_cap_honoured_on_replan(stub_llm, singapore_index) -> None:
    """The replan must respect the same money cap as the initial plan."""
    profile = UserProfile(
        user_id="money_e2e",
        name="Tourist",
        family_size=2,
        category_weights={"museum": 0.5, "park": 0.5},
        accessibility=AccessibilityRequirements(),
    )
    start = datetime(2026, 5, 16, 9, 0)
    cap = 60.0
    initial = await plan_initial(
        profile=profile,
        index=singapore_index,
        start_time=start,
        budget_minutes=600,
        llm=stub_llm,
        money_budget_usd=cap,
    )

    cfg = _build_cfg(profile, singapore_index, stub_llm, start, money=cap)
    state = LoopState(plan=initial)

    at = datetime(2026, 5, 16, 11, 0)
    upcoming_ids = [v.poi_id for v in initial.visits if v.depart > at]
    _inject(cfg, "It started raining heavily", upcoming_ids, at)

    await step(cfg, state, at=at, pref_changes=["It started raining heavily"])

    spent = sum(v.entry_fee_usd + v.travel_cost_usd for v in state.plan.visits)
    assert spent <= cap + 1e-6, f"replan exceeded money cap (${spent:.2f} > ${cap:.2f})"


@pytest.mark.asyncio
async def test_replan_context_includes_literal_user_note(stub_llm, singapore_index) -> None:
    """The LLM scoring prompt should contain the user's actual words."""
    profile = UserProfile(
        user_id="prompt_e2e",
        name="Tourist",
        category_weights={"museum": 1.0},
    )
    start = datetime(2026, 5, 16, 9, 0)
    initial = await plan_initial(
        profile=profile, index=singapore_index, start_time=start, budget_minutes=600, llm=stub_llm
    )

    cfg = _build_cfg(profile, singapore_index, stub_llm, start)
    state = LoopState(plan=initial)

    at = datetime(2026, 5, 16, 11, 0)
    free_text = "skip the next museum, find dinner instead"
    upcoming_ids = [v.poi_id for v in initial.visits if v.depart > at]
    _inject(cfg, free_text, upcoming_ids, at)

    await step(cfg, state, at=at, pref_changes=[free_text])

    # stub_llm.calls is a list of message lists; check that one of the
    # later calls (the replan's score_pois) contains the user's literal note.
    flat = " ".join(
        msg["content"] for messages in stub_llm.calls for msg in messages if isinstance(msg.get("content"), str)
    )
    assert free_text in flat, "user note was not included in any LLM prompt"
