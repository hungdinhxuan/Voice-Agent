from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.cancellation import TurnCancellation


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

    async def close(self) -> None:
        return None
