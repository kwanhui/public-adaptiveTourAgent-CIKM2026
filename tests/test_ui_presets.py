"""Smoke tests for the UI presets + sidebar overflow guards.

These verify that the served HTML/CSS/JS carry the expected markup and
class hooks. They do not exercise the JavaScript runtime; for that, a
headless browser would be needed (out of scope for unit tests).
"""

from fastapi.testclient import TestClient

from adaptivetouragent.ui.server import create_app


def test_html_lists_four_profile_presets() -> None:
    client = TestClient(create_app())
    html = client.get("/").text
    for value in ("solo", "couple", "friends", "family"):
        assert f'value="{value}"' in html, f"missing preset option: {value}"


def test_html_party_size_carries_info_icon() -> None:
    client = TestClient(create_app())
    html = client.get("/").text
    # Tooltip text mentions all four lock/min states.
    assert "Locked to 1 for Solo" in html
    assert "2 for Couple" in html
    assert "Friends" in html
    assert "Family" in html


def test_js_defines_all_four_presets_with_party_constraints() -> None:
    client = TestClient(create_app())
    js = client.get("/static/app.js").text
    for preset in ("ui_solo", "ui_couple", "ui_friends", "ui_family"):
        assert preset in js, f"preset {preset} missing in app.js"
    # Constraint helpers must be present.
    assert "applyPresetConstraints" in js
    assert "initPresetSelect" in js
    # Solo and Couple must be locked; Friends and Family must not be.
    # Two PRESETS entries set `locked: true` (solo + couple); the rest of the
    # JS uses `locked: false` as a default in helper fall-throughs.
    assert js.count("locked: true") == 2
    assert "locked: false" in js


def test_css_has_overflow_guards_for_sidebar_form() -> None:
    """Sidebar form must not overflow when fields are wider than the column."""
    client = TestClient(create_app())
    css = client.get("/static/styles.css").text
    # Grid columns clamped with minmax(0, 1fr) so the columns can shrink.
    assert "minmax(0, 1fr)" in css
    # Field children carry min-width: 0 and width: 100%.
    assert "min-width: 0" in css
    assert "width: 100%" in css
    # Disabled inputs have a distinct visual state for locked party-size.
    assert ".field input:disabled" in css


def test_html_party_size_min_attribute_default_two() -> None:
    """Default min on the party-size input is the lower of the visible presets (2)."""
    client = TestClient(create_app())
    html = client.get("/").text
    # The Friends preset is selected by default and requires party >= 2.
    assert 'id="family-size" type="number" value="2" min="2"' in html
