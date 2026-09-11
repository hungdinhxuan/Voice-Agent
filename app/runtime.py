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
from app.tools import LLMDelta, ToolSpec
from app.tts.base import TTSService
from app.tts.factory import create_tts_service


class ModelRuntime:
    """Owns the shared model adapters and their process-wide lifecycle."""

    def __init__(
        self,
        config: AppConfig,
        *,
        asr: ASRService | None = None,
        llm: LLMService | None = None,
        tts: TTSService | None = None,
        english_asr: ASRService | None = None,
        english_tts: TTSService | None = None,
    ) -> None:
        self.config = config
        self.default_language = config.web.default_language
        self._configs = {
            "vi": config.for_language("vi"),
            "en": config.for_language("en"),
        }
        self._asr = {
            "vi": asr or create_asr_service(self._configs["vi"].asr),
            "en": english_asr or create_asr_service(self._configs["en"].asr),
        }
        self._tts = {
            "vi": tts or create_tts_service(self._configs["vi"].tts),
            "en": english_tts or create_tts_service(self._configs["en"].tts),
        }
        self.asr = self._asr[self.default_language]
        self.llm = llm or create_llm_service(config.llm)
        self.tts = self._tts[self.default_language]
        self._lock = asyncio.Lock()
        self._llm_loaded = False
        self._loaded_languages: set[str] = set()
        self._language_loaded_at: dict[str, float] = {}
        self._loaded_at: float | None = None
        self._gates = {
            "asr": InferenceGate(config.runtime.max_concurrent_asr),
            "llm": InferenceGate(config.runtime.max_concurrent_llm),
            "tts": InferenceGate(config.runtime.max_concurrent_tts),
        }

    @property
    def loaded(self) -> bool:
        return self._llm_loaded and self.default_language in self._loaded_languages

    async def load(self) -> None:
        await self.load_language(self.default_language)

    async def load_language(self, language: str) -> None:
        code = self._normalize_language(language)
        async with self._lock:
            if code in self._loaded_languages and self._llm_loaded:
                return
            llm_loaded_here = False
            try:
                if not self._llm_loaded:
                    await self.llm.load()
                    self._llm_loaded = True
                    llm_loaded_here = True
                await self._asr[code].load()
                await self._tts[code].load()
            except BaseException:
                await asyncio.gather(
                    self._tts[code].close(),
                    self._asr[code].close(),
                    return_exceptions=True,
                )
                if llm_loaded_here:
                    await self.llm.close()
                    self._llm_loaded = False
                raise
            loaded_at = time.time()
            self._loaded_languages.add(code)
            self._language_loaded_at[code] = loaded_at
            if self._loaded_at is None:
                self._loaded_at = loaded_at

    async def close(self) -> None:
        async with self._lock:
            if not self._llm_loaded and not self._loaded_languages:
                return
            services = [
                service
                for code in self._loaded_languages
                for service in (self._tts[code], self._asr[code])
            ]
            if self._llm_loaded:
                services.append(self.llm)
            await asyncio.gather(*(service.close() for service in services))
            self._loaded_languages.clear()
            self._language_loaded_at.clear()
            self._llm_loaded = False
            self._loaded_at = None

    def is_language_loaded(self, language: str) -> bool:
        return self._normalize_language(language) in self._loaded_languages

    def asr_for(self, language: str) -> ASRService:
        return self._asr[self._normalize_language(language)]

    def tts_for(self, language: str) -> TTSService:
        return self._tts[self._normalize_language(language)]

    async def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int,
        cancellation: TurnCancellation | None = None,
        language: str | None = None,
    ) -> str:
        code = self._normalize_language(language or self.default_language)
        async with self._gates["asr"].slot():
            if cancellation:
                cancellation.raise_if_cancelled()
            return await self._asr[code].transcribe(audio, sample_rate, cancellation)

    @property
    def supports_tools(self) -> bool:
        return self.llm.supports_tools

    async def generate_stream(
        self,
        messages: list[dict[str, str]],
        cancellation: TurnCancellation,
    ) -> AsyncIterator[str]:
        async with self._gates["llm"].slot():
            cancellation.raise_if_cancelled()
            async for token in self.llm.generate_stream(messages, cancellation):
                yield token

    async def generate_turn(
        self,
        messages: list[dict],
        cancellation: TurnCancellation,
        tools: list[ToolSpec] | None = None,
    ) -> AsyncIterator[LLMDelta]:
        async with self._gates["llm"].slot():
            cancellation.raise_if_cancelled()
            async for delta in self.llm.generate_turn(messages, cancellation, tools):
                yield delta

    async def synthesize_stream(
        self,
        text: str,
        cancellation: TurnCancellation,
        language: str | None = None,
    ) -> AsyncIterator[np.ndarray]:
        code = self._normalize_language(language or self.default_language)
        async with self._gates["tts"].slot():
            cancellation.raise_if_cancelled()
            async for audio in self._tts[code].synthesize_stream(text, cancellation):
                yield audio

    def snapshot(self) -> dict[str, Any]:
        return {
            "loaded": self.loaded,
            "loaded_at": self._loaded_at,
            "default_language": self.default_language,
            "asr": self._configs[self.default_language].asr.model,
            "llm": self.config.llm.model,
            "tts": self._configs[self.default_language].tts.model,
            "languages": {
                code: {
                    "loaded": code in self._loaded_languages,
                    "loaded_at": self._language_loaded_at.get(code),
                    "asr": profile.asr.model,
                    "tts": profile.tts.model,
                }
                for code, profile in self._configs.items()
            },
            "inference": {name: gate.snapshot() for name, gate in self._gates.items()},
        }

    def _normalize_language(self, language: str) -> str:
        code = language.casefold()
        if code not in self._configs:
            raise ValueError(f"Ngôn ngữ không được hỗ trợ: {language}")
        return code


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
