"""Append-only JSONL event log.

One event per line so the log streams cleanly into the FastAPI SSE endpoint
and analysis scripts can consume it with `jq`.
"""

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


def _default(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, set):
        return sorted(obj)
    if isinstance(obj, tuple):
        return list(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


class EventLog:
    """Tiny JSONL writer with a deferred-open semantics."""

    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event_type: str, **payload: Any) -> None:
        record = {
            "ts": datetime.now().isoformat(),
            "event": event_type,
            **payload,
        }
        line = json.dumps(record, default=_default, ensure_ascii=False)
        if self.path is None:
            print(line)
            return
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
