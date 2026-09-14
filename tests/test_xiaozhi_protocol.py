from __future__ import annotations

import json

import pytest

from app.xiaozhi_adapter.protocol import messages as protocol
from app.xiaozhi_adapter.protocol.binary import decode_uplink_audio, encode_downlink_audio
from app.xiaozhi_adapter.protocol.messages import (
    AbortMessage,
    AudioParams,
    ClientHello,
    ListenMessage,
    McpMessage,
    ProtocolError,
    UnknownMessage,
    parse_client_message,
)
from app.xiaozhi_adapter.protocol.state import DeviceState, DeviceStateTracker, ListenMode


FIRMWARE_HELLO = {
    "type": "hello",
    "version": 1,
    "features": {"mcp": True, "aec": True, "glyph_push": False},
    "text_font": {"bundle": "noto-v1", "charset": "common", "size": 20, "bpp": 4},
    "transport": "websocket",
    "audio_params": {
        "format": "opus",
        "sample_rate": 16000,
        "channels": 1,
        "frame_duration": 60,
    },
}


def parse(message: dict) -> object:
    return parse_client_message(json.dumps(message))


def test_hello_from_current_firmware_is_accepted() -> None:
    hello = parse(FIRMWARE_HELLO)

    assert isinstance(hello, ClientHello)
    assert hello.version == 1
    assert hello.supports_mcp
    assert hello.server_side_aec
    assert hello.audio == AudioParams("opus", 16000, 1, 60)


def test_hello_without_features_reports_no_mcp() -> None:
    message = {name: value for name, value in FIRMWARE_HELLO.items() if name != "features"}

    hello = parse(message)

    assert isinstance(hello, ClientHello)
    assert not hello.supports_mcp


@pytest.mark.parametrize(
    "mutation",
    [
        {"version": "1"},
        {"transport": "mqtt"},
        # "pcm" used to belong here and is now supported on purpose, for DIY
        # devices with no Opus encoder. See test_xiaozhi_audio.py.
        {"audio_params": {"format": "mp3", "sample_rate": 16000, "channels": 1}},
        {"audio_params": {"format": "opus", "channels": 2}},
        {"audio_params": "opus"},
    ],
)
def test_invalid_hello_is_rejected(mutation: dict) -> None:
    with pytest.raises(ProtocolError):
        parse({**FIRMWARE_HELLO, **mutation})


def test_non_json_and_typeless_frames_are_rejected() -> None:
    with pytest.raises(ProtocolError):
        parse_client_message("not json")
    with pytest.raises(ProtocolError):
        parse_client_message(json.dumps({"state": "start"}))
    with pytest.raises(ProtocolError):
        parse_client_message(json.dumps(["hello"]))


def test_listen_messages_match_the_firmware_shapes() -> None:
    start = parse({"session_id": "s", "type": "listen", "state": "start", "mode": "auto"})
    stop = parse({"session_id": "s", "type": "listen", "state": "stop"})
    detect = parse({"session_id": "s", "type": "listen", "state": "detect", "text": "Hi"})

    assert start == ListenMessage(state="start", mode="auto")
    assert stop == ListenMessage(state="stop")
    assert detect == ListenMessage(state="detect", text="Hi")


@pytest.mark.parametrize("state", ["pause", "", None])
def test_unknown_listen_state_is_rejected(state: object) -> None:
    with pytest.raises(ProtocolError):
        parse({"type": "listen", "state": state})


def test_abort_carries_the_optional_wake_word_reason() -> None:
    assert parse({"type": "abort"}) == AbortMessage()
    assert parse({"type": "abort", "reason": "wake_word_detected"}) == AbortMessage(
        reason="wake_word_detected"
    )


def test_mcp_requires_an_object_payload() -> None:
    assert parse({"type": "mcp", "payload": {"jsonrpc": "2.0"}}) == McpMessage(
        payload={"jsonrpc": "2.0"}
    )
    with pytest.raises(ProtocolError):
        parse({"type": "mcp", "payload": "[]"})


def test_unknown_message_types_do_not_raise() -> None:
    """A firmware that grows a message must not be able to drop the session."""

    assert parse({"type": "goodbye"}) == UnknownMessage(type="goodbye")


def test_server_hello_always_carries_transport_and_audio_params() -> None:
    hello = protocol.build_server_hello("abc", AudioParams(sample_rate=24000))

    # ParseServerHello dereferences `transport` even in its own error path.
    assert hello["transport"] == "websocket"
    assert hello["type"] == "hello"
    assert hello["session_id"] == "abc"
    assert hello["audio_params"] == {
        "format": "opus",
        "sample_rate": 24000,
        "channels": 1,
        "frame_duration": 60,
    }


def test_outbound_messages_carry_the_session_id() -> None:
    assert protocol.build_stt("s1", "xin chào") == {
        "type": "stt",
        "session_id": "s1",
        "text": "xin chào",
    }
    assert protocol.build_tts("s1", "start") == {
        "type": "tts",
        "session_id": "s1",
        "state": "start",
    }
    assert protocol.build_tts("s1", "sentence_start", "Chào bạn.")["text"] == "Chào bạn."
    assert protocol.build_mcp("s1", {"id": 1})["payload"] == {"id": 1}


def test_version_1_audio_frames_are_the_bare_opus_packet() -> None:
    packet = b"\x78\x9a\xbc"

    assert decode_uplink_audio(packet, 1) == packet
    assert encode_downlink_audio(packet, 1) == packet


@pytest.mark.parametrize("version", [2, 3])
def test_unsupported_binary_versions_explain_the_fix(version: int) -> None:
    with pytest.raises(ProtocolError, match="websocket.version = 1"):
        decode_uplink_audio(b"\x00", version)
    with pytest.raises(ProtocolError):
        encode_downlink_audio(b"\x00", version)


def test_state_tracker_follows_the_auto_mode_turn_cycle() -> None:
    tracker = DeviceStateTracker()
    assert tracker.state is DeviceState.CONNECTING
    assert not tracker.accepts_audio

    tracker.on_handshake_complete()
    assert tracker.state is DeviceState.LISTENING
    assert not tracker.accepts_audio

    tracker.on_listen("start", "auto")
    tracker.on_tts_start()
    assert tracker.accepts_audio

    tracker.on_tts_stop()
    assert tracker.state is DeviceState.LISTENING
    assert tracker.mode is ListenMode.AUTO


def test_state_tracker_returns_to_idle_after_manual_playback() -> None:
    tracker = DeviceStateTracker()
    tracker.on_handshake_complete()
    tracker.on_listen("start", "manual")
    tracker.on_tts_start()

    tracker.on_tts_stop()

    assert tracker.state is DeviceState.IDLE
    assert tracker.mode is ListenMode.MANUAL


def test_state_tracker_ignores_tts_stop_when_not_speaking() -> None:
    tracker = DeviceStateTracker()
    tracker.on_handshake_complete()

    tracker.on_tts_stop()

    assert tracker.state is DeviceState.LISTENING


def test_listen_stop_moves_the_device_to_idle() -> None:
    tracker = DeviceStateTracker()
    tracker.on_handshake_complete()
    tracker.on_listen("start", "manual")

    tracker.on_listen("stop", None)

    assert tracker.state is DeviceState.IDLE
    assert tracker.mode is ListenMode.MANUAL
