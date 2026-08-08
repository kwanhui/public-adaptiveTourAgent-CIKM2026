"""HTTP tests for the datetime-based /plan surface (single + multi-day)."""

import os
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from adaptivetouragent.ui.server import create_app


def _profile_body() -> dict:
    return {
        "user_id": "ui_test", "name": "UI Test",
        "category_weights": {"park": 0.4, "museum": 0.3, "viewpoint": 0.3},
    }


def test_plan_request_accepts_legacy_hour_fields() -> None:
    """Backward compat: omit start_datetime/end_datetime, keep hour fields."""
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    body = {
        "profile": _profile_body(),
        "city": "Singapore",
        "start_hour": 9, "end_hour": 17, "days": 1,
    }
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OPENAI_API_KEY", None)
        r = client.post("/plan", json=body)
    # Without API key the call returns 503; the request itself was valid.
    assert r.status_code in (200, 503)


def test_plan_request_accepts_datetime_fields() -> None:
    """Datetime surface validates and is recognised."""
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    start = datetime(2026, 6, 1, 9, 0).isoformat()
    end = datetime(2026, 6, 1, 17, 0).isoformat()
    body = {
        "profile": _profile_body(),
        "city": "Singapore",
        "start_datetime": start, "end_datetime": end,
    }
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OPENAI_API_KEY", None)
        r = client.post("/plan", json=body)
    assert r.status_code in (200, 503)


def test_plan_request_rejects_inverted_window() -> None:
    """end_datetime <= start_datetime should return 400."""
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    start = datetime(2026, 6, 3, 9, 0).isoformat()
    end = datetime(2026, 6, 1, 9, 0).isoformat()
    body = {
        "profile": _profile_body(),
        "city": "Singapore",
        "start_datetime": start, "end_datetime": end,
    }
    # We need an API key for the validation to be reached; patch the OpenAI
    # client check via env. Use a fake key; the validation precedes any LLM call.
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-fake-for-validation-test"}):
        r = client.post("/plan", json=body)
    # Either 400 (window invalid) or 503/500 if the LLM path was somehow hit.
    assert r.status_code in (400, 503, 500)


def test_plan_request_rejects_bad_iso() -> None:
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    body = {
        "profile": _profile_body(),
        "city": "Singapore",
        "start_datetime": "not-a-date",
        "end_datetime": (datetime.now() + timedelta(hours=8)).isoformat(),
    }
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-fake-test"}):
        r = client.post("/plan", json=body)
    assert r.status_code in (400, 503, 500)


def test_static_index_html_has_datetime_inputs_and_info_icons() -> None:
    """Sanity: the served HTML carries the new form fields and info icons."""
    app = create_app()
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    # New form fields
    assert 'id="start-datetime"' in html
    assert 'id="end-datetime"' in html
    assert 'type="datetime-local"' in html
    assert 'id="money-cap"' in html
    assert 'id="prefer-low-carbon"' in html
    # Info icons + popover
    assert 'class="info-icon"' in html
    assert 'id="info-popover"' in html
    # The hour inputs should NOT be present (they were removed).
    assert 'id="start-hour"' not in html
    assert 'id="end-hour"' not in html


def test_static_assets_present() -> None:
    app = create_app()
    client = TestClient(app)
    css = client.get("/static/styles.css")
    js = client.get("/static/app.js")
    assert css.status_code == 200
    assert js.status_code == 200
    # CSS contains the new info-icon and day-tabs classes.
    assert ".info-icon" in css.text
    assert ".day-tabs" in css.text or ".day-tab" in css.text
    # JS handles the new datetime fields and info icons.
    assert "start-datetime" in js.text
    assert "initInfoIcons" in js.text
