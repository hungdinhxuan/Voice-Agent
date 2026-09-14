from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


TRANSPORT = "websocket"
AUDIO_FORMAT = "opus"
# Official firmware only ever sends Opus. A DIY ESP32 that would otherwise need
# an Opus encoder on-device may declare raw little-endian 16-bit PCM instead and
# skip the codec entirely; it costs about ten times the uplink bandwidth, which
# is affordable on a LAN and not on a metered link.
PCM_FORMAT = "pcm"
SUPPORTED_UPLINK_FORMATS = (AUDIO_FORMAT, PCM_FORMAT)


class ProtocolError(ValueError):
    """A client message the adapter cannot honour."""


@dataclass(frozen=True, slots=True)
class AudioParams:
    format: str = AUDIO_FORMAT
    sample_rate: int = 16000
    channels: int = 1
    frame_duration_ms: int = 60

    def to_json(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "frame_duration": self.frame_duration_ms,
        }


@dataclass(frozen=True, slots=True)
class ClientHello:
    version: int
    transport: str
    audio: AudioParams
    features: dict[str, bool] = field(default_factory=dict)

    @property
    def supports_mcp(self) -> bool:
        return bool(self.features.get("mcp"))

    @property
    def server_side_aec(self) -> bool:
        return bool(self.features.get("aec"))


@dataclass(frozen=True, slots=True)
class ListenMessage:
    state: str
    mode: str | None = None
    text: str | None = None


@dataclass(frozen=True, slots=True)
class AbortMessage:
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class McpMessage:
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class UnknownMessage:
    type: str


ClientMessage = ClientHello | ListenMessage | AbortMessage | McpMessage | UnknownMessage

_LISTEN_STATES = frozenset({"start", "stop", "detect"})
_LISTEN_MODES = frozenset({"auto", "manual", "realtime"})


def parse_client_message(raw: str) -> ClientMessage:
    """Parse one text frame from the device.

    Raises `ProtocolError` only for input the adapter genuinely cannot act on.
    Unrecognised `type` values become `UnknownMessage` so that a firmware that
    grew a new message cannot take the session down.
    """

    try:
        message = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"Text frame không phải JSON: {exc}") from exc
    if not isinstance(message, dict):
        raise ProtocolError("Text frame phải là JSON object.")
    kind = message.get("type")
    if not isinstance(kind, str):
        raise ProtocolError("Thiếu trường type.")
    if kind == "hello":
        return _parse_hello(message)
    if kind == "listen":
        return _parse_listen(message)
    if kind == "abort":
        reason = message.get("reason")
        return AbortMessage(reason=reason if isinstance(reason, str) else None)
    if kind == "mcp":
        payload = message.get("payload")
        if not isinstance(payload, dict):
            raise ProtocolError("mcp.payload phải là JSON object.")
        return McpMessage(payload=payload)
    return UnknownMessage(type=kind)


def _parse_hello(message: dict[str, Any]) -> ClientHello:
    version = message.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ProtocolError("hello.version phải là số nguyên.")
    transport = message.get("transport", TRANSPORT)
    if transport != TRANSPORT:
        raise ProtocolError(f"hello.transport không được hỗ trợ: {transport}")
    raw_features = message.get("features")
    features: dict[str, bool] = {}
    if isinstance(raw_features, dict):
        features = {
            str(name): bool(value)
            for name, value in raw_features.items()
            if isinstance(value, bool)
        }
    raw_audio = message.get("audio_params")
    if not isinstance(raw_audio, dict):
        raise ProtocolError("hello.audio_params phải là JSON object.")
    audio_format = raw_audio.get("format")
    if audio_format not in SUPPORTED_UPLINK_FORMATS:
        raise ProtocolError(f"hello.audio_params.format không được hỗ trợ: {audio_format}")
    channels = raw_audio.get("channels", 1)
    if channels != 1:
        raise ProtocolError(f"hello.audio_params.channels phải là 1, nhận {channels}.")
    sample_rate = raw_audio.get("sample_rate", 16000)
    frame_duration = raw_audio.get("frame_duration", 60)
    if not isinstance(sample_rate, int) or sample_rate < 1:
        raise ProtocolError("hello.audio_params.sample_rate không hợp lệ.")
    if not isinstance(frame_duration, int) or frame_duration < 1:
        raise ProtocolError("hello.audio_params.frame_duration không hợp lệ.")
    return ClientHello(
        version=version,
        transport=transport,
        audio=AudioParams(
            format=audio_format,
            sample_rate=sample_rate,
            channels=channels,
            frame_duration_ms=frame_duration,
        ),
        features=features,
    )


def _parse_listen(message: dict[str, Any]) -> ListenMessage:
    state = message.get("state")
    if state not in _LISTEN_STATES:
        raise ProtocolError(f"listen.state không được hỗ trợ: {state}")
    mode = message.get("mode")
    if mode is not None and mode not in _LISTEN_MODES:
        raise ProtocolError(f"listen.mode không được hỗ trợ: {mode}")
    text = message.get("text")
    return ListenMessage(
        state=state,
        mode=mode,
        text=text if isinstance(text, str) else None,
    )


def build_server_hello(session_id: str, audio: AudioParams) -> dict[str, Any]:
    """The handshake reply.

    `transport` is mandatory: `ParseServerHello` dereferences it in its own
    error log, so a hello without it is a null dereference on the device.
    """

    return {
        "type": "hello",
        "transport": TRANSPORT,
        "session_id": session_id,
        "audio_params": audio.to_json(),
    }


def build_stt(session_id: str, text: str) -> dict[str, Any]:
    return {"type": "stt", "session_id": session_id, "text": text}


def build_tts(session_id: str, state: str, text: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"type": "tts", "session_id": session_id, "state": state}
    if text is not None:
        message["text"] = text
    return message


def build_mcp(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": "mcp", "session_id": session_id, "payload": payload}
