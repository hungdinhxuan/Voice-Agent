from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

import numpy as np

from app.cancellation import TurnCancellation


class TTSService(ABC):
    sample_rate: int

    async def load(self) -> None:
        return None

    @abstractmethod
    def synthesize_stream(
        self,
        text: str,
        cancellation: TurnCancellation,
    ) -> AsyncIterator[np.ndarray]:
        raise NotImplementedError

    async def close(self) -> None:
        return None
