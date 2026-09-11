from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable


FrameSender = Callable[[bytes], Awaitable[None]]


class PacedFrameSender:
    """Feeds Opus frames to the device at the negotiated frame rate.

    Two upstream facts force this. The device decode queue holds only
    `1200 / frame_duration` packets and drops silently when full, and TTS
    generates far faster than realtime, so unpaced sending destroys audio.

    Scheduling is absolute: a monotonic turn start plus a running playback
    position, never `sleep(frame_duration)` per frame, so per-iteration
    scheduling error cannot accumulate.

    Overflow is handled by backpressure, not by dropping: `submit` awaits room
    in the bounded queue. Dropping would lose speech, and the producer is a
    per-turn task that is cancelled on barge-in anyway.
    """

    def __init__(
        self,
        frame_duration_ms: int,
        send: FrameSender,
        *,
        prebuffer_frames: int = 5,
        max_queued_frames: int = 100,
    ) -> None:
        if frame_duration_ms < 1:
            raise ValueError("frame_duration_ms phải lớn hơn 0.")
        self.frame_duration_ms = frame_duration_ms
        self._send = send
        self._prebuffer_frames = max(0, prebuffer_frames)
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=max_queued_frames)
        self._task: asyncio.Task[None] | None = None
        self._started_at: float | None = None
        self._position_ms = 0.0
        self._sent_in_turn = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self.frames_sent = 0

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="xiaozhi-tts-pacing")

    async def close(self) -> None:
        self.clear()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def begin_turn(self) -> None:
        self._started_at = None
        self._position_ms = 0.0
        self._sent_in_turn = 0

    async def submit(self, frame: bytes) -> None:
        self._idle.clear()
        await self._queue.put(frame)

    def clear(self) -> None:
        while not self._queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
        self._started_at = None
        self._position_ms = 0.0
        self._sent_in_turn = 0
        self._idle.set()

    @property
    def queued(self) -> int:
        return self._queue.qsize()

    def remaining_playback_seconds(self) -> float:
        """How long the frames already handed to the device still have to play."""

        if self._started_at is None:
            return 0.0
        return max(0.0, self._started_at + self._position_ms / 1000 - time.monotonic())

    async def wait_drained(self, timeout: float | None = None) -> bool:
        """Wait until every queued frame is sent and has finished playing."""

        try:
            await asyncio.wait_for(self._idle.wait(), timeout)
        except TimeoutError:
            return False
        await asyncio.sleep(self.remaining_playback_seconds())
        return True

    async def _run(self) -> None:
        while True:
            frame = await self._queue.get()
            await self._pace()
            await self._send(frame)
            self.frames_sent += 1
            self._sent_in_turn += 1
            self._position_ms += self.frame_duration_ms
            if self._queue.empty():
                self._idle.set()

    async def _pace(self) -> None:
        if self._started_at is None:
            self._started_at = time.monotonic()
        if self._sent_in_turn < self._prebuffer_frames:
            return
        while True:
            delay = self._started_at + self._position_ms / 1000 - time.monotonic()
            if delay <= 0:
                return
            await asyncio.sleep(delay)
