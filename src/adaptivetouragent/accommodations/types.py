"""Accommodation types and search requirements."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Accommodation:
    """A bookable place to stay overnight."""

    accommodation_id: str
    name: str
    lat: float
    lon: float
    price_per_night_usd: float
    rating: float  # 0..5
    kid_friendly: bool
    near_mrt: bool
    description: str = ""
    amenities: tuple[str, ...] = ()


@dataclass
class AccommodationRequest:
    """Tourist's stated requirements for accommodation.

    Hard constraints (price, rating, kid_friendly, near_mrt, near_lat/lon)
    filter the candidate list before LLM scoring. Soft preferences
    (`notes`) flow into the scoring prompt.
    """

    max_price_per_night_usd: float | None = None
    min_rating: float = 0.0
    require_kid_friendly: bool = False
    require_near_mrt: bool = False
    near_lat: float | None = None
    near_lon: float | None = None
    near_radius_km: float = 5.0
    notes: str = ""
    amenities: tuple[str, ...] = field(default_factory=tuple)  # required amenities


@dataclass
class AccommodationChoice:
    """Result of `pick_accommodation`: one chosen entry plus context."""

    accommodation: Accommodation
    score: float
    rationale: str
    cost_usd: float = 0.0
