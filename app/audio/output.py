from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
import sounddevice as sd

from app.config import AudioConfig


@dataclass(slots=True)
class AudioPacket:
    turn_id: int
    samples: np.ndarray


class AudioOutput(Protocol):
    async def start(self) -> None: ...

    async def close(self) -> None: ...

    def begin_turn(self, turn_id: int) -> None: ...

    async def enqueue(self, turn_id: int, samples: np.ndarray) -> None: ...

    async def wait_first_played(self, turn_id: int) -> None: ...

    async def wait_drained(self, turn_id: int) -> None: ...

    async def clear(self) -> None: ...


class SpeakerOutput:
    """Single-owner playback queue with turn-aware cancellation."""

    def __init__(self, config: AudioConfig) -> None:
        self.config = config
        self._queue: asyncio.Queue[AudioPacket | None] = asyncio.Queue()
        self._stream: sd.OutputStream | None = None
        self._worker: asyncio.Task[None] | None = None
        self._active_turn: int | None = None
        self._pending: dict[int, int] = {}
        self._drained: dict[int, asyncio.Event] = {}
        self._first_played: dict[int, asyncio.Event] = {}

    async def start(self) -> None:
        self._stream = sd.OutputStream(
            samplerate=self.config.output_sample_rate,
            channels=1,
            dtype="float32",
            device=self.config.output_device,
        )
        self._stream.start()
        self._worker = asyncio.create_task(self._run(), name="speaker-worker")

    async def close(self) -> None:
        await self.clear()
        await self._queue.put(None)
        if self._worker is not None:
            await self._worker
        if self._stream is not None:
            await asyncio.to_thread(self._stream.stop)
            self._stream.close()
            self._stream = None

    def begin_turn(self, turn_id: int) -> None:
        self._active_turn = turn_id
        self._pending[turn_id] = 0
        self._drained[turn_id] = asyncio.Event()
        self._first_played[turn_id] = asyncio.Event()

    async def enqueue(self, turn_id: int, samples: np.ndarray) -> None:
        if turn_id != self._active_turn:
            return
        audio = np.asarray(samples, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            return
        self._pending[turn_id] += 1
        await self._queue.put(AudioPacket(turn_id, audio))

    async def wait_first_played(self, turn_id: int) -> None:
        event = self._first_played.get(turn_id)
        if event is not None:
            await event.wait()

    async def wait_drained(self, turn_id: int) -> None:
        if self._pending.get(turn_id, 0) == 0:
            return
        event = self._drained.get(turn_id)
        if event is not None:
            await event.wait()

    async def clear(self) -> None:
        active = self._active_turn
        self._active_turn = None
        while True:
            try:
                packet = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if packet is not None:
                self._queue.task_done()
        if self._stream is not None:
            await asyncio.to_thread(self._stream.abort)
        if active is not None:
            self._pending[active] = 0
            self._drained.setdefault(active, asyncio.Event()).set()

    async def _run(self) -> None:
        while True:
            packet = await self._queue.get()
            if packet is None:
                self._queue.task_done()
                return
            try:
                if packet.turn_id == self._active_turn and self._stream is not None:
                    if self._stream.stopped:
                        await asyncio.to_thread(self._stream.start)
                    first = self._first_played.get(packet.turn_id)
                    if first is not None and not first.is_set():
                        first.set()
                    await asyncio.to_thread(self._stream.write, packet.samples[:, None])
            except sd.PortAudioError as exc:
                print(f"[AUDIO] playback error: {exc}")
            finally:
                self._mark_done(packet.turn_id)
                self._queue.task_done()

    def _mark_done(self, turn_id: int) -> None:
        pending = max(0, self._pending.get(turn_id, 0) - 1)
        self._pending[turn_id] = pending
        if pending == 0:
            self._drained.setdefault(turn_id, asyncio.Event()).set()
