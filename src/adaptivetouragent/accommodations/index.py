"""Accommodation catalogue loader + hard-constraint filter."""

import json
from importlib import resources
from pathlib import Path

from adaptivetouragent.accommodations.types import Accommodation, AccommodationRequest
from adaptivetouragent.itinerary.routing import haversine_km


def _from_dict(d: dict) -> Accommodation:
    return Accommodation(
        accommodation_id=str(d["accommodation_id"]),
        name=d["name"],
        lat=float(d["lat"]),
        lon=float(d["lon"]),
        price_per_night_usd=float(d["price_per_night_usd"]),
        rating=float(d["rating"]),
        kid_friendly=bool(d.get("kid_friendly", False)),
        near_mrt=bool(d.get("near_mrt", False)),
        description=d.get("description", ""),
        amenities=tuple(d.get("amenities", [])),
    )


def load_accommodations(city: str) -> list[Accommodation]:
    """Load the bundled accommodation catalogue for a city."""
    from adaptivetouragent.retrieval.poi_index import _city_slug

    slug = _city_slug(city)
    try:
        data_pkg = resources.files("adaptivetouragent.data.cities")
        path = data_pkg / f"{slug}_accommodations.json"
        if path.is_file():
            with path.open(encoding="utf-8") as f:
                payload = json.load(f)
            return [_from_dict(d) for d in payload["accommodations"]]
    except (FileNotFoundError, ModuleNotFoundError):
        pass

    src_path = Path(__file__).resolve().parent.parent / "data" / "cities" / f"{slug}_accommodations.json"
    if src_path.is_file():
        with src_path.open(encoding="utf-8") as f:
            payload = json.load(f)
        return [_from_dict(d) for d in payload["accommodations"]]

    raise FileNotFoundError(f"No accommodation catalogue for city '{city}'. Looked at {src_path}.")


def filter_by_hard_constraints(
    accommodations: list[Accommodation],
    request: AccommodationRequest,
) -> list[Accommodation]:
    """Apply hard filters before LLM scoring.

    Hard filters: price cap, min rating, kid-friendly flag, near-MRT flag,
    proximity to a target lat/lon, required amenities.
    """
    required_amenities = set(request.amenities)
    out: list[Accommodation] = []
    for a in accommodations:
        if request.max_price_per_night_usd is not None and a.price_per_night_usd > request.max_price_per_night_usd:
            continue
        if a.rating < request.min_rating:
            continue
        if request.require_kid_friendly and not a.kid_friendly:
            continue
        if request.require_near_mrt and not a.near_mrt:
            continue
        if request.near_lat is not None and request.near_lon is not None:
            distance = haversine_km(a.lat, a.lon, request.near_lat, request.near_lon)
            if distance > request.near_radius_km:
                continue
        if required_amenities and not required_amenities.issubset(set(a.amenities)):
            continue
        out.append(a)
    return out
