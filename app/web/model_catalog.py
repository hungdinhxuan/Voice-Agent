from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from app.config import AppConfig


def build_model_catalog(config: AppConfig) -> dict[str, dict[str, Any]]:
    asr_runtime = "NVIDIA NeMo" if config.asr.backend == "parakeet" else "Transformers"
    asr_package = "nemo-toolkit" if config.asr.backend == "parakeet" else "transformers"
    asr: dict[str, Any] = {
        "role": "Nhận dạng giọng nói",
        "model": config.asr.model,
        "backend": config.asr.backend,
        "runtime": asr_runtime,
        "runtime_version": _package_version(asr_package),
        "device": config.asr.device,
        "language": config.asr.language or "auto",
        "audio_input": f"{config.audio.sample_rate} Hz mono",
    }
    if config.asr.backend == "parakeet":
        asr["checkpoint"] = config.asr.checkpoint_file
    else:
        asr["dtype"] = config.asr.dtype
        asr["max_new_tokens"] = config.asr.max_new_tokens

    llm: dict[str, Any] = {
        "role": "Mô hình ngôn ngữ",
        "model": config.llm.model,
        "backend": config.llm.backend,
        "device": "GPU qua Ollama" if config.llm.backend == "ollama" else config.llm.device,
        "api_base": config.llm.host if config.llm.backend == "ollama" else None,
        "context_length": config.llm.context_length,
        "max_tokens": config.llm.max_tokens,
        "temperature": config.llm.temperature,
        "top_p": config.llm.top_p,
        "top_k": config.llm.top_k,
        "thinking": config.llm.enable_thinking,
        "keep_alive": config.llm.keep_alive,
    }
    if config.llm.backend == "ollama":
        llm["runtime"] = "Ollama local API"
        llm["client"] = f"httpx {_package_version('httpx')}"
    else:
        llm["runtime"] = "Transformers"
        llm["runtime_version"] = _package_version("transformers")

    tts = {
        "role": "Tổng hợp giọng nói",
        "model": "VieNeu-TTS v3 Turbo",
        "backend": config.tts.backend,
        "runtime": "vieneu",
        "runtime_version": _package_version("vieneu"),
        "device": config.tts.device,
        "precision": config.tts.precision,
        "voice": config.tts.voice or "preset mặc định",
        "audio_output": f"{config.tts.sample_rate} Hz mono",
    }

    vad = {
        "role": "Phát hiện giọng nói",
        "model": "Silero VAD",
        "backend": "ONNX Runtime",
        "runtime_version": _package_version("silero-vad"),
        "device": "cpu",
        "threshold": config.vad.threshold,
        "min_speech_ms": config.vad.min_speech_ms,
        "min_silence_ms": config.vad.min_silence_ms,
        "speech_pad_ms": config.vad.speech_pad_ms,
    }
    return {"asr": asr, "llm": llm, "tts": tts, "vad": vad}


def _package_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "không xác định"
