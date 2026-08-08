"""Multi-signal context fusion.

Takes the latest readings of each kind (from one or more SignalSources) plus
a UserState, and returns a single ContextSnapshot the agent reasons over.
This is the ablation surface: pass `disable=("crowd",)` to zero out crowd
readings for the paper's signal-isolation ablation.
"""

import uuid
from collections.abc import Iterable
from datetime import datetime

from adaptivetouragent.fusion.snapshot import ContextSnapshot, UserState
from adaptivetouragent.signals.sources.base import SignalBatch

AblationFlag = str  # "weather" | "crowd" | "transit"


def fuse(
    batches: Iterable[SignalBatch],
    *,
    user: UserState,
    city: str,
    at: datetime,
    disable: Iterable[AblationFlag] = (),
) -> ContextSnapshot:
    """Merge several SignalBatches into one ContextSnapshot.

    Later batches override earlier ones for overlapping keys (last-write-wins).
    """
    disabled = set(disable)
    weather = None
    crowd: dict = {}
    transit: dict = {}
    sources: list[str] = []

    for batch in batches:
        if batch.weather is not None and "weather" not in disabled:
            weather = batch.weather
            if weather.source not in sources:
                sources.append(weather.source)
        if batch.crowd and "crowd" not in disabled:
            crowd.update(batch.crowd)
            for cr in batch.crowd.values():
                if cr.source not in sources:
                    sources.append(cr.source)
        if batch.transit and "transit" not in disabled:
            transit.update(batch.transit)
            for tr in batch.transit.values():
                if tr.source not in sources:
                    sources.append(tr.source)

    return ContextSnapshot(
        snapshot_id=uuid.uuid4().hex[:12],
        at=at,
        city=city,
        weather=weather,
        crowd=crowd,
        transit=transit,
        user=user,
        sources_used=sources,
    )
