from __future__ import annotations

import asyncio
import secrets
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from app.config import AppConfig
from app.runtime import ModelRuntime
from app.xiaozhi_adapter import logging as adapter_logging
from app.xiaozhi_adapter.logging import LOGGER
from app.xiaozhi_adapter.protocol import messages as protocol
from app.xiaozhi_adapter.protocol.messages import ClientHello, ProtocolError
from app.xiaozhi_adapter.session.device_session import DeviceSession


HELLO_TIMEOUT_SECONDS = 10.0

CLOSE_UNAUTHORIZED = 1008
CLOSE_OVERLOADED = 1013
CLOSE_PROTOCOL_ERROR = 1002


class DeviceSessionRegistry:
    """Live device sessions, keyed by session id.

    Sessions never share conversation state: each holds its own orchestrator and
    codecs, and only the model weights in `ModelRuntime` are shared.
    """

    def __init__(self, config: AppConfig, runtime: ModelRuntime) -> None:
        self.config = config
        self.runtime = runtime
        self._sessions: dict[str, DeviceSession] = {}

    @property
    def count(self) -> int:
        return len(self._sessions)

    def add(self, session: DeviceSession) -> None:
        self._sessions[session.session_id] = session

    async def remove(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is not None:
            await session.close()

    async def close_all(self) -> None:
        sessions = list(self._sessions.values())
        self._sessions.clear()
        await asyncio.gather(*(session.close() for session in sessions), return_exceptions=True)

    def snapshots(self) -> list[dict[str, Any]]:
        return [session.snapshot() for session in self._sessions.values()]


class WebSocketTransport:
    """`DeviceTransport` over a FastAPI WebSocket."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def send_json(self, message: dict[str, Any]) -> None:
        await self._websocket.send_json(message)

    async def send_bytes(self, payload: bytes) -> None:
        await self._websocket.send_bytes(payload)


def register_xiaozhi_endpoint(app: FastAPI, config: AppConfig) -> None:
    """Mount the endpoint stock firmware expects.

    `/xiaozhi/v1/` is the ecosystem convention handed to devices by the OTA
    response. The path is registered with and without the trailing slash because
    Starlette does not redirect WebSocket routes.
    """

    settings = config.xiaozhi
    if not settings.enabled:
        return
    adapter_logging.configure()

    async def endpoint(websocket: WebSocket) -> None:
        await _handle_device(websocket, app, config)

    paths = {settings.path, settings.path.rstrip("/")}
    for path in sorted(filter(None, paths)):
        app.add_api_websocket_route(path, endpoint, name=f"xiaozhi{path}")


async def _handle_device(websocket: WebSocket, app: FastAPI, config: AppConfig) -> None:
    settings = config.xiaozhi
    registry: DeviceSessionRegistry = app.state.xiaozhi_sessions
    headers = websocket.headers

    # An ESP32 never sends Origin; a browser always does. Anything that looks
    # like a page rather than a device has to be explicitly allowed.
    origin = headers.get("origin")
    if origin is not None and origin not in config.web.allowed_origins:
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return
    if not _authorized(
        settings.access_token,
        headers.get("authorization"),
        websocket.query_params.get("token"),
    ):
        LOGGER.warning("Từ chối thiết bị Xiaozhi: token không hợp lệ.")
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return
    device_id = headers.get("device-id") or websocket.query_params.get("device-id")
    if not device_id:
        LOGGER.warning("Từ chối thiết bị Xiaozhi: thiếu header Device-Id.")
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return
    if registry.count >= settings.max_sessions:
        LOGGER.warning("Từ chối thiết bị %s: đã đạt xiaozhi.max_sessions.", device_id)
        await websocket.close(code=CLOSE_OVERLOADED)
        return

    client_id = headers.get("client-id") or websocket.query_params.get("client-id") or ""
    await websocket.accept()

    session = DeviceSession(
        config,
        app.state.runtime,
        WebSocketTransport(websocket),
        device_id=device_id,
        client_id=client_id,
    )
    try:
        hello = await _await_hello(websocket)
        await session.open(hello)
    except (ProtocolError, TimeoutError) as exc:
        LOGGER.warning("Handshake Xiaozhi thất bại (%s): %s", device_id, exc)
        await session.close()
        await websocket.close(code=CLOSE_PROTOCOL_ERROR)
        return
    except WebSocketDisconnect:
        await session.close()
        return

    registry.add(session)
    try:
        await _receive_loop(websocket, session)
    finally:
        await registry.remove(session.session_id)


async def _await_hello(websocket: WebSocket) -> ClientHello:
    """Read frames until the client hello arrives.

    The device gives the server 10 seconds before it declares the handshake
    failed, so waiting longer than that is pointless.
    """

    async def read_hello() -> ClientHello:
        while True:
            packet = await websocket.receive()
            if packet["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(packet.get("code", 1000))
            raw = packet.get("text")
            if raw is None:
                continue
            message = protocol.parse_client_message(raw)
            if isinstance(message, ClientHello):
                return message
            raise ProtocolError("Frame đầu tiên phải là hello.")

    return await asyncio.wait_for(read_hello(), HELLO_TIMEOUT_SECONDS)


async def _receive_loop(websocket: WebSocket, session: DeviceSession) -> None:
    try:
        while True:
            packet = await websocket.receive()
            if packet["type"] == "websocket.disconnect":
                return
            payload = packet.get("bytes")
            if payload is not None:
                await session.handle_binary(payload)
                continue
            raw = packet.get("text")
            if raw is not None:
                await session.handle_text(raw)
    except WebSocketDisconnect:
        return


def _authorized(expected: str | None, authorization: str | None, query_token: str | None) -> bool:
    """Compare the device's bearer token against the configured one.

    The firmware prepends `Bearer ` only when the stored token has no space, so
    a token configured with the prefix already present arrives verbatim.
    """

    if expected is None:
        return True
    supplied = authorization or query_token or ""
    if supplied.startswith("Bearer "):
        supplied = supplied[7:]
    return secrets.compare_digest(supplied, expected)
