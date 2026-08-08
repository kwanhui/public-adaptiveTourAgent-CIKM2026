"""Pydantic models for the HTTP boundary.

Internal types stay as plain dataclasses (see fusion/snapshot.py and
itinerary/types.py); pydantic only crosses the wire.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class GroupMemberIn(BaseModel):
    """One traveller in a group, with their own veto/boost over POI categories.

    `category_weights` is optional; when empty the member votes with the
    group's overall taste, so the meaningful per-member input is the veto/boost.
    """

    name: str = "Traveller"
    category_weights: dict[str, float] = Field(default_factory=dict)
    veto_categories: list[str] = Field(default_factory=list)
    boost_categories: list[str] = Field(default_factory=list)


class ProfileIn(BaseModel):
    user_id: str = "anon"
    name: str = "Tourist"
    family_size: int = 1
    require_kid_friendly: bool = False
    notes: str = ""
    category_weights: dict[str, float] = Field(default_factory=dict)
    # Accessibility fields, flow into AccessibilityRequirements at the planner.
    require_wheelchair: bool = False
    require_low_stimulation: bool = False
    dietary: list[str] = Field(default_factory=list)
    # Per-member preferences for Friends/Family groups; empty for a solo party.
    group_members: list[GroupMemberIn] = Field(default_factory=list)


class PlanRequest(BaseModel):
    profile: ProfileIn
    city: str = "Singapore"
    # Legacy single-day surface (still honored when start_datetime is None).
    start_hour: int = 9
    end_hour: int = 19
    days: int = 1
    # Datetime surface for multi-day trips. When both are set the planner
    # picks single-day or multi-day based on whether the dates differ.
    start_datetime: str | None = None  # ISO 8601, e.g. "2026-06-01T09:00"
    end_datetime: str | None = None
    money_budget_usd: float | None = None
    prefer_low_carbon: bool = False
    pace: str = "standard"  # "relaxed" | "standard" | "packed"
    model: str = "gpt-4o-mini"


class POIVisitOut(BaseModel):
    poi_id: str
    name: str
    category: str
    arrive: datetime
    depart: datetime
    lat: float
    lon: float
    entry_fee_usd: float = 0.0
    travel_cost_usd: float = 0.0
    travel_co2e_kg: float = 0.0
    travel_mode: str | None = None  # "walk" | "cycle" | "transit" | "rideshare" | "drive"
    travel_distance_km: float = 0.0
    inbound_geometry: list[list[float]] = Field(default_factory=list)  # [[lat, lon], ...]
    reasoning_text: str = ""  # human-readable rationale (shown inline)
    reasoning_scores: str = ""  # numeric trace (shown via info icon)
    alternatives_considered: list[str] = Field(default_factory=list)


class ItineraryOut(BaseModel):
    plan_id: str
    derived_from: str | None = None
    city: str
    user_id: str
    visits: list[POIVisitOut]
    total_minutes: float
    total_score: float
    total_cost_usd: float = 0.0
    total_co2e_kg: float = 0.0


class DayItineraryOut(BaseModel):
    """One day of a multi-day plan, surfaced to the UI as its own panel."""

    day_index: int
    date: str  # YYYY-MM-DD
    start_time: datetime
    end_time: datetime
    visits: list[POIVisitOut]
    total_minutes: float
    total_score: float
    total_cost_usd: float = 0.0
    total_co2e_kg: float = 0.0


class PlanResponse(BaseModel):
    session_id: str
    # Single canonical itinerary for the *active* day (always the first day).
    # Replan events update this in place via the SSE stream.
    itinerary: ItineraryOut
    # Populated for multi-day plans; length 1 for single-day so the UI can
    # render uniformly. Each entry carries its own date, time window, and
    # totals so a multi-day trip can be displayed as day-by-day sections.
    days: list[DayItineraryOut] = Field(default_factory=list)
    is_multi_day: bool = False
    total_cost_usd: float = 0.0
    total_co2e_kg: float = 0.0
    cost_usd: float = 0.0  # LLM cost so far
    # How many catalogue POIs survive the profile's hard filters (wheelchair,
    # dietary, sensory, kid-friendly), and the catalogue size, so the UI can
    # warn when filters prune the pool to near-empty instead of silently
    # returning a short plan.
    candidates_matched: int = 0
    catalogue_size: int = 0


class ReplanTrigger(BaseModel):
    """Manually inject a replan trigger from the UI (e.g. user request)."""

    note: str = ""
    advance_to_iso: str | None = None  # simulated wall-clock for demo


class GroupVetoIn(BaseModel):
    """A category veto cast mid-trip by a group member, which re-routes the tail."""

    category: str
    member: str = ""  # display only; a single veto blocks the category for all
    advance_to_iso: str | None = None


class RemoveStopIn(BaseModel):
    """Remove one stop by hand; it is excluded from this and later revisions."""

    poi_id: str
    advance_to_iso: str | None = None


class BookingRequestIn(BaseModel):
    """Book a stop from the active itinerary (sandboxed, dry-run actuator)."""

    poi_id: str


class BookingOut(BaseModel):
    booking_id: str
    kind: str
    target_id: str
    target_name: str
    when: datetime
    party_size: int
    amount_usd: float
    status: str
    confirmation_code: str | None = None


class AccommodationRequestIn(BaseModel):
    max_price_per_night_usd: float | None = None
    min_rating: float = 0.0
    require_kid_friendly: bool = False
    require_near_mrt: bool = False
    notes: str = ""


class FindAccommodationRequest(BaseModel):
    profile: ProfileIn
    city: str = "Singapore"
    request: AccommodationRequestIn
    model: str = "gpt-4o-mini"


class AccommodationOut(BaseModel):
    accommodation_id: str
    name: str
    lat: float
    lon: float
    price_per_night_usd: float
    rating: float
    kid_friendly: bool
    near_mrt: bool
    description: str
    amenities: list[str] = Field(default_factory=list)


class FindAccommodationResponse(BaseModel):
    accommodation: AccommodationOut
    score: float
    rationale: str
    cost_usd: float
    candidates_after_filter: int
    candidates_before_filter: int


class ChatMessage(BaseModel):
    """Free-text mid-trip refinement."""

    text: str
    advance_to_iso: str | None = None
