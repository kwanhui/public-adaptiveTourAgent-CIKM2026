"""Session-lifecycle tests for the FastAPI server."""

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
from adaptivetouragent.ui.server import Session, create_app


def _seed_session(app, stub_llm, sid: str = "lifecycle_001") -> None:
    profile = UserProfile(user_id="x", name="X", category_weights={"park": 1.0})
    index = load_city("Singapore")
    start = datetime.combine(datetime.now().date(), time(hour=9))
    plan = asyncio.run(
        plan_initial(profile=profile, index=index, start_time=start, budget_minutes=600, llm=stub_llm)
    )
    sources: list[SignalSource] = [SyntheticCrowdSource(list(index.pois.values()))]
    cfg = LoopConfig(
        profile=profile, index=index, llm=stub_llm, sources=sources,
        start_time=start, budget_minutes=600, log=EventLog(None),
    )
    state = LoopState(plan=plan)
    app.state.sessions[sid] = Session(sid=sid, cfg=cfg, state=state)


@pytest.fixture
def app_with_session(stub_llm):
    app = create_app()
    _seed_session(app, stub_llm)
    return app


def test_delete_session_removes_it(app_with_session) -> None:
    client = TestClient(app_with_session)
    r = client.delete("/sessions/lifecycle_001")
    assert r.status_code == 200
    assert r.json() == {"status": "ended"}
    # Second delete: 404.
    r2 = client.delete("/sessions/lifecycle_001")
    assert r2.status_code == 404


def test_replan_unknown_session_404(app_with_session) -> None:
    client = TestClient(app_with_session)
    r = client.post("/replan/nope", json={"note": "x"})
    assert r.status_code == 404


def test_events_stream_endpoint_exists(app_with_session) -> None:
    """Just verify the route is wired; full SSE consumption needs an event loop."""
    client = TestClient(app_with_session)
    # GET /events/{sid} for an unknown sid returns 404 immediately.
    r = client.get("/events/nope", headers={"Accept": "text/event-stream"})
    assert r.status_code == 404
