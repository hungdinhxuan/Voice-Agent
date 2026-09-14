from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.web.server import create_web_app
from app.xiaozhi_adapter import server as endpoint
from app.xiaozhi_adapter.protocol.messages import ProtocolError
from app.xiaozhi_adapter.server import (
    CLOSE_OVERLOADED,
    CLOSE_PROTOCOL_ERROR,
    CLOSE_UNAUTHORIZED,
    DeviceSessionRegistry,
    _authorized,
    _await_hello,
    _origin_allowed,
    _receive_loop,
)


TOKEN = "0123456789abcdef"

HELLO = {
    "type": "hello",
    "version": 1,
    "features": {"mcp": False},
    "transport": "websocket",
    "audio_params": {
        "format": "opus",
        "sample_rate": 16000,
        "channels": 1,
        "frame_duration": 60,
    },
}

DEVICE_HEADERS = {
    "Protocol-Version": "1",
    "Device-Id": "aa:bb:cc:dd:ee:ff",
    "Client-Id": "client-uuid",
}


class StubDeviceSession:
    """Stands in for `DeviceSession` so the endpoint can be tested alone.

    Echoes an ack for everything it is handed, which also gives the test a
    round trip to synchronise on instead of sleeping.
    """

    instances: list["StubDeviceSession"] = []

    def __init__(self, config, runtime, transport, *, device_id: str, client_id: str) -> None:
        del config, runtime
        self.transport = transport
        self.device_id = device_id
        self.client_id = client_id
        self.session_id = f"stub-{len(StubDeviceSession.instances)}"
        self.hello = None
        self.texts: list[str] = []
        self.binaries: list[bytes] = []
        self.closed = False
        StubDeviceSession.instances.append(self)

    async def open(self, hello) -> None:
        self.hello = hello
        await self.transport.send_json({"type": "hello", "transport": "websocket"})

    async def handle_text(self, raw: str) -> None:
        self.texts.append(raw)
        await self.transport.send_json({"type": "ack", "kind": "text"})

    async def handle_binary(self, payload: bytes) -> None:
        self.binaries.append(payload)
        await self.transport.send_json({"type": "ack", "kind": "binary"})

    async def close(self) -> None:
        self.closed = True

    def snapshot(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "device_id": self.device_id}


class FakeWebSocket:
    """Feeds a scripted packet sequence, then goes quiet."""

    def __init__(self, packets: list[dict[str, Any]]) -> None:
        self._packets = list(packets)
        self.silent_forever = False

    async def receive(self) -> dict[str, Any]:
        if not self._packets:
            self.silent_forever = True
            await asyncio.Event().wait()
        return self._packets.pop(0)


def build_app(**web_overrides) -> tuple[Any, DeviceSessionRegistry, AppConfig]:
    config = AppConfig()
    config.xiaozhi.enabled = True
    for name, value in web_overrides.items():
        setattr(config.web, name, value)
    config.validate()
    app = create_web_app(config)
    app.state.runtime = Mock()
    app.state.sessions = Mock()
    registry = DeviceSessionRegistry(config, app.state.runtime)
    app.state.xiaozhi_sessions = registry
    return app, registry, config


@pytest.fixture(autouse=True)
def stub_sessions(monkeypatch):
    StubDeviceSession.instances.clear()
    monkeypatch.setattr(endpoint, "DeviceSession", StubDeviceSession)
    yield StubDeviceSession.instances


# ------------------------------------------------------------------ routing


def test_endpoint_is_registered_with_and_without_a_trailing_slash() -> None:
    """Starlette does not redirect WebSocket routes, so both must exist."""

    app, _, _ = build_app()

    paths = {route.path for route in app.routes}
    assert "/xiaozhi/v1/" in paths
    assert "/xiaozhi/v1" in paths


def test_nothing_is_registered_while_the_adapter_is_disabled() -> None:
    app = create_web_app(AppConfig())

    assert not any(str(route.path).startswith("/xiaozhi") for route in app.routes)
    assert TestClient(app).get("/xiaozhi").status_code == 404


def test_the_device_console_and_its_assets_are_served() -> None:
    app, _, _ = build_app()
    client = TestClient(app)

    page = client.get("/xiaozhi")

    assert page.status_code == 200
    assert b"Device Console" in page.content
    for asset in ("xiaozhi.css", "xiaozhi.js", "mic-processor.js"):
        assert client.get(f"/static/{asset}").status_code == 200


def test_a_custom_path_is_honoured() -> None:
    config = AppConfig()
    config.xiaozhi.enabled = True
    config.xiaozhi.path = "/devices/esp32"
    config.validate()

    paths = {route.path for route in create_web_app(config).routes}

    assert "/devices/esp32" in paths


# --------------------------------------------------------------------- auth


def test_authorized_accepts_a_bearer_header_or_a_query_token() -> None:
    assert _authorized(TOKEN, f"Bearer {TOKEN}", None)
    assert _authorized(TOKEN, None, TOKEN)
    # The firmware only prepends "Bearer " when the stored token has no space,
    # so a bare token can arrive in the header too.
    assert _authorized(TOKEN, TOKEN, None)
    assert not _authorized(TOKEN, "Bearer wrong", None)
    assert not _authorized(TOKEN, None, None)
    assert _authorized(None, None, None)


def test_device_without_a_device_id_is_refused() -> None:
    app, registry, _ = build_app()
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as refusal:
        with client.websocket_connect("/xiaozhi/v1/", headers={"Protocol-Version": "1"}):
            pass

    assert refusal.value.code == CLOSE_UNAUTHORIZED
    assert registry.count == 0


def test_wrong_token_is_refused() -> None:
    app, registry, config = build_app()
    config.xiaozhi.access_token = TOKEN
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as refusal:
        with client.websocket_connect(
            "/xiaozhi/v1/",
            headers={**DEVICE_HEADERS, "Authorization": "Bearer nope"},
        ):
            pass

    assert refusal.value.code == CLOSE_UNAUTHORIZED
    assert registry.count == 0


def test_a_foreign_origin_is_refused() -> None:
    """WebSocket ignores CORS, so any site could otherwise open a session."""

    app, registry, _ = build_app()
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as refusal:
        with client.websocket_connect(
            "/xiaozhi/v1/",
            headers={**DEVICE_HEADERS, "Origin": "https://evil.example"},
        ):
            pass

    assert refusal.value.code == CLOSE_UNAUTHORIZED
    assert registry.count == 0


def test_the_device_console_can_connect_from_its_own_origin() -> None:
    """The console at /xiaozhi is served by this server and must reach this
    endpoint, and a browser always sends Origin."""

    app, registry, _ = build_app()
    client = TestClient(app)

    # TestClient always sends Host: testserver, so this is the same origin.
    with client.websocket_connect(
        "/xiaozhi/v1/",
        headers={**DEVICE_HEADERS, "Origin": "http://testserver"},
    ) as websocket:
        websocket.send_text(json.dumps(HELLO))
        assert websocket.receive_json()["type"] == "hello"
        assert registry.count == 1


def test_origin_is_matched_on_host_so_a_tls_terminating_proxy_still_works() -> None:
    """The browser sees https while the app sees plain http behind a tunnel."""

    config = AppConfig()
    config.web.allowed_origins = ["https://configured.example"]

    assert _origin_allowed(None, "anything", config)
    assert _origin_allowed("https://voiceagent.example", "voiceagent.example", config)
    assert _origin_allowed("http://127.0.0.1:8080", "127.0.0.1:8080", config)
    assert _origin_allowed("https://configured.example", "other.host", config)
    assert not _origin_allowed("https://evil.example", "voiceagent.example", config)
    assert not _origin_allowed("https://evil.example", None, config)
    # A port mismatch is a different origin.
    assert not _origin_allowed("http://127.0.0.1:9999", "127.0.0.1:8080", config)


def test_the_session_cap_refuses_extra_devices() -> None:
    app, registry, config = build_app()
    config.xiaozhi.max_sessions = 1
    registry.add(Mock(session_id="already-here"))
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as refusal:
        with client.websocket_connect("/xiaozhi/v1/", headers=DEVICE_HEADERS):
            pass

    assert refusal.value.code == CLOSE_OVERLOADED


# ------------------------------------------------------------------ handshake


async def test_await_hello_accepts_the_first_hello_and_skips_binary() -> None:
    websocket = FakeWebSocket([
        {"type": "websocket.receive", "bytes": b"\x01\x02"},
        {"type": "websocket.receive", "text": json.dumps(HELLO)},
    ])

    hello = await _await_hello(websocket)

    assert hello.version == 1
    assert not hello.supports_mcp


async def test_await_hello_rejects_a_non_hello_first_frame() -> None:
    websocket = FakeWebSocket([
        {"type": "websocket.receive", "text": json.dumps({"type": "listen", "state": "start"})},
    ])

    with pytest.raises(ProtocolError, match="hello"):
        await _await_hello(websocket)


async def test_await_hello_rejects_a_malformed_hello() -> None:
    websocket = FakeWebSocket([
        {"type": "websocket.receive", "text": json.dumps({**HELLO, "version": "1"})},
    ])

    with pytest.raises(ProtocolError):
        await _await_hello(websocket)


async def test_await_hello_gives_up_on_a_silent_device(monkeypatch) -> None:
    """The device declares the handshake failed after 10 s, so waiting longer
    than that only holds a socket the device has already abandoned."""

    monkeypatch.setattr(endpoint, "HELLO_TIMEOUT_SECONDS", 0.05)
    websocket = FakeWebSocket([])

    with pytest.raises(TimeoutError):
        await _await_hello(websocket)

    assert websocket.silent_forever


async def test_await_hello_propagates_an_early_disconnect() -> None:
    websocket = FakeWebSocket([{"type": "websocket.disconnect", "code": 1001}])

    with pytest.raises(WebSocketDisconnect):
        await _await_hello(websocket)


def test_a_handshake_failure_closes_with_a_protocol_error() -> None:
    app, registry, _ = build_app()
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as refusal:
        with client.websocket_connect("/xiaozhi/v1/", headers=DEVICE_HEADERS) as websocket:
            websocket.send_text(json.dumps({"type": "abort"}))
            websocket.receive_text()

    assert refusal.value.code == CLOSE_PROTOCOL_ERROR
    assert registry.count == 0
    assert StubDeviceSession.instances[0].closed


# ---------------------------------------------------------------- receive loop


async def test_receive_loop_dispatches_text_and_binary_then_stops() -> None:
    session = Mock()
    session.handle_text = AsyncMock()
    session.handle_binary = AsyncMock()
    websocket = FakeWebSocket([
        {"type": "websocket.receive", "bytes": b"opus"},
        {"type": "websocket.receive", "text": "{}"},
        {"type": "websocket.disconnect", "code": 1000},
    ])

    await _receive_loop(websocket, session)

    session.handle_binary.assert_awaited_once_with(b"opus")
    session.handle_text.assert_awaited_once_with("{}")


async def test_receive_loop_survives_a_frame_with_neither_payload() -> None:
    session = Mock()
    session.handle_text = AsyncMock()
    session.handle_binary = AsyncMock()
    websocket = FakeWebSocket([
        {"type": "websocket.receive"},
        {"type": "websocket.disconnect", "code": 1000},
    ])

    await _receive_loop(websocket, session)

    session.handle_text.assert_not_awaited()
    session.handle_binary.assert_not_awaited()


# -------------------------------------------------------------- happy path


def test_a_device_is_registered_and_cleaned_up_on_disconnect() -> None:
    app, registry, _ = build_app()
    client = TestClient(app)

    with client.websocket_connect("/xiaozhi/v1/", headers=DEVICE_HEADERS) as websocket:
        websocket.send_text(json.dumps(HELLO))
        assert websocket.receive_json()["type"] == "hello"
        assert registry.count == 1
        websocket.send_text(json.dumps({"type": "listen", "state": "start", "mode": "auto"}))
        assert websocket.receive_json() == {"type": "ack", "kind": "text"}
        websocket.send_bytes(b"opus-frame")
        assert websocket.receive_json() == {"type": "ack", "kind": "binary"}

    session = StubDeviceSession.instances[0]
    assert session.device_id == "aa:bb:cc:dd:ee:ff"
    assert session.client_id == "client-uuid"
    assert session.binaries == [b"opus-frame"]
    assert registry.count == 0
    assert session.closed


def test_a_bearer_token_and_an_allowed_origin_are_accepted() -> None:
    app, registry, config = build_app(allowed_origins=["https://voice.local"])
    config.xiaozhi.access_token = TOKEN
    client = TestClient(app)

    with client.websocket_connect(
        "/xiaozhi/v1/",
        headers={
            **DEVICE_HEADERS,
            "Authorization": f"Bearer {TOKEN}",
            "Origin": "https://voice.local",
        },
    ) as websocket:
        websocket.send_text(json.dumps(HELLO))
        assert websocket.receive_json()["type"] == "hello"
        assert registry.count == 1


def test_identity_falls_back_to_query_parameters() -> None:
    """The reference backend accepts these in the query string for test clients
    that cannot set headers; current firmware always uses headers."""

    app, _, config = build_app()
    config.xiaozhi.access_token = TOKEN
    client = TestClient(app)

    with client.websocket_connect(
        f"/xiaozhi/v1/?token={TOKEN}&device-id=11:22:33:44:55:66&client-id=q-uuid"
    ) as websocket:
        websocket.send_text(json.dumps(HELLO))
        websocket.receive_json()

    session = StubDeviceSession.instances[0]
    assert session.device_id == "11:22:33:44:55:66"
    assert session.client_id == "q-uuid"


def test_the_path_without_a_trailing_slash_works_too() -> None:
    app, registry, _ = build_app()
    client = TestClient(app)

    with client.websocket_connect("/xiaozhi/v1", headers=DEVICE_HEADERS) as websocket:
        websocket.send_text(json.dumps(HELLO))
        assert websocket.receive_json()["type"] == "hello"
        assert registry.count == 1


def test_two_devices_hold_independent_sessions() -> None:
    app, registry, config = build_app()
    config.xiaozhi.max_sessions = 2
    client = TestClient(app)

    with client.websocket_connect(
        "/xiaozhi/v1/", headers={**DEVICE_HEADERS, "Device-Id": "device-a"}
    ) as first:
        first.send_text(json.dumps(HELLO))
        first.receive_json()
        with client.websocket_connect(
            "/xiaozhi/v1/", headers={**DEVICE_HEADERS, "Device-Id": "device-b"}
        ) as second:
            second.send_text(json.dumps(HELLO))
            second.receive_json()

            assert registry.count == 2
            ids = {item["device_id"] for item in registry.snapshots()}
            assert ids == {"device-a", "device-b"}
            session_ids = {item["session_id"] for item in registry.snapshots()}
            assert len(session_ids) == 2


# ----------------------------------------------------------------- registry


async def test_registry_removes_and_closes_one_session() -> None:
    config = AppConfig()
    registry = DeviceSessionRegistry(config, Mock())
    session = StubDeviceSession(config, Mock(), Mock(), device_id="a", client_id="b")
    registry.add(session)

    await registry.remove(session.session_id)

    assert registry.count == 0
    assert session.closed
    # Removing an unknown session is a no-op, not an error.
    await registry.remove("missing")


async def test_registry_closes_every_session_on_shutdown() -> None:
    config = AppConfig()
    registry = DeviceSessionRegistry(config, Mock())
    sessions = [
        StubDeviceSession(config, Mock(), Mock(), device_id=f"d{index}", client_id="c")
        for index in range(3)
    ]
    for session in sessions:
        registry.add(session)

    await registry.close_all()

    assert registry.count == 0
    assert all(session.closed for session in sessions)


async def test_close_all_reports_nothing_when_a_session_fails_to_close() -> None:
    """One broken session must not block the shutdown of the others."""

    config = AppConfig()
    registry = DeviceSessionRegistry(config, Mock())
    broken = Mock(session_id="broken")
    broken.close = AsyncMock(side_effect=RuntimeError("socket already gone"))
    healthy = StubDeviceSession(config, Mock(), Mock(), device_id="d", client_id="c")
    registry.add(broken)
    registry.add(healthy)

    await registry.close_all()

    assert registry.count == 0
    assert healthy.closed


# ------------------------------------------------------------------- api view


def test_the_api_reports_connected_devices() -> None:
    app, registry, config = build_app()
    registry.add(StubDeviceSession(config, Mock(), Mock(), device_id="d1", client_id="c1"))

    body = TestClient(app).get("/api/xiaozhi").json()

    assert body["enabled"] is True
    assert body["path"] == "/xiaozhi/v1/"
    assert body["protocol_version"] == 1
    assert body["devices"] == [{"session_id": "stub-0", "device_id": "d1"}]
