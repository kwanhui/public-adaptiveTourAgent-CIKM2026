"""Live loop driver: full replay of the rainy-day trace with the stub LLM."""

from datetime import datetime
from pathlib import Path

import pytest

from adaptivetouragent.agent.types import UserProfile
from adaptivetouragent.logging_.event_log import EventLog
from adaptivetouragent.replanner.initial import plan_initial
from adaptivetouragent.replanner.loop import LoopConfig, LoopState, step
from adaptivetouragent.signals.sources.recorded import RecordedSource

TRACE = Path(__file__).resolve().parent.parent / "demo/sample-inputs/scenarios/family-rainy-day.jsonl"


@pytest.mark.asyncio
async def test_rainy_day_trace_triggers_at_least_one_replan(stub_llm, singapore_index, tmp_path) -> None:
    profile = UserProfile(
        user_id="family",
        name="Family",
        category_weights={"park": 0.4, "zoo": 0.3, "museum": 0.3},
        family_size=4,
    )
    start = datetime(2026, 5, 2, 9, 0)
    initial = await plan_initial(
        profile=profile, index=singapore_index, start_time=start, budget_minutes=600, llm=stub_llm
    )
    log_path = tmp_path / "run.jsonl"
    cfg = LoopConfig(
        profile=profile,
        index=singapore_index,
        llm=stub_llm,
        sources=[RecordedSource(TRACE)],
        start_time=start,
        budget_minutes=600,
        log=EventLog(log_path),
    )
    state = LoopState(plan=initial)

    times = cfg.sources[0].all_event_times  # type: ignore[attr-defined]
    for at in times:
        await step(cfg, state, at=at)

    assert state.n_replans >= 1
    # Bound trigger thrashing: a 1-day trace should not produce more than ~6 replans.
    assert state.n_replans < 8

    log_text = log_path.read_text()
    assert '"event": "replan"' in log_text
    assert '"event": "trigger"' in log_text
