from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import numpy as np

from app.config import AudioConfig


EventHandler = Callable[[dict[str, Any]], None]


class BrowserAudioOutput:
    """Streams raw TTS PCM to the browser and waits for playback acknowledgements."""

    def __init__(self, config: AudioConfig, event_handler: EventHandler) -> None:
        self.config = config
        self._emit = event_handler
        self._active_turn: int | None = None
        self._has_audio: dict[int, bool] = {}
        self._first_played: dict[int, asyncio.Event] = {}
        self._drained: dict[int, asyncio.Event] = {}
        self._sequence = 0

    async def start(self) -> None:
        return

    async def close(self) -> None:
        await self.clear()

    def begin_turn(self, turn_id: int) -> None:
        self._active_turn = turn_id
        self._has_audio[turn_id] = False
        self._first_played[turn_id] = asyncio.Event()
        self._drained[turn_id] = asyncio.Event()
        self._sequence = 0

    async def enqueue(self, turn_id: int, samples: np.ndarray) -> None:
        if turn_id != self._active_turn:
            return
        audio = np.asarray(samples, dtype="<f4").reshape(-1)
        if audio.size == 0:
            return
        self._has_audio[turn_id] = True
        sequence = self._sequence
        self._sequence += 1
        self._emit(
            {
                "type": "audio_chunk",
                "turn_id": turn_id,
                "sample_rate": self.config.output_sample_rate,
                "sequence": sequence,
                "pcm": audio.tobytes(),
            }
        )

    async def wait_first_played(self, turn_id: int) -> None:
        event = self._first_played.get(turn_id)
        if event is not None and self._has_audio.get(turn_id):
            await event.wait()

    async def wait_drained(self, turn_id: int) -> None:
        if not self._has_audio.get(turn_id):
            return
        self._emit({"type": "audio_end", "turn_id": turn_id})
        event = self._drained.get(turn_id)
        if event is not None:
            await event.wait()

    async def clear(self) -> None:
        active = self._active_turn
        self._active_turn = None
        if active is None:
            return
        self._emit({"type": "audio_clear", "turn_id": active})
        self._drained.setdefault(active, asyncio.Event()).set()

    def mark_started(self, turn_id: int) -> None:
        if turn_id == self._active_turn:
            self._first_played.setdefault(turn_id, asyncio.Event()).set()

    def mark_drained(self, turn_id: int) -> None:
        if turn_id == self._active_turn:
            self._drained.setdefault(turn_id, asyncio.Event()).set()
