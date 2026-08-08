"""Sanity tests on the four critical contracts."""

from datetime import datetime

from adaptivetouragent.fusion.snapshot import (
    ContextSnapshot,
    UserState,
    WeatherReading,
)
from adaptivetouragent.itinerary.types import (
    DiffOp,
    Itinerary,
    PlanDiff,
    PlanDiffEntry,
    POIVisit,
)
from adaptivetouragent.replanner.types import ReplanRequest
from adaptivetouragent.signals.triggers.types import TriggerEvent


def test_poi_visit_duration() -> None:
    v = POIVisit(
        poi_id="x",
        name="X",
        arrive=datetime(2026, 1, 1, 10, 0),
        depart=datetime(2026, 1, 1, 11, 30),
        category="park",
    )
    assert v.duration_min == 90.0


def test_plan_diff_counts() -> None:
    diff = PlanDiff(
        entries=[
            PlanDiffEntry(op=DiffOp.PRESERVE, poi_id="a", name="A"),
            PlanDiffEntry(op=DiffOp.DROP, poi_id="b", name="B"),
            PlanDiffEntry(op=DiffOp.INSERT, poi_id="c", name="C"),
            PlanDiffEntry(op=DiffOp.INSERT, poi_id="d", name="D"),
        ],
        summary="x",
    )
    assert diff.n_dropped == 1
    assert diff.n_inserted == 2


def test_context_snapshot_ablation_flags() -> None:
    snap = ContextSnapshot(
        snapshot_id="s1",
        at=datetime(2026, 1, 1),
        city="Singapore",
        weather=None,
        crowd={},
        transit={},
        user=UserState(fatigue_0_1=0.0, elapsed_min=0.0, pois_visited=0, last_break_min_ago=None),
    )
    assert not snap.has_weather()
    assert not snap.has_crowd()
    assert not snap.has_transit()


def test_replan_request_round_trip() -> None:
    snap = ContextSnapshot(
        snapshot_id="s1",
        at=datetime(2026, 1, 1),
        city="Singapore",
        weather=WeatherReading(28.0, 0.0, "clear", datetime(2026, 1, 1), "synthetic"),
        crowd={},
        transit={},
        user=UserState(fatigue_0_1=0.2, elapsed_min=60.0, pois_visited=1, last_break_min_ago=None),
    )
    plan = Itinerary(
        city="Singapore", user_id="u", visits=[], total_minutes=0.0, total_score=0.0, plan_id="p1"
    )
    req = ReplanRequest(
        current=plan,
        executed_prefix=[],
        snapshot=snap,
        triggers=[
            TriggerEvent(
                kind="user_request",
                severity="info",
                at=datetime(2026, 1, 1),
                affects=[],
                details={},
                snapshot_id="s1",
            )
        ],
        now=datetime(2026, 1, 1, 10),
    )
    assert req.snapshot.has_weather()
    assert req.triggers[0].kind == "user_request"
