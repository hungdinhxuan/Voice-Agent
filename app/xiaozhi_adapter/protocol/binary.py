from __future__ import annotations

from app.xiaozhi_adapter.protocol.messages import ProtocolError


SUPPORTED_VERSIONS = frozenset({1})

_VERSION_LAYOUTS = {
    2: "BinaryProtocol2 (16-byte big-endian header with timestamp)",
    3: "BinaryProtocol3 (4-byte header)",
}


def decode_uplink_audio(payload: bytes, version: int) -> bytes:
    """Extract the Opus packet from one binary frame sent by the device.

    Protocol version 1 puts a bare Opus packet in the WebSocket binary frame, so
    the frame boundary is the packet boundary and there is nothing to strip.
    Versions 2 and 3 prepend a header; they are documented in
    `docs/xiaozhi_protocol_research.md` but not implemented, and the handshake
    refuses them so this branch is only a guard.
    """

    if version not in SUPPORTED_VERSIONS:
        raise ProtocolError(_unsupported(version))
    return payload


def encode_downlink_audio(packet: bytes, version: int) -> bytes:
    """Wrap one Opus packet for the device's binary framing version."""

    if version not in SUPPORTED_VERSIONS:
        raise ProtocolError(_unsupported(version))
    return packet


def _unsupported(version: int) -> str:
    layout = _VERSION_LAYOUTS.get(version)
    detail = f" {layout}" if layout else ""
    return (
        f"Protocol-Version {version} chưa được hỗ trợ.{detail} "
        f"Hãy đặt websocket.version = 1 trên thiết bị."
    )
