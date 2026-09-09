from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from app.cancellation import TurnCancellation


class ASRService(ABC):
    @abstractmethod
    async def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int,
        cancellation: TurnCancellation | None = None,
    ) -> str:
        raise NotImplementedError

