from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(slots=True)
class TurnTiming:
    speech_end: float = field(default_factory=time.perf_counter)
    asr_started: float | None = None
    asr_finished: float | None = None
    llm_started: float | None = None
    first_token: float | None = None
    llm_finished: float | None = None
    tts_started: float | None = None
    first_audio: float | None = None
    first_played: float | None = None

    @staticmethod
    def milliseconds(start: float | None, end: float | None) -> float | None:
        if start is None or end is None:
            return None
        return (end - start) * 1000

