"""Per-IP rate limiter middleware tests."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaptivetouragent.ui.middleware import PerIPRateLimiter


def _make_app(max_per_hour: int) -> TestClient:
    app = FastAPI()
    app.add_middleware(PerIPRateLimiter, max_per_hour=max_per_hour, paths=("/plan",))

    @app.post("/plan")
    async def plan() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app)


def test_under_cap_allows_through() -> None:
    client = _make_app(max_per_hour=3)
    for _ in range(3):
        r = client.post("/plan", json={})
        assert r.status_code == 200


def test_over_cap_returns_429() -> None:
    client = _make_app(max_per_hour=2)
    assert client.post("/plan", json={}).status_code == 200
    assert client.post("/plan", json={}).status_code == 200
    blocked = client.post("/plan", json={})
    assert blocked.status_code == 429
    assert "rate limit" in blocked.json().get("detail", "")
    assert blocked.headers.get("retry-after") is not None


def test_other_routes_not_limited() -> None:
    client = _make_app(max_per_hour=1)
    assert client.post("/plan", json={}).status_code == 200
    # Even after the cap, /healthz remains free.
    for _ in range(5):
        assert client.get("/healthz").status_code == 200


def test_zero_cap_disables_limiter() -> None:
    client = _make_app(max_per_hour=0)
    for _ in range(20):
        assert client.post("/plan", json={}).status_code == 200
