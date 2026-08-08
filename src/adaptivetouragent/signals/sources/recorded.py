"""Replay a JSONL signal trace at simulated wall-clock.

The trace is the source of truth for the demo runs and for tests. Each line
is one Reading with a `kind` tag; the source merges contiguous readings into
SignalBatches keyed by the `at` timestamp.

Trace line schema:
    {"at": "2026-05-02T10:00:00", "kind": "weather", "temp_c": 28.0,
     "precip_mm_per_h": 0.0, "condition": "clear", "source": "recorded"}
    {"at": "2026-05-02T10:30:00", "kind": "weather", "temp_c": 26.0,
     "precip_mm_per_h": 4.5, "condition": "rain", "source": "recorded"}
    {"at": "2026-05-02T10:00:00", "kind": "crowd", "poi_id": "sg05",
     "crowd_level": "high", "queue_minutes": 45.0, "source": "recorded"}
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from adaptivetouragent.fusion.snapshot import (
    CrowdReading,
    TransitReading,
    WeatherReading,
)
from adaptivetouragent.signals.sources.base import SignalBatch


def _parse_line(line: str) -> tuple[datetime, str, dict[str, Any]] | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    obj = json.loads(line)
    at = datetime.fromisoformat(obj["at"])
    kind = obj["kind"]
    return at, kind, obj


def _to_weather(obj: dict[str, Any]) -> WeatherReading:
    return WeatherReading(
        temp_c=float(obj["temp_c"]),
        precip_mm_per_h=float(obj["precip_mm_per_h"]),
        condition=obj["condition"],
        fetched_at=datetime.fromisoformat(obj["at"]),
        source=obj.get("source", "recorded"),
    )


def _to_crowd(obj: dict[str, Any]) -> CrowdReading:
    queue = obj.get("queue_minutes")
    return CrowdReading(
        poi_id=str(obj["poi_id"]),
        crowd_level=obj["crowd_level"],
        queue_minutes=float(queue) if queue is not None else None,
        fetched_at=datetime.fromisoformat(obj["at"]),
        source=obj.get("source", "recorded"),
    )


def _to_transit(obj: dict[str, Any]) -> TransitReading:
    return TransitReading(
        from_poi=str(obj["from_poi"]),
        to_poi=str(obj["to_poi"]),
        mode=obj.get("mode", "walk"),
        duration_min=float(obj["duration_min"]),
        disruption=bool(obj.get("disruption", False)),
        fetched_at=datetime.fromisoformat(obj["at"]),
        source=obj.get("source", "recorded"),
    )


class RecordedSource:
    """Replays a JSONL trace. Returns the latest reading at-or-before `at`."""

    name = "recorded"

    def __init__(self, trace_path: str | Path):
        self.trace_path = Path(trace_path)
        self._weather: list[tuple[datetime, WeatherReading]] = []
        self._crowd: dict[str, list[tuple[datetime, CrowdReading]]] = {}
        self._transit: dict[tuple[str, str], list[tuple[datetime, TransitReading]]] = {}
        self._load()

    def _load(self) -> None:
        if not self.trace_path.is_file():
            raise FileNotFoundError(f"Trace not found: {self.trace_path}")
        with self.trace_path.open(encoding="utf-8") as f:
            for raw in f:
                parsed = _parse_line(raw)
                if parsed is None:
                    continue
                at, kind, obj = parsed
                if kind == "weather":
                    self._weather.append((at, _to_weather(obj)))
                elif kind == "crowd":
                    cr = _to_crowd(obj)
                    self._crowd.setdefault(cr.poi_id, []).append((at, cr))
                elif kind == "transit":
                    tr = _to_transit(obj)
                    self._transit.setdefault((tr.from_poi, tr.to_poi), []).append((at, tr))

        self._weather.sort(key=lambda x: x[0])
        for crowd_lst in self._crowd.values():
            crowd_lst.sort(key=lambda x: x[0])
        for transit_lst in self._transit.values():
            transit_lst.sort(key=lambda x: x[0])

    @property
    def all_event_times(self) -> list[datetime]:
        """Every distinct timestamp in the trace, sorted. Used by the demo driver."""
        times: set[datetime] = set()
        for at, _ in self._weather:
            times.add(at)
        for crowd_lst in self._crowd.values():
            for at, _ in crowd_lst:
                times.add(at)
        for transit_lst in self._transit.values():
            for at, _ in transit_lst:
                times.add(at)
        return sorted(times)

    async def fetch(self, at: datetime) -> SignalBatch:
        weather = _latest_at_or_before(self._weather, at)
        crowd = {pid: r for pid, lst in self._crowd.items() if (r := _latest_at_or_before(lst, at))}
        transit = {key: r for key, lst in self._transit.items() if (r := _latest_at_or_before(lst, at))}
        return SignalBatch(at=at, weather=weather, crowd=crowd, transit=transit)

    async def close(self) -> None:
        return None


def _latest_at_or_before(items, at: datetime):
    """Linear scan, fine for the demo's small traces."""
    latest = None
    for ts, value in items:
        if ts <= at:
            latest = value
        else:
            break
    return latest
