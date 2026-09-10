from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, TypeVar, get_type_hints
from urllib.parse import urlparse

import yaml


class ConfigError(ValueError):
    pass


@dataclass(slots=True)
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    block_size: int = 512
    input_device: int | str | None = None
    output_device: int | str | None = None
    output_sample_rate: int = 48000
    allow_barge_in: bool = True
    interrupt_phrases: list[str] = field(
        default_factory=lambda: ["dừng", "dừng lại", "ngừng", "ngừng lại", "thôi"]
    )
    echo_guard_ms: int = 600


@dataclass(slots=True)
class VADConfig:
    threshold: float = 0.5
    min_speech_ms: int = 250
    min_silence_ms: int = 400
    speech_pad_ms: int = 150


@dataclass(slots=True)
class ASRConfig:
    backend: str = "parakeet"
    model: str = "nvidia/parakeet-ctc-0.6b-Vietnamese"
    checkpoint_file: str = "parakeet-ctc-0.6b-vi.nemo"
    device: str = "cuda"
    dtype: str = "bfloat16"
    language: str | None = "vi"
    max_new_tokens: int = 256


@dataclass(slots=True)
class LLMConfig:
    backend: str = "ollama"
    model: str = "qwen3.5:4b"
    host: str = "http://127.0.0.1:11434"
    keep_alive: str | int = -1
    context_length: int = 4096
    hf_model: str = "Qwen/Qwen3.5-4B"
    device: str = "cuda"
    dtype: str = "bfloat16"
    temperature: float = 0.7
    top_p: float = 0.8
    top_k: int = 20
    max_tokens: int = 256
    do_sample: bool = True
    enable_thinking: bool = False


@dataclass(slots=True)
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    access_token: str | None = None
    tls_certfile: str | None = None
    tls_keyfile: str | None = None
    allowed_origins: list[str] = field(default_factory=list)
    playback_prebuffer_ms: int = 120


@dataclass(slots=True)
class TTSConfig:
    backend: str = "onnx"
    device: str = "cpu"
    precision: str = "int8"
    voice: str | None = None
    sample_rate: int = 48000


@dataclass(slots=True)
class ChunkerConfig:
    min_chars: int = 15
    preferred_chars: int = 60
    max_chars: int = 120


@dataclass(slots=True)
class ConversationConfig:
    max_history_turns: int = 20
    system_prompt: str = "Bạn là trợ lý hội thoại tiếng Việt."


@dataclass(slots=True)
class RuntimeConfig:
    max_concurrent_asr: int = 1
    max_concurrent_llm: int = 1
    max_concurrent_tts: int = 1


@dataclass(slots=True)
class AppConfig:
    audio: AudioConfig = field(default_factory=AudioConfig)
    vad: VADConfig = field(default_factory=VADConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    web: WebConfig = field(default_factory=WebConfig)
    tts_chunker: ChunkerConfig = field(default_factory=ChunkerConfig)
    conversation: ConversationConfig = field(default_factory=ConversationConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        config_path = Path(path)
        if not config_path.is_file():
            raise ConfigError(f"Không tìm thấy cấu hình: {config_path}")
        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"YAML không hợp lệ: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError("Cấu hình gốc phải là mapping YAML.")
        config = _dataclass_from_dict(cls, raw, "config")
        config.validate()
        return config

    def validate(self) -> None:
        if self.audio.sample_rate != 16000:
            raise ConfigError("audio.sample_rate phải là 16000 cho Silero VAD và Qwen3-ASR.")
        if self.audio.channels != 1:
            raise ConfigError("audio.channels phải là 1.")
        if self.audio.block_size != 512:
            raise ConfigError("audio.block_size phải là 512 cho Silero VAD ở 16 kHz.")
        if self.audio.echo_guard_ms < 0:
            raise ConfigError("audio.echo_guard_ms không được âm.")
        if not self.audio.interrupt_phrases or any(
            not isinstance(phrase, str) or not phrase.strip()
            for phrase in self.audio.interrupt_phrases
        ):
            raise ConfigError("audio.interrupt_phrases phải chứa ít nhất một cụm từ hợp lệ.")
        if self.asr.backend not in {"parakeet", "qwen3"}:
            raise ConfigError("asr.backend chỉ hỗ trợ parakeet hoặc qwen3.")
        if self.asr.backend == "parakeet" and not self.asr.checkpoint_file:
            raise ConfigError("asr.checkpoint_file không được rỗng với Parakeet.")
        if not 0 < self.vad.threshold < 1:
            raise ConfigError("vad.threshold phải nằm giữa 0 và 1.")
        if min(self.vad.min_speech_ms, self.vad.min_silence_ms, self.vad.speech_pad_ms) < 0:
            raise ConfigError("Các mốc thời gian VAD không được âm.")
        c = self.tts_chunker
        if not 0 < c.min_chars <= c.preferred_chars <= c.max_chars:
            raise ConfigError("Cần min_chars <= preferred_chars <= max_chars và min_chars > 0.")
        if self.conversation.max_history_turns < 1:
            raise ConfigError("conversation.max_history_turns phải lớn hơn 0.")
        if min(
            self.runtime.max_concurrent_asr,
            self.runtime.max_concurrent_llm,
            self.runtime.max_concurrent_tts,
        ) < 1:
            raise ConfigError("Các giới hạn runtime concurrency phải lớn hơn 0.")
        if self.llm.backend not in {"ollama", "transformers"}:
            raise ConfigError("llm.backend chỉ hỗ trợ ollama hoặc transformers.")
        if self.llm.context_length < 512:
            raise ConfigError("llm.context_length phải từ 512 trở lên.")
        if self.llm.backend == "ollama" and urlparse(self.llm.host).hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ConfigError("llm.host phải trỏ đến Ollama trên máy cục bộ.")
        if not 1 <= self.web.port <= 65535:
            raise ConfigError("web.port phải nằm trong khoảng 1..65535.")
        is_loopback = self.web.host in {"127.0.0.1", "localhost", "::1"}
        tls_configured = bool(self.web.tls_certfile and self.web.tls_keyfile)
        if bool(self.web.tls_certfile) != bool(self.web.tls_keyfile):
            raise ConfigError("web.tls_certfile và web.tls_keyfile phải được cấu hình cùng nhau.")
        if not is_loopback:
            if not self.web.access_token or len(self.web.access_token) < 16:
                raise ConfigError("Web trên LAN cần web.access_token dài ít nhất 16 ký tự.")
            if not tls_configured:
                raise ConfigError("Web trên LAN cần TLS certificate và key.")
            if not self.web.allowed_origins:
                raise ConfigError("Web trên LAN cần web.allowed_origins.")
        if not 0 <= self.web.playback_prebuffer_ms <= 1000:
            raise ConfigError("web.playback_prebuffer_ms phải nằm trong 0..1000.")
        if self.tts.backend != "onnx" or self.tts.device != "cpu":
            raise ConfigError("Milestone 1 chỉ hỗ trợ VieNeu backend=onnx, device=cpu.")
        if self.audio.output_sample_rate != self.tts.sample_rate:
            raise ConfigError("audio.output_sample_rate phải bằng tts.sample_rate.")


T = TypeVar("T")


def _dataclass_from_dict(cls: type[T], data: dict[str, Any], path: str) -> T:
    hints = get_type_hints(cls)
    known = {item.name for item in fields(cls)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"Khóa cấu hình không xác định tại {path}: {', '.join(unknown)}")
    kwargs: dict[str, Any] = {}
    for item in fields(cls):
        if item.name not in data:
            continue
        value = data[item.name]
        target = hints[item.name]
        if isinstance(target, type) and is_dataclass(target):
            if not isinstance(value, dict):
                raise ConfigError(f"{path}.{item.name} phải là mapping YAML.")
            value = _dataclass_from_dict(target, value, f"{path}.{item.name}")
        kwargs[item.name] = value
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise ConfigError(f"Giá trị cấu hình không hợp lệ tại {path}: {exc}") from exc
