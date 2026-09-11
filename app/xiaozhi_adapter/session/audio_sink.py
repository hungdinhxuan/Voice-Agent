from __future__ import annotations

import asyncio
from collections.abc import Callable

import numpy as np

from app.xiaozhi_adapter.audio.pacing import PacedFrameSender
from app.xiaozhi_adapter.audio.pipeline import DownlinkPipeline


Logger = Callable[[str, str], None]

DRAIN_GRACE_SECONDS = 5.0


class XiaozhiAudioSink:
    """`AudioOutput` that turns agent TTS PCM into paced device Opus frames.

    Implements the same protocol as `SpeakerOutput` and `BrowserAudioOutput`, so
    the orchestrator is unchanged. Two differences from the browser sink:

    - There are no playback acknowledgements from an ESP32, so "first played"
      and "drained" are answered from the pacer's own schedule.
    - Frames are dropped when the device is not in its Speaking state, because
      `Application::OnIncomingAudio` would discard them anyway.

    Stale-turn suppression is the existing `turn_id` guard: anything arriving for
    a turn that is no longer active is dropped, which is what stops a late TTS
    chunk from a cancelled turn reaching the device.
    """

    def __init__(
        self,
        pipeline: DownlinkPipeline,
        pacer: PacedFrameSender,
        *,
        can_send: Callable[[], bool],
        log: Logger | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._pacer = pacer
        self._can_send = can_send
        self._log = log or (lambda message, level: None)
        self._active_turn: int | None = None
        self._has_audio = False
        self._first_sent = asyncio.Event()
        self.frames_dropped_not_speaking = 0

    async def start(self) -> None:
        self._pacer.start()

    async def close(self) -> None:
        await self.clear()
        await self._pacer.close()
        self._pipeline.close()

    def begin_turn(self, turn_id: int) -> None:
        self._active_turn = turn_id
        self._has_audio = False
        self._first_sent = asyncio.Event()
        self._pipeline.begin_turn()
        self._pacer.begin_turn()

    async def enqueue(self, turn_id: int, samples: np.ndarray) -> None:
        if turn_id != self._active_turn:
            return
        audio = np.asarray(samples, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            return
        packets = self._pipeline.push(audio)
        if not packets:
            return
        if not self._can_send():
            self.frames_dropped_not_speaking += len(packets)
            return
        self._has_audio = True
        for packet in packets:
            if turn_id != self._active_turn:
                return
            await self._pacer.submit(packet)
            self._first_sent.set()

    async def wait_first_played(self, turn_id: int) -> None:
        if turn_id != self._active_turn:
            return
        await self._first_sent.wait()

    async def wait_drained(self, turn_id: int) -> None:
        if turn_id != self._active_turn or not self._has_audio:
            return
        tail = self._pipeline.flush()
        if tail and self._can_send():
            for packet in tail:
                await self._pacer.submit(packet)
        queued_seconds = self._pacer.queued * self._pacer.frame_duration_ms / 1000
        timeout = queued_seconds + self._pacer.remaining_playback_seconds()
        if not await self._pacer.wait_drained(timeout + DRAIN_GRACE_SECONDS):
            self._log(
                f"[AUDIO] turn {turn_id} không xả hết sau {timeout:.1f}s, bỏ qua để tiếp tục.",
                "error",
            )

    async def clear(self) -> None:
        self._active_turn = None
        self._has_audio = False
        self._pacer.clear()
        self._first_sent.set()
