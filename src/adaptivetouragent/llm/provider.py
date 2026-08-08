"""LLM provider Protocol: keeps OpenAI behind one seam for v2 swap."""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class LLMResponse:
    """Provider-agnostic response from a chat completion."""

    content: str | None
    usage: dict[str, int]
    model: str
    finish_reason: str = "stop"


class LLMProvider(Protocol):
    """Minimal async chat completion interface.

    Implementations must track cumulative usage and expose a cost summary so
    the demo can enforce per-session spend caps.
    """

    model: str
    total_usage: dict[str, int]

    async def complete(
        self,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> LLMResponse: ...

    def get_usage_summary(self) -> dict[str, Any]: ...

    def reset_usage(self) -> None: ...
