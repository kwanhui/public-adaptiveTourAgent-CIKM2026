"""In-memory POI index with structured filters.

A minimal city knowledge base. The seed catalogue ships under
`adaptivetouragent/data/cities/{city}.json` so the demo runs from a fresh
clone with no external data dependency.
"""

import json
from importlib import resources
from pathlib import Path

from adaptivetouragent.itinerary.routing import build_cost_matrix
from adaptivetouragent.itinerary.types import POI


class POIIndex:
    """Holds POIs for a city plus a precomputed travel-time matrix."""

    def __init__(self, city: str, pois: list[POI]):
        self.city = city
        self.pois: dict[str, POI] = {p.poi_id: p for p in pois}
        self.cost_matrix: dict[tuple[str, str], float] = build_cost_matrix(pois, city=city)
        self.max_popularity: float = max((p.popularity for p in pois), default=1.0)

    def __len__(self) -> int:
        return len(self.pois)

    def filter(
        self,
        *,
        kid_friendly: bool | None = None,
        indoor: bool | None = None,
        category: str | None = None,
        open_at_hour: int | None = None,
        require_wheelchair: bool | None = None,
        require_dietary: tuple[str, ...] = (),
        require_low_stimulation: bool | None = None,
    ) -> list[POI]:
        """Apply structured filters and return the matching POIs."""
        required_diet = set(require_dietary)
        result: list[POI] = []
        for p in self.pois.values():
            if kid_friendly is not None and p.kid_friendly != kid_friendly:
                continue
            if indoor is not None and p.indoor != indoor:
                continue
            if category is not None and p.category != category:
                continue
            if open_at_hour is not None and not (p.open_hours[0] <= open_at_hour < p.open_hours[1]):
                continue
            if require_wheelchair and not p.wheelchair_accessible:
                continue
            if required_diet and not required_diet.issubset(set(p.dietary_options)):
                continue
            if require_low_stimulation and not p.sensory_low_stimulation:
                continue
            result.append(p)
        return result


def _poi_from_dict(d: dict) -> POI:
    return POI(
        poi_id=str(d["poi_id"]),
        name=d["name"],
        category=d["category"],
        lat=float(d["lat"]),
        lon=float(d["lon"]),
        avg_duration_min=float(d["avg_duration_min"]),
        popularity=float(d["popularity"]),
        open_hours=tuple(d.get("open_hours", [9, 21])),  # type: ignore[arg-type]
        kid_friendly=bool(d.get("kid_friendly", True)),
        indoor=bool(d.get("indoor", False)),
        entry_fee_usd=float(d.get("entry_fee_usd", 0.0)),
        wheelchair_accessible=bool(d.get("wheelchair_accessible", True)),
        dietary_options=tuple(d.get("dietary_options", [])),
        sensory_low_stimulation=bool(d.get("sensory_low_stimulation", False)),
    )


def _city_slug(city: str) -> str:
    """Filesystem-safe key for a city name. 'New York' → 'new_york'."""
    return city.lower().strip().replace(" ", "_").replace("-", "_")


def load_city(city: str) -> POIIndex:
    """Load a city's POI catalogue from the bundled data directory."""
    slug = _city_slug(city)
    # Prefer package resource (works after pip install).
    try:
        data_pkg = resources.files("adaptivetouragent.data.cities")
        path = data_pkg / f"{slug}.json"
        if path.is_file():
            with path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            return POIIndex(city=payload["city"], pois=[_poi_from_dict(d) for d in payload["pois"]])
    except (FileNotFoundError, ModuleNotFoundError):
        pass

    # Editable-install fallback: read from the source tree.
    src_path = Path(__file__).resolve().parent.parent / "data" / "cities" / f"{slug}.json"
    if src_path.is_file():
        with src_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        return POIIndex(city=payload["city"], pois=[_poi_from_dict(d) for d in payload["pois"]])

    raise FileNotFoundError(f"No POI catalogue for city '{city}'. Looked at {src_path}.")
