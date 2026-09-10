from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from typing import Any

import numpy as np

from app.config import AudioConfig


EventHandler = Callable[[dict[str, Any]], None]
AudioSink = Callable[[np.ndarray], None]


class BrowserAudioInputRouter:
    """Selects one currently-speaking browser without mixing silent client streams."""

    def __init__(
        self,
        sink: AudioSink,
        *,
        activation_rms: float = 0.008,
        release_silence_frames: int = 48,
    ) -> None:
        self._sink = sink
        self._activation_rms = activation_rms
        self._release_silence_frames = release_silence_frames
        self._active_client: object | None = None
        self._silent_frames = 0

    def feed(self, client: object, frame: np.ndarray) -> bool:
        audio = np.asarray(frame, dtype=np.float32).reshape(-1)
        rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
        if self._active_client is None:
            if rms < self._activation_rms:
                return False
            self._active_client = client
        if client is not self._active_client:
            return False

        self._sink(audio)
        self._silent_frames = self._silent_frames + 1 if rms < self._activation_rms else 0
        if self._silent_frames >= self._release_silence_frames:
            self.release()
        return True

    def release(self) -> None:
        self._active_client = None
        self._silent_frames = 0

    def disconnect(self, client: object) -> None:
        if client is self._active_client:
            self.release()


class BrowserAudioOutput:
    """Streams raw TTS PCM to the browser and waits for playback acknowledgements."""

    def __init__(self, config: AudioConfig, event_handler: EventHandler) -> None:
        self.config = config
        self._emit = event_handler
        self._active_turn: int | None = None
        self._has_audio: dict[int, bool] = {}
        self._first_played: dict[int, asyncio.Event] = {}
        self._drained: dict[int, asyncio.Event] = {}

    async def start(self) -> None:
        return

    async def close(self) -> None:
        await self.clear()

    def begin_turn(self, turn_id: int) -> None:
        self._active_turn = turn_id
        self._has_audio[turn_id] = False
        self._first_played[turn_id] = asyncio.Event()
        self._drained[turn_id] = asyncio.Event()

    async def enqueue(self, turn_id: int, samples: np.ndarray) -> None:
        if turn_id != self._active_turn:
            return
        audio = np.asarray(samples, dtype="<f4").reshape(-1)
        if audio.size == 0:
            return
        self._has_audio[turn_id] = True
        self._emit(
            {
                "type": "audio_chunk",
                "turn_id": turn_id,
                "sample_rate": self.config.output_sample_rate,
                "pcm": base64.b64encode(audio.tobytes()).decode("ascii"),
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
