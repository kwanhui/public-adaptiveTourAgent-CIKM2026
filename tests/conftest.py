"""Shared fixtures: a stub LLM provider that returns deterministic JSON scores."""

import contextlib
import json
import os
from typing import Any

import pytest

from adaptivetouragent.llm.provider import LLMResponse
from adaptivetouragent.retrieval.poi_index import load_city

# CI must never depend on OSRM's public demo. The geometry pipeline reads
# this flag and skips all network fetches; visits still get the 2-point
# straight-line fallback, which is what tests assert against.
os.environ.setdefault("ATAU_DISABLE_OSRM", "1")


class StubLLM:
    """Deterministic stand-in for OpenAIClient.

    Returns category-weighted scores based on the user prompt's POI list.
    Used everywhere except the live smoke tests.
    """

    model = "stub-model"
    PRICING = {"stub-model": (0.0, 0.0)}

    def __init__(self) -> None:
        self.total_usage: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        self.calls: list[list[dict[str, Any]]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> LLMResponse:
        self.calls.append(messages)
        # Pull POI ids out of the user prompt.
        text = messages[-1]["content"]
        scores: dict[str, float] = {}
        for line in text.splitlines():
            if not line.startswith("POI_"):
                continue
            head = line.split(":", 1)[0]
            pid = head.removeprefix("POI_").strip()
            popularity = 0.5
            if "popularity=" in line:
                with contextlib.suppress(ValueError):
                    popularity = float(line.split("popularity=", 1)[1].split(",")[0])
            # Hash position into a deterministic 0..1 jitter for variety.
            jitter = (sum(ord(c) for c in pid) % 11) / 100.0
            scores[f"POI_{pid}"] = round(min(1.0, max(0.0, popularity * 0.9 + jitter)), 3)

        payload = {"scores": scores}
        body = json.dumps(payload)
        usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        for k, v in usage.items():
            self.total_usage[k] += v
        return LLMResponse(content=body, usage=usage, model=self.model, finish_reason="stop")

    def get_usage_summary(self) -> dict[str, Any]:
        return {**self.total_usage, "model": self.model, "estimated_cost_usd": 0.0}

    def reset_usage(self) -> None:
        self.total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


@pytest.fixture
def stub_llm() -> StubLLM:
    return StubLLM()


@pytest.fixture
def singapore_index():
    return load_city("Singapore")
