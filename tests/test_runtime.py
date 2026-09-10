import asyncio
from collections.abc import AsyncIterator

import numpy as np
import pytest

from app.asr.base import ASRService
from app.cancellation import TurnCancellation
from app.config import AppConfig
from app.llm.base import LLMService
from app.runtime import ModelRuntime
from app.tts.base import TTSService


class FakeASR(ASRService):
    def __init__(self) -> None:
        self.loads = 0
        self.closes = 0

    async def load(self) -> None:
        self.loads += 1

    async def close(self) -> None:
        self.closes += 1

    async def transcribe(self, audio, sample_rate, cancellation=None) -> str:
        return "xin chào"


class FakeLLM(LLMService):
    def __init__(self) -> None:
        self.loads = 0
        self.closes = 0

    async def load(self) -> None:
        self.loads += 1

    async def close(self) -> None:
        self.closes += 1

    async def generate_stream(
        self,
        messages: list[dict[str, str]],
        cancellation: TurnCancellation,
    ) -> AsyncIterator[str]:
        if False:
            yield ""


class FakeTTS(TTSService):
    sample_rate = 48000

    def __init__(self) -> None:
        self.loads = 0
        self.closes = 0

    async def load(self) -> None:
        self.loads += 1

    async def close(self) -> None:
        self.closes += 1

    async def synthesize_stream(
        self,
        text: str,
        cancellation: TurnCancellation,
    ) -> AsyncIterator[np.ndarray]:
        if False:
            yield np.empty(0, dtype=np.float32)


@pytest.mark.asyncio
async def test_model_runtime_loads_shared_models_once() -> None:
    asr, llm, tts = FakeASR(), FakeLLM(), FakeTTS()
    runtime = ModelRuntime(AppConfig(), asr=asr, llm=llm, tts=tts)

    await runtime.load()
    await runtime.load()

    assert (asr.loads, llm.loads, tts.loads) == (1, 1, 1)
    await runtime.close()
    assert (asr.closes, llm.closes, tts.closes) == (1, 1, 1)


@pytest.mark.asyncio
async def test_model_runtime_limits_concurrent_asr() -> None:
    release = asyncio.Event()

    class BlockingASR(FakeASR):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.max_active = 0

        async def transcribe(self, audio, sample_rate, cancellation=None) -> str:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await release.wait()
            self.active -= 1
            return "xong"

    asr = BlockingASR()
    runtime = ModelRuntime(AppConfig(), asr=asr, llm=FakeLLM(), tts=FakeTTS())
    audio = np.zeros(512, dtype=np.float32)
    first = asyncio.create_task(runtime.transcribe(audio, 16000))
    second = asyncio.create_task(runtime.transcribe(audio, 16000))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert runtime.snapshot()["inference"]["asr"] == {"limit": 1, "active": 1, "waiting": 1}
    release.set()
    await asyncio.gather(first, second)
    assert asr.max_active == 1
