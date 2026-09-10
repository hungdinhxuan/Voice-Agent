from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import numpy as np

from app.asr.base import ASRService
from app.asr.factory import create_asr_service
from app.config import AppConfig
from app.cancellation import TurnCancellation
from app.llm.base import LLMService
from app.llm.factory import create_llm_service
from app.tts.base import TTSService
from app.tts.vieneu import VieNeuTTSService


class ModelRuntime:
    """Owns the shared model adapters and their process-wide lifecycle."""

    def __init__(
        self,
        config: AppConfig,
        *,
        asr: ASRService | None = None,
        llm: LLMService | None = None,
        tts: TTSService | None = None,
    ) -> None:
        self.config = config
        self.asr = asr or create_asr_service(config.asr)
        self.llm = llm or create_llm_service(config.llm)
        self.tts = tts or VieNeuTTSService(config.tts)
        self._lock = asyncio.Lock()
        self._loaded = False
        self._loaded_at: float | None = None
        self._gates = {
            "asr": InferenceGate(config.runtime.max_concurrent_asr),
            "llm": InferenceGate(config.runtime.max_concurrent_llm),
            "tts": InferenceGate(config.runtime.max_concurrent_tts),
        }

    @property
    def loaded(self) -> bool:
        return self._loaded

    async def load(self) -> None:
        async with self._lock:
            if self._loaded:
                return
            try:
                await self.asr.load()
                await self.llm.load()
                await self.tts.load()
            except BaseException:
                await asyncio.gather(
                    self.tts.close(),
                    self.llm.close(),
                    self.asr.close(),
                    return_exceptions=True,
                )
                raise
            self._loaded = True
            self._loaded_at = time.time()

    async def close(self) -> None:
        async with self._lock:
            if not self._loaded:
                return
            await asyncio.gather(self.tts.close(), self.llm.close(), self.asr.close())
            self._loaded = False

    async def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int,
        cancellation: TurnCancellation | None = None,
    ) -> str:
        async with self._gates["asr"].slot():
            if cancellation:
                cancellation.raise_if_cancelled()
            return await self.asr.transcribe(audio, sample_rate, cancellation)

    async def generate_stream(
        self,
        messages: list[dict[str, str]],
        cancellation: TurnCancellation,
    ) -> AsyncIterator[str]:
        async with self._gates["llm"].slot():
            cancellation.raise_if_cancelled()
            async for token in self.llm.generate_stream(messages, cancellation):
                yield token

    async def synthesize_stream(
        self,
        text: str,
        cancellation: TurnCancellation,
    ) -> AsyncIterator[np.ndarray]:
        async with self._gates["tts"].slot():
            cancellation.raise_if_cancelled()
            async for audio in self.tts.synthesize_stream(text, cancellation):
                yield audio

    def snapshot(self) -> dict[str, Any]:
        return {
            "loaded": self._loaded,
            "loaded_at": self._loaded_at,
            "asr": self.config.asr.model,
            "llm": self.config.llm.model,
            "tts": "VieNeu-TTS",
            "inference": {name: gate.snapshot() for name, gate in self._gates.items()},
        }


class InferenceGate:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._semaphore = asyncio.Semaphore(limit)
        self.waiting = 0
        self.active = 0

    @asynccontextmanager
    async def slot(self):
        self.waiting += 1
        try:
            await self._semaphore.acquire()
        finally:
            self.waiting -= 1
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1
            self._semaphore.release()

    def snapshot(self) -> dict[str, int]:
        return {"limit": self.limit, "active": self.active, "waiting": self.waiting}
