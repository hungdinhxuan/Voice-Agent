from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Iterator
from typing import Any

import numpy as np

from app.cancellation import TurnCancellation
from app.config import TTSConfig
from app.tts.base import TTSService


_END = object()


class KokoroTTSService(TTSService):
    def __init__(self, config: TTSConfig) -> None:
        self.config = config
        self.sample_rate = config.sample_rate
        self.pipeline = None
        self._inference_lock = threading.Lock()

    async def load(self) -> None:
        await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> None:
        from kokoro import KPipeline

        self.pipeline = KPipeline(
            lang_code=self.config.lang_code or "a",
            repo_id=self.config.model,
            device=self.config.device,
        )

    async def synthesize_stream(
        self,
        text: str,
        cancellation: TurnCancellation,
    ) -> AsyncIterator[np.ndarray]:
        if self.pipeline is None:
            raise RuntimeError("Kokoro chưa được load.")
        cancellation.raise_if_cancelled()
        stream = self.pipeline(text, voice=self.config.voice)
        while True:
            audio = await asyncio.to_thread(_next_audio_chunk, stream, self._inference_lock)
            cancellation.raise_if_cancelled()
            if audio is _END:
                return
            if hasattr(audio, "detach"):
                audio = audio.detach().cpu().numpy()
            samples = np.asarray(audio, dtype=np.float32).reshape(-1)
            if samples.size:
                yield samples

    async def close(self) -> None:
        self.pipeline = None


def _next_audio_chunk(stream: Iterator[Any], lock: threading.Lock) -> object:
    with lock:
        try:
            result = next(stream)
        except StopIteration:
            return _END
    return result[2]
