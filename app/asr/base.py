from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from app.cancellation import TurnCancellation


class ASRService(ABC):
    async def load(self) -> None:
        return None

    @abstractmethod
    async def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int,
        cancellation: TurnCancellation | None = None,
    ) -> str:
        raise NotImplementedError

    async def close(self) -> None:
        return None
