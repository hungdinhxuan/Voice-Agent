from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

from app.asr.qwen3_asr import Qwen3ASRService
from app.audio.input import MicrophoneInput
from app.audio.output import SpeakerOutput
from app.cancellation import TurnCancellation
from app.config import AppConfig
from app.llm.factory import create_llm_service
from app.tts.vieneu import VieNeuTTSService


async def test_microphone(config: AppConfig, seconds: float = 5.0) -> None:
    print(f"[MIC] Thu {seconds:.0f} giây. Hãy nói...")
    peak = 0.0
    frames = 0
    target = int(seconds * config.audio.sample_rate)
    async with MicrophoneInput(config.audio) as microphone:
        async for frame in microphone.frames():
            peak = max(peak, float(np.max(np.abs(frame))))
            frames += frame.size
            if frames >= target:
                break
    print(f"[MIC] OK, peak={peak:.3f}")


async def test_tts(config: AppConfig, text: str) -> None:
    service = VieNeuTTSService(config.tts)
    print("[TTS] Đang load VieNeu...")
    await service.load()
    speaker = SpeakerOutput(config.audio)
    await speaker.start()
    cancellation = TurnCancellation()
    speaker.begin_turn(1)
    try:
        async for audio in service.synthesize_stream(text, cancellation):
            await speaker.enqueue(1, audio)
        await speaker.wait_drained(1)
    finally:
        await speaker.close()
    print("[TTS] OK")


async def test_asr(config: AppConfig, path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    audio, sample_rate = await asyncio.to_thread(sf.read, path, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)
    if sample_rate != config.audio.sample_rate:
        audio = await asyncio.to_thread(soxr.resample, audio, sample_rate, config.audio.sample_rate)
    service = Qwen3ASRService(config.asr)
    print("[ASR] Đang load Qwen3-ASR...")
    await service.load()
    text = await service.transcribe(audio, config.audio.sample_rate)
    print(f"[ASR] {text}")


async def test_llm(config: AppConfig, prompt: str) -> None:
    service = create_llm_service(config.llm)
    print(f"[LLM] Đang load {config.llm.backend}/{config.llm.model}...")
    await service.load()
    cancellation = TurnCancellation()
    try:
        print("[LLM] ", end="", flush=True)
        async for token in service.generate_stream(
            [
                {"role": "system", "content": config.conversation.system_prompt},
                {"role": "user", "content": prompt},
            ],
            cancellation,
        ):
            print(token, end="", flush=True)
        print()
    finally:
        await service.close()
