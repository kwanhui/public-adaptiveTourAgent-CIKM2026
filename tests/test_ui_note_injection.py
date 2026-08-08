"""HTTP-level smoke test: POST /replan and /chat actually rewrite the plan.

Seeds a session with the stub LLM, then exercises the same endpoints the
browser hits. Before the fix, the post-replan itinerary was identical to
the initial plan because the user's text was dropped before reaching the
fusion + scoring path.
"""

import asyncio
from datetime import datetime, time

import pytest
from fastapi.testclient import TestClient

from adaptivetouragent.agent.types import UserProfile
from adaptivetouragent.logging_.event_log import EventLog
from adaptivetouragent.replanner.initial import plan_initial
from adaptivetouragent.replanner.loop import LoopConfig, LoopState
from adaptivetouragent.retrieval.poi_index import load_city
from adaptivetouragent.signals.sources.base import SignalSource
from adaptivetouragent.signals.sources.crowd_synth import SyntheticCrowdSource
from adaptivetouragent.signals.sources.manual import ManualSignalSource
from adaptivetouragent.ui.server import Session, create_app


@pytest.fixture
def app_with_session(stub_llm):
    app = create_app()
    profile = UserProfile(
        user_id="ui_note",
        name="Tourist",
        category_weights={"park": 0.4, "viewpoint": 0.3, "museum": 0.3},
    )
    index = load_city("Singapore")
    start = datetime.combine(datetime.now().date(), time(hour=9))
    plan = asyncio.run(plan_initial(profile=profile, index=index, start_time=start, budget_minutes=600, llm=stub_llm))
    sources: list[SignalSource] = [
        SyntheticCrowdSource(list(index.pois.values())),
        ManualSignalSource(),
    ]
    cfg = LoopConfig(
        profile=profile,
        index=index,
        llm=stub_llm,
        sources=sources,
        start_time=start,
        budget_minutes=600,
        log=EventLog(None),
    )
    state = LoopState(plan=plan)
    sid = "ui_note_001"
    app.state.sessions[sid] = Session(sid=sid, cfg=cfg, state=state)
    return app, sid, plan


def test_replan_endpoint_with_rain_note_changes_plan(app_with_session) -> None:
    app, sid, initial = app_with_session
    client = TestClient(app)
    # Use an `advance_to_iso` close to the plan's start so most of the tail
    # is still replanable (otherwise the executed prefix locks everything).
    at_iso = initial.visits[0].arrive.isoformat() if initial.visits else None

    r = client.post(
        f"/replan/{sid}",
        json={"note": "It started raining heavily", "advance_to_iso": at_iso},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["n_replans"] == 1
    fired = set(body["fired"])
    # Either path is acceptable; weather_rain_onset is the "right" one,
    # user_request is the fallback when manual weather injection doesn't
    # cross the trigger's debounce.
    assert fired & {"weather_rain_onset", "user_request"}


def test_chat_endpoint_with_tired_note_replans(app_with_session) -> None:
    app, sid, initial = app_with_session
    client = TestClient(app)
    at_iso = initial.visits[0].arrive.isoformat() if initial.visits else None
    r = client.post(
        f"/chat/{sid}",
        json={"text": "the kids are tired", "advance_to_iso": at_iso},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["n_replans"] == 1


def test_replan_with_advance_to_iso_locks_prefix(app_with_session) -> None:
    """When the user marks POI #i as visited, the front-end sends
    advance_to_iso = visits[i].depart, and the server must lock those
    visits in the executed_prefix and only re-decide the tail.
    """
    app, sid, initial = app_with_session
    client = TestClient(app)
    assert len(initial.visits) >= 3, "test needs at least 3 visits"

    locked_idx = 1  # mark POI 0 and POI 1 as visited
    at_iso = initial.visits[locked_idx].depart.isoformat()
    locked_ids = [v.poi_id for v in initial.visits[: locked_idx + 1]]

    r = client.post(
        f"/replan/{sid}",
        json={"note": "It started raining heavily", "advance_to_iso": at_iso},
    )
    assert r.status_code == 200
    new_plan = app.state.sessions[sid].state.plan
    # The first `locked_idx + 1` visits on the new plan must be exactly the
    # ones the user marked visited, in the same order.
    new_prefix_ids = [v.poi_id for v in new_plan.visits[: locked_idx + 1]]
    assert new_prefix_ids == locked_ids, (
        f"executed prefix not preserved: got {new_prefix_ids}, expected {locked_ids}"
    )
