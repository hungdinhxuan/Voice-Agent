from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from app.cancellation import TurnCancellation
from app.config import AppConfig, VADConfig
from app.runtime import ModelRuntime
from app.tools import LLMDelta, ToolCall, ToolSpec
from app.vad.silero import VADEvent, VADEventType
from app.xiaozhi_adapter.session.device_session import DeviceSession
from app.xiaozhi_adapter.testing import FakeXiaozhiDevice


class StubVAD:
    """Stands in for `SileroVADSegmenter` without loading the ONNX model.

    Emits speech start on the first frame and speech end every
    `utterance_frames` frames, so a test can drive a whole turn by feeding a
    known number of Opus packets.
    """

    utterance_frames = 8

    def __init__(self, config: VADConfig, sample_rate: int, frame_samples: int) -> None:
        del config
        self.sample_rate = sample_rate
        self.frame_samples = frame_samples
        self.frames_seen = 0
        self.resets = 0
        self._active = False
        self._since_start = 0

    @property
    def active(self) -> bool:
        return self._active

    def reset(self) -> None:
        self.resets += 1
        self._active = False
        self._since_start = 0

    def flush(self) -> VADEvent | None:
        if not self._active:
            return None
        self._active = False
        self._since_start = 0
        return VADEvent(VADEventType.SPEECH_END, self._utterance())

    def process(self, frame: np.ndarray) -> list[VADEvent]:
        if frame.size != self.frame_samples:
            raise ValueError(f"StubVAD cần {self.frame_samples} samples, nhận {frame.size}.")
        self.frames_seen += 1
        if not self._active:
            self._active = True
            self._since_start = 0
            return [VADEvent(VADEventType.SPEECH_START)]
        self._since_start += 1
        if self._since_start >= self.utterance_frames:
            self._active = False
            self._since_start = 0
            return [VADEvent(VADEventType.SPEECH_END, self._utterance())]
        return []

    def _utterance(self) -> np.ndarray:
        return np.zeros(self.sample_rate // 2, dtype=np.float32)


@dataclass
class StubBrain:
    """Scripted ASR/LLM/TTS so the adapter is tested, not the models."""

    transcript: str = "xin chào"
    reply: str = "Chào bạn."
    tool_calls: list[ToolCall] = field(default_factory=list)
    tts_chunks: int = 4
    tts_chunk_seconds: float = 0.12
    tts_chunk_delay: float = 0.0
    llm_delay: float = 0.0
    tool_rounds_seen: int = 0
    tools_offered: list[list[ToolSpec]] = field(default_factory=list)


@dataclass
class Harness:
    config: AppConfig
    runtime: ModelRuntime
    device: FakeXiaozhiDevice
    session: DeviceSession
    brain: StubBrain
    vad: StubVAD | None = None

    async def opus_packets(self, count: int) -> list[bytes]:
        """Real Opus packets at the device's uplink format (16 kHz, 60 ms)."""

        from app.xiaozhi_adapter.audio.codec import OpusEncoder

        encoder = OpusEncoder(16000, 60, 24000)
        samples = encoder.frame_samples
        time_axis = np.arange(count * samples, dtype=np.float32) / 16000
        tone = 0.2 * np.sin(2 * np.pi * 220 * time_axis).astype(np.float32)
        packets: list[bytes] = []
        for index in range(count):
            block = tone[index * samples : (index + 1) * samples]
            packets.extend(encoder.encode(block))
        encoder.close()
        return packets


def build_config(**overrides) -> AppConfig:
    config = AppConfig()
    config.xiaozhi.enabled = True
    # The device defers its own listen restart until playback drains, so the
    # server-side echo guard only makes tests slow.
    config.audio.echo_guard_ms = 0
    for name, value in overrides.items():
        setattr(config.xiaozhi, name, value)
    config.validate()
    return config


@pytest.fixture
def stub_brain() -> type[StubBrain]:
    """The scripted-brain class. A fixture because `tests` is not a package."""

    return StubBrain


@pytest.fixture
async def xiaozhi_harness() -> AsyncIterator[Callable[..., object]]:
    """Factory for a `DeviceSession` wired to a fake device and a scripted brain."""

    harnesses: list[Harness] = []
    patcher = patch("app.orchestrator.SileroVADSegmenter", StubVAD)
    patcher.start()

    async def build(
        *,
        device: FakeXiaozhiDevice | None = None,
        brain: StubBrain | None = None,
        open_session: bool = True,
        **config_overrides,
    ) -> Harness:
        config = build_config(**config_overrides)
        brain = brain or StubBrain()
        runtime = _scripted_runtime(config, brain)
        device = device or FakeXiaozhiDevice()
        session = DeviceSession(
            config,
            runtime,
            device.transport,
            device_id="aa:bb:cc:dd:ee:ff",
            client_id="client-uuid",
        )
        device.attach(session)
        harness = Harness(config, runtime, device, session, brain)
        harnesses.append(harness)
        if open_session:
            from app.xiaozhi_adapter.protocol.messages import parse_client_message

            import json as _json

            hello = parse_client_message(_json.dumps(device.hello_message()))
            await session.open(hello)
            harness.vad = await _wait_for_vad(session)
        return harness

    try:
        yield build
    finally:
        for harness in harnesses:
            await harness.device.detach()
            await harness.session.close()
            await harness.runtime.llm.close()
        patcher.stop()


async def _wait_for_vad(session: DeviceSession, timeout: float = 2.0) -> StubVAD:
    """The orchestrator builds its VAD on a worker thread inside `run()`."""

    orchestrator = session._agent._orchestrator
    deadline = asyncio.get_running_loop().time() + timeout
    while orchestrator.vad is None:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("Orchestrator chưa khởi tạo VAD.")
        await asyncio.sleep(0.01)
    return orchestrator.vad


def _scripted_runtime(config: AppConfig, brain: StubBrain) -> ModelRuntime:
    runtime = ModelRuntime(config)
    runtime.load_language = AsyncMock()
    runtime._loaded_languages.update({"vi", "en"})
    runtime._llm_loaded = True
    runtime.transcribe = AsyncMock(side_effect=lambda *_, **__: brain.transcript)

    async def generate_turn(
        messages: list[dict],
        cancellation: TurnCancellation,
        tools: list[ToolSpec] | None = None,
    ):
        brain.tools_offered.append(list(tools or []))
        if brain.llm_delay:
            await asyncio.sleep(brain.llm_delay)
        cancellation.raise_if_cancelled()
        pending = brain.tool_calls if brain.tool_rounds_seen == 0 else []
        if pending:
            brain.tool_rounds_seen += 1
            yield LLMDelta(tool_calls=tuple(pending))
            return
        for word in brain.reply.split():
            cancellation.raise_if_cancelled()
            yield LLMDelta(text=f"{word} ")

    async def synthesize_stream(
        text: str,
        cancellation: TurnCancellation,
        language: str | None = None,
    ):
        del text, language
        rate = config.for_language(config.xiaozhi.language).audio.output_sample_rate
        samples = int(rate * brain.tts_chunk_seconds)
        for _ in range(brain.tts_chunks):
            cancellation.raise_if_cancelled()
            if brain.tts_chunk_delay:
                await asyncio.sleep(brain.tts_chunk_delay)
            yield np.full(samples, 0.05, dtype=np.float32)

    runtime.generate_turn = generate_turn
    runtime.synthesize_stream = synthesize_stream
    return runtime
