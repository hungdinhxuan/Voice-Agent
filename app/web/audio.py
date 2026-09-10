from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import numpy as np

from app.config import AudioConfig


EventHandler = Callable[[dict[str, Any]], None]

ACK_GRACE_SECONDS = 3.0


class BrowserAudioOutput:
    """Streams raw TTS PCM to the browser and waits for playback acknowledgements."""

    def __init__(
        self,
        config: AudioConfig,
        event_handler: EventHandler,
        *,
        ack_grace: float = ACK_GRACE_SECONDS,
    ) -> None:
        self.config = config
        self._emit = event_handler
        self._ack_grace = ack_grace
        self._active_turn: int | None = None
        self._has_audio: dict[int, bool] = {}
        self._first_played: dict[int, asyncio.Event] = {}
        self._drained: dict[int, asyncio.Event] = {}
        self._queued_seconds: dict[int, float] = {}
        self._sequence = 0

    async def start(self) -> None:
        return

    async def close(self) -> None:
        await self.clear()

    def begin_turn(self, turn_id: int) -> None:
        for bookkeeping in (
            self._has_audio,
            self._first_played,
            self._drained,
            self._queued_seconds,
        ):
            bookkeeping.clear()
        self._active_turn = turn_id
        self._has_audio[turn_id] = False
        self._first_played[turn_id] = asyncio.Event()
        self._drained[turn_id] = asyncio.Event()
        self._queued_seconds[turn_id] = 0.0
        self._sequence = 0

    async def enqueue(self, turn_id: int, samples: np.ndarray) -> None:
        if turn_id != self._active_turn:
            return
        audio = np.asarray(samples, dtype="<f4").reshape(-1)
        if audio.size == 0:
            return
        self._has_audio[turn_id] = True
        self._queued_seconds[turn_id] = (
            self._queued_seconds.get(turn_id, 0.0)
            + audio.size / self.config.output_sample_rate
        )
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
        if event is not None:
            await self._wait_for_ack(event, self._ack_grace, turn_id, "audio_started")

    async def wait_drained(self, turn_id: int) -> None:
        if not self._has_audio.get(turn_id):
            return
        self._emit({"type": "audio_end", "turn_id": turn_id})
        event = self._drained.get(turn_id)
        if event is not None:
            timeout = self._queued_seconds.get(turn_id, 0.0) + self._ack_grace
            await self._wait_for_ack(event, timeout, turn_id, "audio_drained")

    async def _wait_for_ack(
        self,
        event: asyncio.Event,
        timeout: float,
        turn_id: int,
        name: str,
    ) -> None:
        """A frozen or backgrounded tab must not keep the turn alive forever."""

        try:
            await asyncio.wait_for(event.wait(), timeout)
        except TimeoutError:
            self._emit(
                {
                    "type": "log",
                    "level": "error",
                    "message": (
                        f"[AUDIO] Không nhận được {name} của turn {turn_id} "
                        f"sau {timeout:.1f} s. Bỏ qua để tiếp tục hội thoại."
                    ),
                }
            )

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
