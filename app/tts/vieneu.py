from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from typing import Any

import numpy as np

from app.cancellation import TurnCancellation
from app.config import TTSConfig
from app.tts.base import TTSService


_END = object()


class VieNeuTTSService(TTSService):
    def __init__(self, config: TTSConfig) -> None:
        self.config = config
        self.sample_rate = config.sample_rate
        self.engine = None
        self._inference_lock = threading.Lock()

    async def load(self) -> None:
        await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> None:
        from vieneu import Vieneu

        self.engine = Vieneu(
            backend=self.config.backend,
            device=self.config.device,
            precision=self.config.precision,
        )
        if self.config.voice is None:
            voices = self.engine.list_preset_voices()
            if not voices:
                raise RuntimeError("VieNeu không tìm thấy preset voice.")
            self.config.voice = voices[0][1]

    async def synthesize_stream(
        self,
        text: str,
        cancellation: TurnCancellation,
    ) -> AsyncIterator[np.ndarray]:
        if self.engine is None:
            raise RuntimeError("TTS chưa được load.")
        cancellation.raise_if_cancelled()
        stream = self.engine.infer_stream(text, voice=self.config.voice)
        while True:
            audio = await asyncio.to_thread(_next_audio_chunk, stream, self._inference_lock)
            cancellation.raise_if_cancelled()
            if audio is _END:
                return
            samples = np.asarray(audio, dtype=np.float32).reshape(-1)
            if samples.size:
                yield samples

    async def close(self) -> None:
        self.engine = None


def _next_audio_chunk(stream: Any, lock: threading.Lock) -> object:
    with lock:
        try:
            return next(stream)
        except StopIteration:
            return _END
