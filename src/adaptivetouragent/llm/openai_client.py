"""Async OpenAI client for adaptive tour planning.

Minimal async wrapper with usage and cost tracking.
The PRICING table is used for cost reporting; the API call itself is priced
server-side. Cost reporting matters for the demo's per-session spend cap.
"""

import asyncio
import os
from typing import Any

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None  # type: ignore[assignment, misc]

from adaptivetouragent.llm.provider import LLMResponse


class OpenAIClient:
    """Async OpenAI client.

    Usage:
        client = OpenAIClient()
        response = await client.complete([
            {"role": "system", "content": "You are a tour planner."},
            {"role": "user", "content": "Generate an itinerary..."},
        ])
    """

    PRICING: dict[str, tuple[float, float]] = {
        "gpt-4o-mini": (0.15, 0.60),
        "gpt-4o": (2.50, 10.00),
        "gpt-4.1": (2.00, 8.00),
        "gpt-4.1-mini": (0.40, 1.60),
        "gpt-4.1-nano": (0.10, 0.40),
        "o3-mini": (1.10, 4.40),
        "o4-mini": (1.10, 4.40),
    }
    DEFAULT_INPUT_COST_PER_MTOK = 0.15
    DEFAULT_OUTPUT_COST_PER_MTOK = 0.60

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        api_key: str | None = None,
    ):
        if AsyncOpenAI is None:
            raise ImportError("openai package required: pip install openai>=1.30.0")

        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ValueError("Set OPENAI_API_KEY or pass api_key parameter.")

        self._client = AsyncOpenAI(api_key=key)
        self.total_usage: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    async def complete(
        self,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> LLMResponse:
        temp = temperature if temperature is not None else self.temperature
        max_tok = max_tokens or self.max_tokens

        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                response = await self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=temp,
                    max_tokens=max_tok,
                )
                return self._parse_response(response)
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (2**attempt))

        raise last_error  # type: ignore[misc]

    def _parse_response(self, response: Any) -> LLMResponse:
        message = response.choices[0].message
        usage = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }
        self.total_usage["prompt_tokens"] += usage["prompt_tokens"]
        self.total_usage["completion_tokens"] += usage["completion_tokens"]
        self.total_usage["total_tokens"] += usage["total_tokens"]

        return LLMResponse(
            content=message.content,
            usage=usage,
            model=response.model,
            finish_reason=response.choices[0].finish_reason,
        )

    def get_usage_summary(self) -> dict[str, Any]:
        in_rate, out_rate = self.PRICING.get(
            self.model,
            (self.DEFAULT_INPUT_COST_PER_MTOK, self.DEFAULT_OUTPUT_COST_PER_MTOK),
        )
        input_cost = (self.total_usage["prompt_tokens"] / 1_000_000) * in_rate
        output_cost = (self.total_usage["completion_tokens"] / 1_000_000) * out_rate
        return {
            **self.total_usage,
            "model": self.model,
            "input_rate_per_mtok": in_rate,
            "output_rate_per_mtok": out_rate,
            "estimated_cost_usd": round(input_cost + output_cost, 4),
        }

    def reset_usage(self) -> None:
        self.total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
