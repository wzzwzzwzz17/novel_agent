from __future__ import annotations

from typing import AsyncIterator, Protocol


class LLMProvider(Protocol):
    name: str

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.85,
        top_p: float = 0.9,
        max_tokens: int = 1600,
    ) -> str: ...

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.85,
        top_p: float = 0.9,
        max_tokens: int = 1600,
    ) -> AsyncIterator[str]: ...
