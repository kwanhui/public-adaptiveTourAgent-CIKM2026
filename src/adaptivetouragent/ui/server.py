"""FastAPI server: chat panel + map view.

Endpoints:
  GET  /healthz                    -> {"ok": true}
  GET  /                           -> static index.html
  POST /plan                       -> initial single-day itinerary, opens a session
  POST /replan/{sid}               -> inject a structured trigger (button-driven)
  POST /chat/{sid}                 -> free-text mid-trip refinement
  GET  /events/{sid}               -> SSE stream of snapshot/trigger/replan events
  DELETE /sessions/{sid}           -> end a session
  POST /find-accommodation         -> LLM-driven accommodation matcher (single shot)
  GET  /accommodations/{city}      -> list bundled accommodations for a city

The server is single-tenant by design: one demo session at a time. A real
deployment would multiplex over `session_id`.
"""

import asyncio
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from adaptivetouragent.accommodations.agent import pick_accommodation
from adaptivetouragent.accommodations.index import (
    filter_by_hard_constraints,
    load_accommodations,
)
from adaptivetouragent.accommodations.types import AccommodationRequest
from adaptivetouragent.agent.types import (
    AccessibilityRequirements,
    GroupMember,
    UserProfile,
)
from adaptivetouragent.booking.actuator import BookingActuator, BookingRecord
from adaptivetouragent.itinerary.types import Itinerary
from adaptivetouragent.llm.openai_client import OpenAIClient
from adaptivetouragent.logging_.event_log import EventLog
from adaptivetouragent.replanner.edit import remove_and_reroute
from adaptivetouragent.replanner.initial import plan_initial, plan_multi_day
from adaptivetouragent.replanner.loop import LoopConfig, LoopState, step
from adaptivetouragent.retrieval.poi_index import load_city
from adaptivetouragent.signals.note_parser import interpret_note, note_to_signals
from adaptivetouragent.signals.sources.base import SignalSource
from adaptivetouragent.signals.sources.crowd_synth import SyntheticCrowdSource
from adaptivetouragent.signals.sources.manual import ManualSignalSource
from adaptivetouragent.ui.middleware import PerIPRateLimiter
from adaptivetouragent.ui.schemas import (
    AccommodationOut,
    BookingOut,
    BookingRequestIn,
    ChatMessage,
    DayItineraryOut,
    FindAccommodationRequest,
    FindAccommodationResponse,
    GroupVetoIn,
    ItineraryOut,
    PlanRequest,
    PlanResponse,
    POIVisitOut,
    RemoveStopIn,
    ReplanTrigger,
)

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_USD_PER_SESSION = float(os.environ.get("MAX_USD_PER_SESSION", "0.50"))


def _visits_out(visits, index) -> list[POIVisitOut]:
    out: list[POIVisitOut] = []
    for v in visits:
        poi = index.pois[v.poi_id]
        out.append(
            POIVisitOut(
                poi_id=v.poi_id,
                name=v.name,
                category=v.category,
                arrive=v.arrive,
                depart=v.depart,
                lat=poi.lat,
                lon=poi.lon,
                entry_fee_usd=v.entry_fee_usd,
                travel_cost_usd=v.travel_cost_usd,
                travel_co2e_kg=v.travel_co2e_kg,
                travel_mode=v.travel_mode,
                travel_distance_km=v.travel_distance_km,
                inbound_geometry=[list(pt) for pt in v.inbound_geometry],
                reasoning_text=v.reasoning_text,
                reasoning_scores=v.reasoning_scores,
                alternatives_considered=list(v.alternatives_considered),
            )
        )
    return out


def _itin_out(plan: Itinerary, index) -> ItineraryOut:
    return ItineraryOut(
        plan_id=plan.plan_id,
        derived_from=plan.derived_from,
        city=plan.city,
        user_id=plan.user_id,
        visits=_visits_out(plan.visits, index),
        total_minutes=plan.total_minutes,
        total_score=plan.total_score,
        total_cost_usd=plan.total_cost_usd,
        total_co2e_kg=plan.total_co2e_kg,
    )


class Session:
    def __init__(self, sid: str, cfg: LoopConfig, state: LoopState):
        self.sid = sid
        self.cfg = cfg
        self.state = state
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.task: asyncio.Task | None = None
        self.scenario_path: Path | None = None
        # Sandboxed, dry-run booking actuator with an in-memory audit trail
        # (synthetic confirmation codes, no external calls).
        self.actuator = BookingActuator(dry_run=True)

    async def push_event(self, event_type: str, payload: dict[str, Any]) -> None:
        await self.queue.put({"event": event_type, "data": json.dumps(payload, default=_json_default)})

    def cost_usd(self) -> float:
        summary = self.cfg.llm.get_usage_summary()
        return float(summary.get("estimated_cost_usd", 0.0)) if isinstance(summary, dict) else 0.0


def _json_default(obj: object) -> object:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    raise TypeError(f"Not serialisable: {type(obj).__name__}")


def create_app() -> FastAPI:
    app = FastAPI(title="AdaptTour")

    max_plans_per_hour = int(os.environ.get("MAX_PLANS_PER_HOUR", "0"))
    if max_plans_per_hour > 0:
        # Both of these spend on the LLM. /find-accommodation takes no session,
        # so the per-session cost cap cannot reach it and the rate limiter is
        # the only thing bounding it.
        app.add_middleware(
            PerIPRateLimiter,
            max_per_hour=max_plans_per_hour,
            paths=("/plan", "/find-accommodation"),
        )

    sessions: dict[str, Session] = {}
    app.state.sessions = sessions

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/")
    async def root() -> FileResponse:
        index_path = STATIC_DIR / "index.html"
        if not index_path.is_file():
            raise HTTPException(404, "UI assets missing")
        return FileResponse(index_path)

    def _profile_from_in(p) -> UserProfile:
        """Lift a wire-side `ProfileIn` into the internal `UserProfile`.

        Folds the three accessibility fields on `ProfileIn` into a single
        `AccessibilityRequirements` so the retriever's filter (and the
        optimiser's mode picker, for `require_wheelchair`) read a single
        canonical struct.
        """
        accessibility = AccessibilityRequirements(
            require_wheelchair=p.require_wheelchair,
            dietary=tuple(p.dietary),
            require_low_stimulation=p.require_low_stimulation,
        )
        members = tuple(
            GroupMember(
                member_id=f"m{i}",
                name=m.name,
                # An empty member ballot votes with the group's overall taste,
                # so the per-member input that matters is the veto/boost.
                category_weights=dict(m.category_weights) or dict(p.category_weights),
                veto_categories=tuple(m.veto_categories),
                boost_categories=tuple(m.boost_categories),
            )
            for i, m in enumerate(getattr(p, "group_members", []) or [])
        )
        return UserProfile(
            user_id=p.user_id,
            name=p.name,
            category_weights=p.category_weights,
            family_size=p.family_size,
            require_kid_friendly=p.require_kid_friendly,
            notes=p.notes,
            accessibility=accessibility,
            group_members=members,
        )

    @app.post("/plan")
    async def plan(req: PlanRequest) -> PlanResponse:
        profile = _profile_from_in(req.profile)
        try:
            index = load_city(req.city)
        except FileNotFoundError as e:
            raise HTTPException(404, f"city not found: {req.city}") from e

        # Resolve trip window FIRST (cheap input validation), so bad inputs
        # surface as 400/404 even without an OPENAI_API_KEY in the env.
        if req.start_datetime and req.end_datetime:
            try:
                start_dt = datetime.fromisoformat(req.start_datetime)
                end_dt = datetime.fromisoformat(req.end_datetime)
            except ValueError as e:
                raise HTTPException(400, f"invalid datetime: {e}") from e
            if end_dt <= start_dt:
                raise HTTPException(400, "end_datetime must be after start_datetime")
        else:
            today = datetime.now().date()
            start_dt = datetime.combine(today, time(hour=req.start_hour))
            end_dt = start_dt + timedelta(minutes=(req.end_hour - req.start_hour) * 60.0 * max(1, req.days))

        try:
            llm = OpenAIClient(model=req.model)
        except (ValueError, ImportError) as e:
            raise HTTPException(503, f"LLM unavailable: {e}") from e

        is_multi = start_dt.date() != end_dt.date()
        sid = uuid.uuid4().hex[:12]
        # The manual source lets the UI's button row + chat box push typed
        # signals (weather, transit, fatigue) into the same fusion pipeline
        # as the synthetic crowd feed; see `/replan` and `/chat` below.
        sources: list[SignalSource] = [
            SyntheticCrowdSource(list(index.pois.values())),
            ManualSignalSource(),
        ]
        days_out: list[DayItineraryOut] = []

        if is_multi:
            multi = await plan_multi_day(
                profile=profile,
                index=index,
                start_datetime=start_dt,
                end_datetime=end_dt,
                llm=llm,
                daily_start_hour=req.start_hour,
                daily_end_hour=req.end_hour,
                money_budget_usd=req.money_budget_usd,
                prefer_low_carbon=req.prefer_low_carbon,
                pace=req.pace,
            )
            for d in multi.days:
                visits_out = _visits_out(d.visits, index)
                days_out.append(
                    DayItineraryOut(
                        day_index=d.day_index,
                        date=d.date.isoformat(),
                        start_time=d.start_time,
                        end_time=d.end_time,
                        visits=visits_out,
                        total_minutes=d.total_minutes,
                        total_score=d.total_score,
                        total_cost_usd=sum(v.entry_fee_usd + v.travel_cost_usd for v in d.visits),
                        total_co2e_kg=sum(v.travel_co2e_kg for v in d.visits),
                    )
                )
            # Active itinerary for the live loop is the first day.
            from adaptivetouragent.itinerary.types import Itinerary

            first = multi.days[0] if multi.days else None
            active = Itinerary(
                city=index.city,
                user_id=profile.user_id,
                visits=first.visits if first else [],
                total_minutes=first.total_minutes if first else 0.0,
                total_score=first.total_score if first else 0.0,
                plan_id=first.plan_id if first else uuid.uuid4().hex[:12],
            )
            cfg_start = first.start_time if first else start_dt
            cfg_budget = (first.end_time - first.start_time).total_seconds() / 60.0 if first else 0.0
        else:
            active = await plan_initial(
                profile=profile,
                index=index,
                start_time=start_dt,
                budget_minutes=(end_dt - start_dt).total_seconds() / 60.0,
                llm=llm,
                money_budget_usd=req.money_budget_usd,
                prefer_low_carbon=req.prefer_low_carbon,
                pace=req.pace,
            )
            days_out.append(
                DayItineraryOut(
                    day_index=0,
                    date=start_dt.date().isoformat(),
                    start_time=start_dt,
                    end_time=end_dt,
                    visits=_visits_out(active.visits, index),
                    total_minutes=active.total_minutes,
                    total_score=active.total_score,
                    total_cost_usd=active.total_cost_usd,
                    total_co2e_kg=active.total_co2e_kg,
                )
            )
            cfg_start = start_dt
            cfg_budget = (end_dt - start_dt).total_seconds() / 60.0

        cfg = LoopConfig(
            profile=profile,
            index=index,
            llm=llm,
            sources=sources,
            start_time=cfg_start,
            budget_minutes=cfg_budget,
            log=EventLog(None),
            money_budget_usd=req.money_budget_usd,
            prefer_low_carbon=req.prefer_low_carbon,
            pace=req.pace,
        )
        state = LoopState(plan=active)
        session = Session(sid, cfg, state)
        sessions[sid] = session

        itin_out = _itin_out(active, index)
        total_cost = sum(d.total_cost_usd for d in days_out)
        total_co2 = sum(d.total_co2e_kg for d in days_out)
        # How many catalogue POIs survive the profile's hard filters, so the UI
        # can warn when accessibility/dietary/sensory filters prune the pool.
        matched = index.filter(
            kid_friendly=profile.require_kid_friendly or None,
            require_wheelchair=profile.accessibility.require_wheelchair or None,
            require_dietary=profile.accessibility.dietary,
            require_low_stimulation=profile.accessibility.require_low_stimulation or None,
        )

        await session.push_event(
            "initial_plan",
            {
                "itinerary": itin_out.model_dump(mode="json"),
                "days": [d.model_dump(mode="json") for d in days_out],
                "is_multi_day": is_multi,
            },
        )

        return PlanResponse(
            session_id=sid,
            itinerary=itin_out,
            days=days_out,
            is_multi_day=is_multi,
            total_cost_usd=total_cost,
            total_co2e_kg=total_co2,
            cost_usd=session.cost_usd(),
            candidates_matched=len(matched),
            catalogue_size=len(index.pois),
        )

    def _inject_note_signals(session: Session, note: str, at: datetime) -> None:
        """Translate a free-text note into typed signals on the session's manual source.

        Keyword-driven: "rain" injects a WeatherReading, "delay" injects a
        TransitReading on the next upcoming edge, "tired" bumps the fatigue
        offset that `step()` consumes on the next tick. The raw note also
        flows through `pref_changes` and reaches the LLM scoring prompt
        verbatim, so anything the parser misses still drives a replan
        through the LLM-scoring path.
        """
        if not note:
            return
        manual = next(
            (s for s in session.cfg.sources if isinstance(s, ManualSignalSource)),
            None,
        )
        if manual is None:
            return
        upcoming_ids = [v.poi_id for v in session.state.plan.visits if v.depart > at]
        batch, fatigue_boost = note_to_signals(note=note, at=at, upcoming_poi_ids=upcoming_ids)
        if batch.weather is not None or batch.crowd or batch.transit:
            manual.inject(batch)
        if fatigue_boost > 0.0:
            manual.add_fatigue(fatigue_boost)

    @app.post("/replan/{sid}")
    async def replan_endpoint(sid: str, body: ReplanTrigger) -> dict[str, Any]:
        session = sessions.get(sid)
        if session is None:
            raise HTTPException(404, "session not found")

        if session.cost_usd() > MAX_USD_PER_SESSION:
            raise HTTPException(429, f"session cost cap reached (${MAX_USD_PER_SESSION:.2f})")

        at = datetime.fromisoformat(body.advance_to_iso) if body.advance_to_iso else datetime.now()
        pref_changes = [body.note] if body.note else None
        _inject_note_signals(session, body.note, at)

        async def on_replan(response):
            await session.push_event(
                "replan",
                {
                    "itinerary": _itin_out(response.updated, session.cfg.index).model_dump(mode="json"),
                    "diff": response.diff.summary,
                    "rationale": response.rationale,
                    "cost_usd": response.cost_usd,
                },
            )

        triggers = await step(session.cfg, session.state, at=at, pref_changes=pref_changes, on_replan=on_replan)
        return {
            "fired": [t.kind for t in triggers],
            "interpretation": interpret_note(body.note) if body.note else "",
            "n_replans": session.state.n_replans,
            "cost_usd": session.cost_usd(),
        }

    @app.post("/chat/{sid}")
    async def chat_endpoint(sid: str, body: ChatMessage) -> dict[str, Any]:
        """Free-text mid-trip refinement.

        Routes the user's text into the same fusion pipeline as weather/crowd
        by emitting a `user_request` trigger with the text as the change note.
        """
        session = sessions.get(sid)
        if session is None:
            raise HTTPException(404, "session not found")
        if session.cost_usd() > MAX_USD_PER_SESSION:
            raise HTTPException(429, f"session cost cap reached (${MAX_USD_PER_SESSION:.2f})")
        if not body.text.strip():
            raise HTTPException(400, "empty message")

        at = datetime.fromisoformat(body.advance_to_iso) if body.advance_to_iso else datetime.now()
        _inject_note_signals(session, body.text, at)

        async def on_replan(response):
            await session.push_event(
                "replan",
                {
                    "itinerary": _itin_out(response.updated, session.cfg.index).model_dump(mode="json"),
                    "diff": response.diff.summary,
                    "rationale": response.rationale,
                    "cost_usd": response.cost_usd,
                    "source": "chat",
                    "user_message": body.text,
                },
            )

        triggers = await step(
            session.cfg,
            session.state,
            at=at,
            pref_changes=[body.text],
            on_replan=on_replan,
        )
        return {
            "fired": [t.kind for t in triggers],
            "interpretation": interpret_note(body.text),
            "n_replans": session.state.n_replans,
            "cost_usd": session.cost_usd(),
        }

    @app.post("/group-veto/{sid}")
    async def group_veto_endpoint(sid: str, body: GroupVetoIn) -> dict[str, Any]:
        """Cast a category veto mid-trip; the replanner drops it from the tail live.

        Demonstrates the group-dynamics claim: a single veto blocks the
        category for the whole party, and because it lands on the live session
        profile, every subsequent replan re-applies it (it is not a one-shot).
        """
        session = sessions.get(sid)
        if session is None:
            raise HTTPException(404, "session not found")
        if session.cost_usd() > MAX_USD_PER_SESSION:
            raise HTTPException(429, f"session cost cap reached (${MAX_USD_PER_SESSION:.2f})")
        category = body.category.strip().lower()
        if not category:
            raise HTTPException(400, "empty category")

        profile = session.cfg.profile
        if category not in profile.live_veto_categories:
            profile.live_veto_categories = (*profile.live_veto_categories, category)

        at = datetime.fromisoformat(body.advance_to_iso) if body.advance_to_iso else datetime.now()
        who = body.member.strip() or "a traveller"

        async def on_replan(response):
            await session.push_event(
                "replan",
                {
                    "itinerary": _itin_out(response.updated, session.cfg.index).model_dump(mode="json"),
                    "diff": response.diff.summary,
                    "rationale": response.rationale,
                    "cost_usd": response.cost_usd,
                    "source": "group-veto",
                    "user_message": f"{who} vetoed {category}",
                },
            )

        triggers = await step(
            session.cfg,
            session.state,
            at=at,
            pref_changes=[f"the group no longer wants any {category} stops"],
            on_replan=on_replan,
        )
        return {
            "fired": [t.kind for t in triggers],
            "vetoed": list(profile.live_veto_categories),
            "n_replans": session.state.n_replans,
            "cost_usd": session.cost_usd(),
        }

    @app.post("/remove-stop/{sid}")
    async def remove_stop_endpoint(sid: str, body: RemoveStopIn) -> dict[str, Any]:
        """Remove one stop by hand; the remaining stops re-route around the gap.

        Delete-only: clicking the cross means "cancel this stop", so the freed
        time is NOT backfilled with a new POI; the remaining stops keep their
        order and are simply re-timed. No LLM call. The removed POI is recorded
        on the live profile so any later agentic replan keeps it out too.
        """
        session = sessions.get(sid)
        if session is None:
            raise HTTPException(404, "session not found")
        poi_id = body.poi_id.strip()
        if not poi_id:
            raise HTTPException(400, "empty poi_id")

        profile = session.cfg.profile
        if poi_id not in profile.excluded_pois:
            profile.excluded_pois = (*profile.excluded_pois, poi_id)

        at = datetime.fromisoformat(body.advance_to_iso) if body.advance_to_iso else datetime.now()
        name = next((v.name for v in session.state.plan.visits if v.poi_id == poi_id), poi_id)

        new_plan = await remove_and_reroute(
            plan=session.state.plan,
            index=session.cfg.index,
            removed_poi_id=poi_id,
            at=at,
            start_time=session.cfg.start_time,
            budget_minutes=session.cfg.budget_minutes,
            money_budget_usd=session.cfg.money_budget_usd,
            party_size=max(1, profile.family_size),
            prefer_low_carbon=session.cfg.prefer_low_carbon,
            require_wheelchair=profile.accessibility.require_wheelchair,
            pace=session.cfg.pace,
        )

        session.state.history.append(session.state.plan)
        session.state.plan = new_plan
        n_remaining = len(new_plan.visits)
        await session.push_event(
            "replan",
            {
                "itinerary": _itin_out(new_plan, session.cfg.index).model_dump(mode="json"),
                "diff": f"{n_remaining} stops remain",
                "rationale": "Re-routed around the gap; nothing was added in its place.",
                "cost_usd": session.cost_usd(),
                "source": "remove-stop",
                "user_message": f"removed {name}",
            },
        )
        return {
            "removed": list(profile.excluded_pois),
            "n_stops": n_remaining,
            "cost_usd": session.cost_usd(),
        }

    @app.post("/undo/{sid}")
    async def undo_endpoint(sid: str) -> dict[str, Any]:
        """Restore the itinerary to the revision before the last change."""
        session = sessions.get(sid)
        if session is None:
            raise HTTPException(404, "session not found")
        if not session.state.history:
            raise HTTPException(409, "nothing to undo")
        session.state.plan = session.state.history.pop()
        await session.push_event(
            "replan",
            {
                "itinerary": _itin_out(session.state.plan, session.cfg.index).model_dump(mode="json"),
                "diff": "reverted",
                "rationale": "Reverted to the previous itinerary.",
                "cost_usd": session.cost_usd(),
                "source": "undo",
                "user_message": "undo",
            },
        )
        return {"undone": True, "history_depth": len(session.state.history)}

    def _booking_out(r: BookingRecord) -> BookingOut:
        return BookingOut(
            booking_id=r.booking_id,
            kind=r.kind,
            target_id=r.target_id,
            target_name=r.target_name,
            when=r.when,
            party_size=r.party_size,
            amount_usd=r.amount_usd,
            status=r.status,
            confirmation_code=r.confirmation_code,
        )

    @app.post("/book/{sid}")
    async def book_endpoint(sid: str, body: BookingRequestIn) -> BookingOut:
        """Sandboxed booking of one stop on the active itinerary.

        The dry-run actuator returns a synthetic confirmation code and appends
        to the session's in-memory audit trail (see GET /bookings/{sid}); no
        external service is contacted. Closes the loop past plan emission.
        """
        session = sessions.get(sid)
        if session is None:
            raise HTTPException(404, "session not found")
        visit = next((v for v in session.state.plan.visits if v.poi_id == body.poi_id), None)
        if visit is None:
            raise HTTPException(404, f"no stop {body.poi_id} on the active itinerary")
        record = session.actuator.book_poi_visit(visit, party_size=max(1, session.cfg.profile.family_size))
        await session.push_event(
            "booking",
            {"booking": _booking_out(record).model_dump(mode="json")},
        )
        return _booking_out(record)

    @app.get("/bookings/{sid}")
    async def list_bookings(sid: str) -> list[BookingOut]:
        """The session's booking audit trail."""
        session = sessions.get(sid)
        if session is None:
            raise HTTPException(404, "session not found")
        return [_booking_out(r) for r in session.actuator.records]

    @app.get("/events/{sid}")
    async def events(sid: str) -> EventSourceResponse:
        session = sessions.get(sid)
        if session is None:
            raise HTTPException(404, "session not found")

        async def event_generator() -> AsyncIterator[dict[str, str]]:
            while True:
                event = await session.queue.get()
                yield event

        return EventSourceResponse(event_generator())

    @app.delete("/sessions/{sid}")
    async def end_session(sid: str) -> dict[str, str]:
        session = sessions.pop(sid, None)
        if session is None:
            raise HTTPException(404, "session not found")
        if session.task is not None:
            session.task.cancel()
        return {"status": "ended"}

    @app.get("/accommodations/{city}")
    async def list_accommodations(city: str) -> list[AccommodationOut]:
        try:
            entries = load_accommodations(city)
        except FileNotFoundError as e:
            raise HTTPException(404, f"no accommodation catalogue for {city}") from e
        return [
            AccommodationOut(
                accommodation_id=a.accommodation_id,
                name=a.name,
                lat=a.lat,
                lon=a.lon,
                price_per_night_usd=a.price_per_night_usd,
                rating=a.rating,
                kid_friendly=a.kid_friendly,
                near_mrt=a.near_mrt,
                description=a.description,
                amenities=list(a.amenities),
            )
            for a in entries
        ]

    @app.post("/find-accommodation")
    async def find_accommodation(req: FindAccommodationRequest) -> FindAccommodationResponse:
        profile = _profile_from_in(req.profile)
        try:
            entries = load_accommodations(req.city)
        except FileNotFoundError as e:
            raise HTTPException(404, f"no accommodation catalogue for {req.city}") from e
        try:
            llm = OpenAIClient(model=req.model)
        except (ValueError, ImportError) as e:
            raise HTTPException(503, f"LLM unavailable: {e}") from e

        request = AccommodationRequest(
            max_price_per_night_usd=req.request.max_price_per_night_usd,
            min_rating=req.request.min_rating,
            require_kid_friendly=req.request.require_kid_friendly,
            require_near_mrt=req.request.require_near_mrt,
            notes=req.request.notes,
        )
        candidates = filter_by_hard_constraints(entries, request)
        if not candidates:
            raise HTTPException(404, "no accommodations match the hard constraints")

        choice = await pick_accommodation(
            candidates=candidates,
            request=request,
            profile=profile,
            llm=llm,
        )
        if choice is None:
            raise HTTPException(500, "matcher returned no choice")

        d = asdict(choice.accommodation)
        return FindAccommodationResponse(
            accommodation=AccommodationOut(
                accommodation_id=d["accommodation_id"],
                name=d["name"],
                lat=d["lat"],
                lon=d["lon"],
                price_per_night_usd=d["price_per_night_usd"],
                rating=d["rating"],
                kid_friendly=d["kid_friendly"],
                near_mrt=d["near_mrt"],
                description=d["description"],
                amenities=list(d["amenities"]),
            ),
            score=choice.score,
            rationale=choice.rationale,
            cost_usd=choice.cost_usd,
            candidates_after_filter=len(candidates),
            candidates_before_filter=len(entries),
        )

    return app


app = create_app()
