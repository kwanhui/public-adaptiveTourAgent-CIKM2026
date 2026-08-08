"""HTTP smoke tests using FastAPI's TestClient.

The /plan endpoint creates a session backed by the real OpenAIClient (which
requires OPENAI_API_KEY), so the test patches in the stub LLM through the
session registry directly. This avoids hitting the network in CI.
"""

import asyncio
import os
from datetime import datetime, time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from adaptivetouragent.agent.types import UserProfile
from adaptivetouragent.logging_.event_log import EventLog
from adaptivetouragent.replanner.initial import plan_initial
from adaptivetouragent.replanner.loop import LoopConfig, LoopState
from adaptivetouragent.retrieval.poi_index import load_city
from adaptivetouragent.signals.sources.crowd_synth import SyntheticCrowdSource
from adaptivetouragent.ui.server import Session, create_app


@pytest.fixture
def client_with_session(stub_llm):
    """Bypass /plan and seed a session straight into the app for tests."""
    app = create_app()
    client = TestClient(app)

    profile = UserProfile(user_id="ui_test", name="Test", category_weights={"park": 0.5, "museum": 0.5})
    index = load_city("Singapore")
    start = datetime.combine(datetime.now().date(), time(hour=9))
    budget = 600.0

    plan = asyncio.run(
        plan_initial(profile=profile, index=index, start_time=start, budget_minutes=budget, llm=stub_llm)
    )
    cfg = LoopConfig(
        profile=profile,
        index=index,
        llm=stub_llm,
        sources=[SyntheticCrowdSource(list(index.pois.values()))],
        start_time=start,
        budget_minutes=budget,
        log=EventLog(None),
    )
    state = LoopState(plan=plan)
    sid = "test_sid_001"
    app.state.sessions[sid] = Session(sid=sid, cfg=cfg, state=state)
    return client, sid


def test_healthz_ok() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_root_serves_index_html() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/")
    # When static dir exists the HTML is served; otherwise a 404 is returned.
    # Either is acceptable depending on packaging; we just check it does not 500.
    assert r.status_code in (200, 404)


def test_replan_endpoint_returns_fired_kinds(client_with_session) -> None:
    client, sid = client_with_session
    r = client.post(
        f"/replan/{sid}",
        json={"note": "user wants to skip the next stop", "advance_to_iso": "2026-05-02T14:00:00"},
    )
    assert r.status_code == 200
    payload = r.json()
    assert "fired" in payload
    assert "n_replans" in payload
    assert "user_request" in payload["fired"]


def test_unknown_session_404(client_with_session) -> None:
    client, _ = client_with_session
    r = client.post("/replan/nonexistent", json={"note": "x"})
    assert r.status_code == 404


def test_book_endpoint_confirms_and_audits(client_with_session) -> None:
    client, sid = client_with_session
    poi_id = client.app.state.sessions[sid].state.plan.visits[0].poi_id
    r = client.post(f"/book/{sid}", json={"poi_id": poi_id})
    assert r.status_code == 200
    booking = r.json()
    assert booking["status"] == "confirmed"
    assert booking["confirmation_code"].startswith("DRYRUN-")
    assert booking["target_id"] == poi_id
    # The booking lands on the session's audit trail.
    audit = client.get(f"/bookings/{sid}")
    assert audit.status_code == 200
    assert len(audit.json()) == 1
    assert audit.json()[0]["confirmation_code"] == booking["confirmation_code"]


def test_book_unknown_stop_404(client_with_session) -> None:
    client, sid = client_with_session
    r = client.post(f"/book/{sid}", json={"poi_id": "sg-not-real"})
    assert r.status_code == 404


def test_remove_stop_excludes_and_persists(client_with_session) -> None:
    client, sid = client_with_session
    poi_id = client.app.state.sessions[sid].state.plan.visits[0].poi_id
    r = client.post(
        f"/remove-stop/{sid}",
        json={"poi_id": poi_id, "advance_to_iso": "2026-05-02T09:00:00"},
    )
    assert r.status_code == 200
    assert poi_id in r.json()["removed"]
    # The removal sticks on the live profile for subsequent replans.
    assert poi_id in client.app.state.sessions[sid].cfg.profile.excluded_pois


def test_remove_stop_is_delete_only(client_with_session) -> None:
    """Removing a stop drops it and re-routes the rest in order; no backfill."""
    client, sid = client_with_session
    session = client.app.state.sessions[sid]
    before = [v.poi_id for v in session.state.plan.visits]
    assert len(before) >= 2
    poi_id = before[1]  # a middle stop
    r = client.post(
        f"/remove-stop/{sid}",
        json={"poi_id": poi_id, "advance_to_iso": "2026-05-02T09:00:00"},
    )
    assert r.status_code == 200
    after = [v.poi_id for v in session.state.plan.visits]
    # The stop is gone, nothing new was added, and the rest keep their order.
    assert poi_id not in after
    assert set(after).issubset(set(before))
    assert after == [p for p in before if p != poi_id]
    assert r.json()["n_stops"] == len(before) - 1


def test_undo_restores_previous_plan(client_with_session) -> None:
    client, sid = client_with_session
    session = client.app.state.sessions[sid]
    before_ids = [v.poi_id for v in session.state.plan.visits]
    # A removal pushes the prior plan onto history and re-routes.
    client.post(
        f"/remove-stop/{sid}",
        json={"poi_id": before_ids[0], "advance_to_iso": "2026-05-02T09:00:00"},
    )
    # Undo restores the pre-removal itinerary.
    r = client.post(f"/undo/{sid}")
    assert r.status_code == 200
    assert r.json()["undone"] is True
    assert [v.poi_id for v in session.state.plan.visits] == before_ids


def test_undo_with_no_history_409(client_with_session) -> None:
    client, sid = client_with_session
    r = client.post(f"/undo/{sid}")
    assert r.status_code == 409


def test_group_veto_endpoint_records_and_fires(client_with_session) -> None:
    client, sid = client_with_session
    r = client.post(
        f"/group-veto/{sid}",
        json={"category": "park", "member": "Alex", "advance_to_iso": "2026-05-02T09:00:00"},
    )
    assert r.status_code == 200
    payload = r.json()
    assert "park" in payload["vetoed"]
    assert "user_request" in payload["fired"]
    # The veto persists on the live session profile for subsequent replans.
    assert "park" in client.app.state.sessions[sid].cfg.profile.live_veto_categories


def test_plan_endpoint_surfaces_missing_api_key() -> None:
    """`/plan` cleanly surfaces a missing OPENAI_API_KEY rather than crashing."""
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    body = {
        "profile": {"user_id": "x", "name": "X", "category_weights": {"park": 1.0}},
        "city": "Singapore",
        "start_hour": 9,
        "end_hour": 19,
        "days": 1,
    }
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OPENAI_API_KEY", None)
        r = client.post("/plan", json=body)
    # 503 = LLM unavailable (the structured error). 200 if a key happens to be set in the env.
    assert r.status_code in (200, 503)
    if r.status_code == 503:
        assert "LLM unavailable" in r.json().get("detail", "")


@pytest.mark.parametrize("city", ["Singapore", "Melbourne", "London", "New York", "Paris"])
def test_plan_endpoint_accepts_every_supported_city(city: str) -> None:
    """The /plan endpoint must accept any of the five city dropdown options.
    Returns 503 (no API key in CI); we just want to confirm it gets past
    city resolution (which would have been a 404 before the catalogues
    landed)."""
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    body = {
        "profile": {"user_id": "x", "name": "X", "category_weights": {"park": 1.0}},
        "city": city,
        "start_hour": 9,
        "end_hour": 19,
        "days": 1,
    }
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OPENAI_API_KEY", None)
        r = client.post("/plan", json=body)
    # Either the LLM is configured (200) or it isn't (503). 404 (unknown city)
    # would mean the catalogue didn't land; the failure we're guarding against.
    assert r.status_code in (200, 503), f"{city} failed at {r.status_code}: {r.text}"


def test_plan_response_reports_filter_match_counts() -> None:
    """A wheelchair+sensory filter set must prune below the catalogue, the
    signal /plan reports so the UI can warn when filters collapse the pool."""
    from adaptivetouragent.retrieval.poi_index import load_city

    index = load_city("Singapore")
    matched_all = index.filter()
    matched_filtered = index.filter(require_wheelchair=True, require_low_stimulation=True)
    # The filtered pool is a strict, non-empty subset of the full catalogue.
    assert 0 < len(matched_filtered) < len(matched_all) == len(index.pois)


def test_plan_endpoint_rejects_unknown_city() -> None:
    """An obviously-bogus city name should 404 cleanly."""
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    body = {
        "profile": {"user_id": "x", "name": "X", "category_weights": {"park": 1.0}},
        "city": "Atlantis",
        "start_hour": 9,
        "end_hour": 19,
    }
    r = client.post("/plan", json=body)
    assert r.status_code == 404
    assert "city not found" in r.json().get("detail", "")


def test_plan_endpoint_accepts_new_profile_and_pace_fields() -> None:
    """The expanded ProfileIn + PlanRequest schema must validate the three
    new toggles (require_wheelchair, require_low_stimulation on the profile;
    pace on the request)."""
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    body = {
        "profile": {
            "user_id": "x",
            "name": "X",
            "category_weights": {"park": 1.0},
            "require_wheelchair": True,
            "require_low_stimulation": True,
            "dietary": ["vegetarian", "halal"],
        },
        "city": "Singapore",
        "start_hour": 9,
        "end_hour": 19,
        "pace": "relaxed",
    }
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OPENAI_API_KEY", None)
        r = client.post("/plan", json=body)
    # 422 (validation error) would mean the schema didn't accept the new
    # fields. We only want to rule that out.
    assert r.status_code != 422, r.text
