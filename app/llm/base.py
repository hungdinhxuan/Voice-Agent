from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.cancellation import TurnCancellation
from app.tools import LLMDelta, ToolSpec


class LLMService(ABC):
    async def load(self) -> None:
        return None

    @abstractmethod
    def generate_stream(
        self,
        messages: list[dict[str, str]],
        cancellation: TurnCancellation,
    ) -> AsyncIterator[str]:
        raise NotImplementedError

    @property
    def supports_tools(self) -> bool:
        return False

    async def generate_turn(
        self,
        messages: list[dict],
        cancellation: TurnCancellation,
        tools: list[ToolSpec] | None = None,
    ) -> AsyncIterator[LLMDelta]:
        """One model turn, as text deltas plus any tool calls it requested.

        The default wraps `generate_stream` and ignores `tools`, so a backend
        without tool support keeps working unchanged.
        """

        del tools
        async for token in self.generate_stream(messages, cancellation):
            yield LLMDelta(text=token)

    async def close(self) -> None:
        return None
