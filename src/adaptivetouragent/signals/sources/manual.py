"""User-injected signal source.

The UI's button row and chat box translate free-text notes into typed
signals (weather, transit) and a fatigue boost, and stash them here. The
next loop step fetches them, fuses them into the snapshot, and the trigger
registry + replanner behave as if the signal had come from a live feed.

Kept as its own source class so the demo paper can point at it: the
"user-initiated change" path reuses the same fusion pipeline as the
weather/crowd/transit sources, rather than living as a side channel.
"""

from collections import deque
from datetime import datetime

from adaptivetouragent.signals.sources.base import SignalBatch


class ManualSignalSource:
    """Per-session queue of user-injected readings.

    `inject` is called by the HTTP endpoints when the user clicks a trigger
    button or sends a chat message; `fetch` drains the queue on the next
    loop tick and returns a merged batch. The fatigue boost is consumed
    separately by the loop driver because fatigue lives on `UserState`,
    which is computed (not pushed) in the fusion step.
    """

    name = "manual"

    def __init__(self) -> None:
        self._pending: deque[SignalBatch] = deque()
        self._fatigue_boost: float = 0.0

    def inject(self, batch: SignalBatch) -> None:
        """Queue a batch to be returned on the next `fetch`."""
        self._pending.append(batch)

    def add_fatigue(self, amount: float) -> None:
        """Bump the pending fatigue offset, clamped to [0, 1]."""
        self._fatigue_boost = max(0.0, min(1.0, self._fatigue_boost + amount))

    def pop_fatigue_boost(self) -> float:
        """Read the pending fatigue offset and clear it."""
        val = self._fatigue_boost
        self._fatigue_boost = 0.0
        return val

    async def fetch(self, at: datetime) -> SignalBatch:
        if not self._pending:
            return SignalBatch(at=at)
        merged = SignalBatch(at=at)
        while self._pending:
            b = self._pending.popleft()
            if b.weather is not None:
                merged.weather = b.weather
            if b.crowd:
                merged.crowd.update(b.crowd)
            if b.transit:
                merged.transit.update(b.transit)
        return merged

    async def close(self) -> None:
        return None
