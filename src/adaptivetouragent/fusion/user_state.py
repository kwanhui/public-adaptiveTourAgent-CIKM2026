"""Rule-based user-state model.

Fatigue accumulates linearly with elapsed time and per visited POI; resets
on breaks. The model is intentionally simple; the demo paper claims
multi-signal fusion, not learned fatigue prediction.
"""

from datetime import datetime

from adaptivetouragent.fusion.snapshot import UserState
from adaptivetouragent.itinerary.types import POIVisit

# Tunables (per-hour fatigue rate, per-visit fatigue jump).
FATIGUE_PER_HOUR = 0.06
FATIGUE_PER_VISIT = 0.04
FATIGUE_BREAK_DECAY = 0.15  # absolute reduction after a break


def estimate_user_state(
    *,
    start_time: datetime,
    now: datetime,
    executed_visits: list[POIVisit],
    family_size: int = 1,
    last_break_min_ago: float | None = None,
    pref_changes: list[str] | None = None,
    fatigue_offset: float = 0.0,
) -> UserState:
    """Build a UserState from elapsed time + executed visits.

    `fatigue_offset` is added on top of the time-and-visit model. The UI
    pushes this from the "I'm tired" trigger button (via the manual signal
    source) so a user can declare fatigue earlier than the model predicts.
    """
    elapsed_min = max(0.0, (now - start_time).total_seconds() / 60.0)
    fatigue = (elapsed_min / 60.0) * FATIGUE_PER_HOUR + len(executed_visits) * FATIGUE_PER_VISIT

    # Larger families fatigue faster (rough proxy for kids in tow).
    if family_size > 1:
        fatigue *= 1.0 + 0.05 * (family_size - 1)

    if last_break_min_ago is not None and last_break_min_ago < 30.0:
        fatigue = max(0.0, fatigue - FATIGUE_BREAK_DECAY)

    fatigue = max(0.0, min(1.0, fatigue + fatigue_offset))

    return UserState(
        fatigue_0_1=fatigue,
        elapsed_min=elapsed_min,
        pois_visited=len(executed_visits),
        last_break_min_ago=last_break_min_ago,
        explicit_pref_changes=pref_changes or [],
    )
