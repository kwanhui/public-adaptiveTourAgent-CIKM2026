"""Command-line interface.

Sub-commands:
  plan                - itinerary from a profile (single-day or multi-day)
  find-accommodation  - LLM-driven accommodation matcher
  demo                - scripted demo runs against recorded signal traces
"""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import date, datetime, time
from pathlib import Path

from adaptivetouragent.accommodations.agent import pick_accommodation
from adaptivetouragent.accommodations.index import (
    filter_by_hard_constraints,
    load_accommodations,
)
from adaptivetouragent.accommodations.types import Accommodation, AccommodationRequest
from adaptivetouragent.llm.openai_client import OpenAIClient
from adaptivetouragent.logging_.event_log import EventLog
from adaptivetouragent.replanner.initial import (
    _coerce_profile,
    plan_initial,
    plan_multi_day,
)
from adaptivetouragent.retrieval.poi_index import load_city


def _datetime_default(obj: object) -> object:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    raise TypeError(f"Not serialisable: {type(obj).__name__}")


def _load_accommodation_from_path(path: str) -> Accommodation:
    """Read an Accommodation from a JSON file (output of `find-accommodation`)."""
    with Path(path).open(encoding="utf-8") as f:
        data = json.load(f)
    if "accommodation" in data:  # AccommodationChoice payload
        data = data["accommodation"]
    return Accommodation(
        accommodation_id=str(data["accommodation_id"]),
        name=data["name"],
        lat=float(data["lat"]),
        lon=float(data["lon"]),
        price_per_night_usd=float(data["price_per_night_usd"]),
        rating=float(data["rating"]),
        kid_friendly=bool(data.get("kid_friendly", False)),
        near_mrt=bool(data.get("near_mrt", False)),
        description=data.get("description", ""),
        amenities=tuple(data.get("amenities", [])),
    )


async def _cmd_plan(args: argparse.Namespace) -> int:
    profile = _coerce_profile(args.user)
    index = load_city(args.city)
    llm = OpenAIClient(model=args.model)
    log = EventLog(args.log)

    accommodation: Accommodation | None = None
    if args.accommodation:
        accommodation = _load_accommodation_from_path(args.accommodation)

    multi_day = args.start_datetime is not None or args.end_datetime is not None
    if multi_day and (args.start_datetime is None or args.end_datetime is None):
        print("--start-datetime and --end-datetime must be passed together", file=sys.stderr)
        return 2

    if multi_day:
        start_dt = datetime.fromisoformat(args.start_datetime)
        end_dt = datetime.fromisoformat(args.end_datetime)
        log.write(
            "plan_start",
            mode="multi-day",
            city=args.city,
            user_id=profile.user_id,
            model=args.model,
            start=start_dt.isoformat(),
            end=end_dt.isoformat(),
            accommodation=accommodation.accommodation_id if accommodation else None,
        )
        multi_result = await plan_multi_day(
            profile=profile,
            index=index,
            start_datetime=start_dt,
            end_datetime=end_dt,
            llm=llm,
            accommodation=accommodation,
            daily_start_hour=args.start_hour,
            daily_end_hour=args.end_hour,
        )
        log.write(
            "plan_done", mode="multi-day",
            n_days=multi_result.n_days, n_visits=multi_result.n_visits,
        )
        summary_msg = (
            f"days={multi_result.n_days} visits={multi_result.n_visits} "
            f"score={multi_result.total_score:.3f}"
        )
        payload = asdict(multi_result)
    else:
        today = datetime.now().date()
        start_time = datetime.combine(today, time(hour=args.start_hour))
        budget_minutes = (args.end_hour - args.start_hour) * 60.0 * args.days
        log.write(
            "plan_start",
            mode="single-day",
            city=args.city,
            user_id=profile.user_id,
            model=args.model,
            accommodation=accommodation.accommodation_id if accommodation else None,
        )
        single_result = await plan_initial(
            profile=profile,
            index=index,
            start_time=start_time,
            budget_minutes=budget_minutes,
            llm=llm,
            start_location=accommodation,
        )
        log.write("plan_done", mode="single-day", n_visits=len(single_result.visits))
        summary_msg = (
            f"visits={len(single_result.visits)} score={single_result.total_score:.3f}"
        )
        payload = asdict(single_result)

    text = json.dumps(payload, default=_datetime_default, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)

    summary = llm.get_usage_summary()
    print(
        f"\n[plan_done] {summary_msg} cost=${summary['estimated_cost_usd']:.4f}",
        file=sys.stderr,
    )
    return 0


async def _cmd_find_accommodation(args: argparse.Namespace) -> int:
    profile = _coerce_profile(args.user)
    accommodations = load_accommodations(args.city)
    llm = OpenAIClient(model=args.model)

    request = AccommodationRequest(
        max_price_per_night_usd=args.max_price,
        min_rating=args.min_rating,
        require_kid_friendly=args.kid_friendly,
        require_near_mrt=args.near_mrt,
        notes=args.notes or "",
    )

    candidates = filter_by_hard_constraints(accommodations, request)
    if not candidates:
        print(
            "No accommodations match the hard constraints. "
            "Try relaxing --max-price / --min-rating or removing flags.",
            file=sys.stderr,
        )
        return 1

    choice = await pick_accommodation(
        candidates=candidates,
        request=request,
        profile=profile,
        llm=llm,
    )
    if choice is None:
        print("Pick failed (no candidates).", file=sys.stderr)
        return 1

    payload = {
        "accommodation": asdict(choice.accommodation),
        "score": choice.score,
        "rationale": choice.rationale,
        "cost_usd": choice.cost_usd,
        "candidates_after_filter": len(candidates),
        "candidates_before_filter": len(accommodations),
    }
    text = json.dumps(payload, default=_datetime_default, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)

    print(
        f"\n[find_accommodation_done] picked={choice.accommodation.name!r} "
        f"score={choice.score:.3f} cost=${choice.cost_usd:.4f}",
        file=sys.stderr,
    )
    return 0


async def _cmd_demo(args: argparse.Namespace) -> int:
    from adaptivetouragent.demo import run as demo_run

    return await demo_run(args)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="adaptivetouragent")
    sub = p.add_subparsers(dest="command", required=True)

    plan = sub.add_parser(
        "plan",
        help="Generate an itinerary (single-day, or multi-day with --start-datetime/--end-datetime)",
    )
    plan.add_argument("--user", required=True, help="Path to a YAML user profile")
    plan.add_argument("--city", required=True, help="City name (matches data/cities/<city>.json)")
    plan.add_argument("--days", type=int, default=1, help="Number of days (single-day mode only)")
    plan.add_argument("--start-hour", type=int, default=9, dest="start_hour")
    plan.add_argument("--end-hour", type=int, default=19, dest="end_hour")
    plan.add_argument(
        "--start-datetime",
        dest="start_datetime",
        default=None,
        help="ISO datetime, e.g. 2026-06-01T09:00 (switches to multi-day mode when paired with --end-datetime)",
    )
    plan.add_argument(
        "--end-datetime",
        dest="end_datetime",
        default=None,
        help="ISO datetime, e.g. 2026-06-03T18:00",
    )
    plan.add_argument(
        "--accommodation",
        default=None,
        help="Path to JSON describing the accommodation (output of `find-accommodation`)",
    )
    plan.add_argument("--model", default="gpt-4o-mini")
    plan.add_argument("--out", help="Write JSON to this file (default: stdout)")
    plan.add_argument("--log", help="JSONL event log path (default: stdout)")
    plan.set_defaults(func=_cmd_plan)

    fa = sub.add_parser(
        "find-accommodation",
        help="LLM-driven accommodation matcher: filter by hard constraints, score with LLM, return the top pick",
    )
    fa.add_argument("--user", required=True, help="Path to a YAML user profile")
    fa.add_argument("--city", required=True)
    fa.add_argument("--max-price", type=float, default=None, dest="max_price")
    fa.add_argument("--min-rating", type=float, default=0.0, dest="min_rating")
    fa.add_argument("--kid-friendly", action="store_true", dest="kid_friendly")
    fa.add_argument("--near-mrt", action="store_true", dest="near_mrt")
    fa.add_argument("--notes", default="", help="Free-text preferences for the LLM")
    fa.add_argument("--model", default="gpt-4o-mini")
    fa.add_argument("--out", help="Write JSON to this file (default: stdout)")
    fa.set_defaults(func=_cmd_find_accommodation)

    demo = sub.add_parser("demo", help="Run a scripted demo scenario")
    demo.add_argument("--scenario", required=True, help="Scenario name (family-rainy-day, ...)")
    demo.add_argument("--model", default="gpt-4o-mini")
    demo.add_argument("--log", help="JSONL event log path (default: stdout)")
    demo.set_defaults(func=_cmd_demo)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    result: int = asyncio.run(args.func(args))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
