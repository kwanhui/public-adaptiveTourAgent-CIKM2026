"""Scripted demo runner: replays a recorded signal trace end-to-end.

Usage:
    python3 -m adaptivetouragent.demo --scenario=family-rainy-day --log /tmp/run.jsonl
"""

import argparse
import asyncio
import sys
from datetime import datetime, time
from pathlib import Path

from adaptivetouragent.llm.openai_client import OpenAIClient
from adaptivetouragent.logging_.event_log import EventLog
from adaptivetouragent.replanner.initial import _coerce_profile, plan_initial
from adaptivetouragent.replanner.loop import LoopConfig, LoopState, step
from adaptivetouragent.retrieval.poi_index import load_city
from adaptivetouragent.signals.sources.recorded import RecordedSource


def _resolve_scenario(name: str) -> tuple[Path, dict]:
    """Locate the scenario JSONL trace + manifest."""
    here = Path(__file__).resolve().parent.parent.parent
    candidates = [
        here / "demo" / "sample-inputs" / "scenarios" / f"{name}.jsonl",
        here / "demo" / "scenarios" / f"{name}.jsonl",
        Path(name),
    ]
    for p in candidates:
        if p.is_file():
            return p, _scenario_manifest(name)
    raise FileNotFoundError(f"Scenario '{name}' not found. Tried: {[str(p) for p in candidates]}")


def _scenario_manifest(name: str) -> dict:
    """Per-scenario defaults so each scenario name implies a profile + city."""
    here = Path(__file__).resolve().parent.parent.parent
    manifests = {
        "family-rainy-day": {
            "city": "Singapore",
            "profile_path": here / "demo" / "sample-inputs" / "profile-family.yaml",
            "start_hour": 9,
            "end_hour": 19,
        },
        "solo-crowded-museum": {
            "city": "Singapore",
            "profile_path": here / "demo" / "sample-inputs" / "profile-solo.yaml",
            "start_hour": 10,
            "end_hour": 19,
        },
        "couple-transit-disruption": {
            "city": "Singapore",
            "profile_path": here / "demo" / "sample-inputs" / "profile-couple.yaml",
            "start_hour": 11,
            "end_hour": 21,
        },
    }
    if name not in manifests:
        raise KeyError(f"No manifest for scenario '{name}'.")
    return manifests[name]


async def run(args: argparse.Namespace) -> int:
    trace_path, manifest = _resolve_scenario(args.scenario)
    profile = _coerce_profile(str(manifest["profile_path"]))
    index = load_city(manifest["city"])
    llm = OpenAIClient(model=args.model)

    today = datetime.now().date()
    start_time = datetime.combine(today, time(hour=manifest["start_hour"]))
    budget = (manifest["end_hour"] - manifest["start_hour"]) * 60.0

    log = EventLog(args.log)
    log.write("demo_start", scenario=args.scenario, city=manifest["city"], trace=str(trace_path))

    plan = await plan_initial(
        profile=profile,
        index=index,
        start_time=start_time,
        budget_minutes=budget,
        llm=llm,
    )
    log.write("initial_plan", n_visits=len(plan.visits), score=plan.total_score)

    source = RecordedSource(trace_path)
    cfg = LoopConfig(
        profile=profile,
        index=index,
        llm=llm,
        sources=[source],
        start_time=start_time,
        budget_minutes=budget,
        log=log,
    )
    state = LoopState(plan=plan)

    times = source.all_event_times or [start_time]
    if not times:
        times = [start_time]

    for at in times:
        # Make sure simulated time advances past the start.
        if at < start_time:
            at = start_time
        await step(cfg, state, at=at)

    summary = llm.get_usage_summary()
    log.write(
        "demo_done",
        n_replans=state.n_replans,
        final_visits=len(state.plan.visits),
        cost_usd=summary["estimated_cost_usd"],
    )
    print(
        f"[demo_done] scenario={args.scenario} replans={state.n_replans} "
        f"cost=${summary['estimated_cost_usd']:.4f}",
        file=sys.stderr,
    )

    await source.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="adaptivetouragent.demo")
    p.add_argument("--scenario", required=True, help="Scenario name (family-rainy-day, ...)")
    p.add_argument("--model", default="gpt-4o-mini")
    p.add_argument("--log", help="JSONL event log path (default: stdout)")
    args = p.parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
