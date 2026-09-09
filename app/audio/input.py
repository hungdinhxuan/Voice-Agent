from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import numpy as np
import sounddevice as sd

from app.config import AudioConfig


def list_audio_devices() -> None:
    print(sd.query_devices())


class MicrophoneInput:
    def __init__(self, config: AudioConfig, queue_size: int = 64) -> None:
        self.config = config
        self._queue: asyncio.Queue[np.ndarray | BaseException] = asyncio.Queue(maxsize=queue_size)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stream: sd.InputStream | None = None

    async def __aenter__(self) -> "MicrophoneInput":
        self._loop = asyncio.get_running_loop()
        self._stream = sd.InputStream(
            samplerate=self.config.sample_rate,
            channels=self.config.channels,
            dtype="float32",
            blocksize=self.config.block_size,
            device=self.config.input_device,
            callback=self._callback,
        )
        self._stream.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._stream is not None:
            await asyncio.to_thread(self._stream.stop)
            self._stream.close()
            self._stream = None

    def _callback(self, indata: np.ndarray, frames: int, time_info: Any, status: sd.CallbackFlags) -> None:
        del frames, time_info
        if self._loop is None:
            return
        if status.input_overflow:
            self._loop.call_soon_threadsafe(self._offer, RuntimeError("Microphone buffer overflow"))
        mono = np.asarray(indata[:, 0], dtype=np.float32).copy()
        self._loop.call_soon_threadsafe(self._offer, mono)

    def _offer(self, item: np.ndarray | BaseException) -> None:
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(item)

    async def frames(self) -> AsyncIterator[np.ndarray]:
        while True:
            item = await self._queue.get()
            if isinstance(item, BaseException):
                raise item
            yield item

