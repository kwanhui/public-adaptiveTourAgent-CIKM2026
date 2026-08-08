"""Map free-text notes to typed signals.

The UI's trigger buttons ("Rain", "Fatigue", "Delay") and chat box both
flow through one endpoint that calls this parser. Matching is keyword-based
and intentionally simple: the demo paper claims multi-signal fusion, not
free-text intent classification. The user's raw note also continues to
fire a `user_request` trigger and is fed verbatim into the LLM scoring
prompt, so anything this parser misses still drives a meaningful replan
through the LLM-scoring path.
"""

from datetime import datetime

from adaptivetouragent.fusion.snapshot import TransitReading, WeatherReading
from adaptivetouragent.signals.sources.base import SignalBatch

_RAIN_KEYWORDS = (
    "rain",
    "raining",
    "rainy",
    "pouring",
    "downpour",
    "wet weather",
    "drizzle",
    "shower",
    "showers",
)
_STORM_KEYWORDS = (
    "storm",
    "thunder",
    "lightning",
    "thunderstorm",
)
_TRANSIT_KEYWORDS = (
    "delay",
    "delayed",
    "disruption",
    "disrupted",
    "mrt delay",
    "subway delay",
    "metro delay",
    "tram delay",
    "tube delay",
    "métro delay",
    "metro is down",
    "train cancelled",
    "service suspended",
    "no service",
)
_FATIGUE_KEYWORDS = (
    "tired",
    "exhausted",
    "fatigue",
    "fatigued",
    "need a break",
    "need a rest",
    "kids are tired",
    "we're tired",
    "we are tired",
    "running out of energy",
    "feet hurt",
    "worn out",
    "nap",
)

# Intents that do not map to a typed signal but should still be reflected back
# to the user (the verbatim note drives the LLM rescore). Used by
# `interpret_note` so the UI can echo what it understood rather than staying
# silent on a request the keyword parser does not turn into a signal.
_HEAT_KEYWORDS = ("too hot", "so hot", "heat", "sweltering", "boiling", "sunburn", "shade")
_HUNGER_KEYWORDS = ("hungry", "dinner", "lunch", "breakfast", "eat", "food", "snack", "meal", "thirsty", "coffee")
_COST_KEYWORDS = ("cheaper", "too expensive", "save money", "spend less", "budget", "costly", "pricey")
_SKIP_KEYWORDS = ("skip", "drop", "remove", "no more", "don't want", "do not want", "instead of")

# Roughly how much fatigue a single "tired" injection adds to the user state.
# 0.35 lifts a fresh user from ~0 well clear of the 0.7 cutoff that biases the
# LLM context, and tips a partly-tired user (>=0.5) over the 0.85 threshold
# that fires the `fatigue_high` trigger on its own.
FATIGUE_BOOST_PER_INJECTION = 0.35


def _matches_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(k in text for k in keywords)


def interpret_note(note: str) -> str:
    """A short, human-readable reading of a free-text note for the UI to echo.

    The replan itself still feeds the verbatim note to the LLM scorer; this is
    only so the traveller sees what was understood instead of silence when the
    keyword parser does not turn a request into a typed signal.
    """
    lower = note.strip().lower()
    if not lower:
        return ""
    if _matches_any(lower, _STORM_KEYWORDS):
        return "treating this as a storm and favouring indoor stops"
    if _matches_any(lower, _RAIN_KEYWORDS):
        return "avoiding the rain and biasing toward indoor stops"
    if _matches_any(lower, _HEAT_KEYWORDS):
        return "beating the heat and favouring indoor or shaded stops"
    if _matches_any(lower, _TRANSIT_KEYWORDS):
        return "routing around the transit disruption"
    if _matches_any(lower, _FATIGUE_KEYWORDS):
        return "easing the pace and looking for a lighter next stop"
    if _matches_any(lower, _HUNGER_KEYWORDS):
        return "working a food stop into the plan nearby"
    if _matches_any(lower, _COST_KEYWORDS):
        return "trimming spend on the remaining stops"
    if _matches_any(lower, _SKIP_KEYWORDS):
        return "dropping what you flagged and re-routing the rest"
    return "re-scoring your remaining stops around your note"


def note_to_signals(
    *,
    note: str,
    at: datetime,
    upcoming_poi_ids: list[str],
) -> tuple[SignalBatch, float]:
    """Translate a free-text note into a SignalBatch + fatigue boost.

    The batch is meant to be injected into a per-session `ManualSignalSource`.
    The fatigue boost is consumed separately by the loop driver because
    fatigue lives on `UserState`, which is computed (not pushed) in fusion.

    `upcoming_poi_ids` is needed to anchor a transit-disruption reading on
    a real edge. When there are fewer than two upcoming POIs, the transit
    branch is skipped (no edge to disrupt).
    """
    lower = note.strip().lower()
    batch = SignalBatch(at=at)
    fatigue_boost = 0.0
    if not lower:
        return batch, fatigue_boost

    if _matches_any(lower, _STORM_KEYWORDS):
        batch.weather = WeatherReading(
            temp_c=24.0,
            precip_mm_per_h=12.0,
            condition="storm",
            fetched_at=at,
            source="manual",
        )
    elif _matches_any(lower, _RAIN_KEYWORDS):
        batch.weather = WeatherReading(
            temp_c=25.0,
            precip_mm_per_h=4.0,
            condition="rain",
            fetched_at=at,
            source="manual",
        )

    if _matches_any(lower, _TRANSIT_KEYWORDS) and len(upcoming_poi_ids) >= 2:
        a, b = upcoming_poi_ids[0], upcoming_poi_ids[1]
        batch.transit[(a, b)] = TransitReading(
            from_poi=a,
            to_poi=b,
            mode="transit",
            duration_min=45.0,
            disruption=True,
            fetched_at=at,
            source="manual",
        )

    if _matches_any(lower, _FATIGUE_KEYWORDS):
        fatigue_boost = FATIGUE_BOOST_PER_INJECTION

    return batch, fatigue_boost
