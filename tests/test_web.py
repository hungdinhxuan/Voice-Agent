import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.web.events import EventBroker
from app.web.server import (
    STATIC_DIR,
    RedactTokenFilter,
    _authorized,
    _origin_allowed,
    _receive_actions,
    create_web_app,
    decode_browser_audio,
    encode_browser_audio,
)


def test_event_broker_replays_recent_events() -> None:
    broker = EventBroker(history_size=2)
    broker.publish({"type": "state", "state": "IDLE"})
    broker.publish({"type": "transcript", "text": "một"})
    broker.publish({"type": "transcript", "text": "hai"})
    queue = broker.subscribe()
    assert queue.get_nowait()["text"] == "một"
    assert queue.get_nowait()["text"] == "hai"


def test_event_broker_does_not_replay_audio() -> None:
    broker = EventBroker()
    broker.publish({"type": "state", "state": "SPEAKING"})
    broker.publish({"type": "audio_chunk", "pcm": "stale"})

    queue = broker.subscribe()

    assert queue.get_nowait()["type"] == "state"
    assert queue.empty()


def test_event_broker_drops_audio_before_control_events() -> None:
    broker = EventBroker(history_size=0, queue_size=2)
    queue = broker.subscribe()
    broker.publish({"type": "state", "state": "SPEAKING"})
    broker.publish({"type": "audio_chunk", "pcm": b"old"})
    broker.publish({"type": "audio_end", "turn_id": 1})

    assert [queue.get_nowait()["type"], queue.get_nowait()["type"]] == [
        "state",
        "audio_end",
    ]
    assert broker.dropped == 1


def test_event_broker_drops_new_audio_if_queue_only_contains_control() -> None:
    broker = EventBroker(history_size=0, queue_size=1)
    queue = broker.subscribe()
    broker.publish({"type": "audio_end", "turn_id": 1})
    broker.publish({"type": "audio_chunk", "pcm": b"new"})

    assert queue.get_nowait()["type"] == "audio_end"
    assert broker.dropped == 1


def test_event_broker_never_drops_playback_control_events() -> None:
    broker = EventBroker(history_size=0, queue_size=2)
    queue = broker.subscribe()
    broker.publish({"type": "audio_end", "turn_id": 1})
    broker.publish({"type": "state", "state": "SPEAKING"})
    broker.publish({"type": "audio_clear", "turn_id": 1})

    assert [queue.get_nowait()["type"] for _ in range(2)] == ["audio_end", "audio_clear"]
    assert broker.dropped == 1


def test_web_assets_exist() -> None:
    for name in ("index.html", "app.js", "styles.css", "mic-processor.js"):
        assert (Path(STATIC_DIR) / name).is_file()


def test_models_api_is_exposed_with_complete_catalog() -> None:
    app = create_web_app(AppConfig.load("config.yaml"))
    response = TestClient(app).get("/api/models")

    assert response.status_code == 200
    assert set(response.json()["models"]) == {"asr", "llm", "tts", "vad"}
    assert set(response.json()["languages"]) == {"vi", "en"}
    assert "/api/models" in app.openapi()["paths"]
    assert "/api/sessions" in app.openapi()["paths"]
    assert "/api/runtime" in app.openapi()["paths"]


def test_runtime_and_sessions_apis_expose_snapshots() -> None:
    app = create_web_app(AppConfig.load("config.yaml"))
    app.state.runtime = Mock()
    app.state.runtime.snapshot.return_value = {"loaded": True}
    app.state.sessions = Mock()
    app.state.sessions.snapshots.return_value = [{"id": "session-one"}]
    client = TestClient(app)

    assert client.get("/api/runtime").json() == {"runtime": {"loaded": True}}
    assert client.get("/api/sessions").json() == {
        "sessions": [{"id": "session-one"}]
    }


def test_web_security_accepts_bearer_or_query_token() -> None:
    token = "0123456789abcdef"

    assert _authorized(token, f"Bearer {token}", None)
    assert _authorized(token, None, token)
    assert not _authorized(token, "Bearer wrong", None)
    assert _authorized(None, None, None)


def test_web_api_enforces_token_and_origin() -> None:
    config = AppConfig()
    config.web.access_token = "0123456789abcdef"
    config.web.allowed_origins = ["https://voice.local"]
    client = TestClient(create_web_app(config))

    assert client.get("/api/models").status_code == 401
    assert client.get(
        "/api/models?token=0123456789abcdef",
        headers={"Origin": "https://voice.local"},
    ).status_code == 200
    assert client.get(
        "/api/models?token=0123456789abcdef",
        headers={"Origin": "https://evil.example"},
    ).status_code == 403


def test_web_origin_allowlist() -> None:
    config = AppConfig()
    config.web.allowed_origins = ["https://voice.local"]

    assert _origin_allowed("https://voice.local", None, config)
    assert not _origin_allowed("https://evil.example", None, config)


def test_dashboard_renders_model_catalog() -> None:
    html = (Path(STATIC_DIR) / "index.html").read_text(encoding="utf-8")
    script = (Path(STATIC_DIR) / "app.js").read_text(encoding="utf-8")

    assert 'id="models"' in html
    assert 'href="/api/models"' in html
    assert "function renderModels(models)" in script
    assert 'id="language-switch"' in html
    assert "set_language" in script


def test_web_mode_uses_browser_microphone_and_speaker() -> None:
    script = (Path(STATIC_DIR) / "app.js").read_text(encoding="utf-8")

    assert "navigator.mediaDevices.getUserMedia" in script
    assert "new AudioWorkletNode" in script
    assert "audio_chunk" in script


def test_browser_audio_frame_decodes_float32_pcm() -> None:
    expected = np.linspace(-1, 1, 512, dtype="<f4")
    actual = decode_browser_audio(expected.tobytes(), frame_samples=512)

    np.testing.assert_array_equal(actual, expected)


def test_tts_audio_frame_uses_binary_versioned_header() -> None:
    samples = np.array([-1.0, 0.0, 1.0], dtype="<f4")
    packet = encode_browser_audio(
        {
            "turn_id": 9,
            "sample_rate": 48000,
            "sequence": 3,
            "time": 12.5,
            "pcm": samples.tobytes(),
        }
    )

    assert packet[:4] == b"VAO1"
    np.testing.assert_array_equal(np.frombuffer(packet[24:], dtype="<f4"), samples)


@pytest.mark.asyncio
async def test_websocket_routes_pcm_from_every_client() -> None:
    frame = np.linspace(-1, 1, 512, dtype="<f4")
    sessions = []

    for _ in range(2):
        packets = iter(
            [
                {"type": "websocket.receive", "bytes": frame.tobytes()},
                {
                    "type": "websocket.receive",
                    "text": json.dumps({"action": "audio_started", "turn_id": 4}),
                },
                {
                    "type": "websocket.receive",
                    "text": json.dumps({"action": "audio_drained", "turn_id": 4}),
                },
                {"type": "websocket.disconnect"},
            ]
        )
        websocket = Mock()
        websocket.receive = AsyncMock(side_effect=lambda: next(packets))
        session = Mock()
        session.handle_action = AsyncMock()
        sessions.append(session)

        await _receive_actions(
            websocket,
            session,
            frame_samples=512,
        )

    for session in sessions:
        np.testing.assert_array_equal(session.push_audio.call_args.args[0], frame)
        assert session.handle_action.call_count == 2


def test_page_served_through_a_tunnel_may_open_its_own_websocket() -> None:
    """Trang do chinh server nay phuc vu, nhin qua tunnel.

    Trinh duyet thay https://voiceagent.example con app thay ket noi http tren
    loopback, nen so ca origin la tu choi chinh trang cua minh - da tung lam
    dien thoai tai duoc trang nhung khong mo noi WebSocket.
    """

    config = AppConfig()
    assert _origin_allowed("https://voiceagent.example", "voiceagent.example", config)
    # Host khac thi van la site la, du co Host header.
    assert not _origin_allowed("https://evil.example", "voiceagent.example", config)
    # Khong co Host thi khong co gi de doi chieu.
    assert not _origin_allowed("https://voiceagent.example", None, config)


def test_loopback_origin_rejects_other_websites() -> None:
    config = AppConfig()

    assert _origin_allowed(None, None, config)
    assert _origin_allowed("http://127.0.0.1:8080", None, config)
    assert _origin_allowed("http://localhost:8080", None, config)
    assert not _origin_allowed("https://evil.example", None, config)
    assert (
        TestClient(create_web_app(config))
        .get("/api/models", headers={"Origin": "https://evil.example"})
        .status_code
        == 403
    )


def test_websocket_rejects_clients_beyond_the_session_cap() -> None:
    config = AppConfig()
    config.web.max_sessions = 1
    app = create_web_app(config)
    app.state.sessions = Mock()
    app.state.sessions.count = 1
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as rejection:
        with client.websocket_connect("/ws"):
            pass

    assert rejection.value.code == 1013
    app.state.sessions.open.assert_not_called()


def test_access_log_redacts_token_query() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1", "GET", "/api/models?token=supersecret&x=1", "1.1", 200),
        None,
    )

    assert RedactTokenFilter().filter(record)
    assert "supersecret" not in record.getMessage()
    assert "token=***" in record.getMessage()


@pytest.mark.asyncio
async def test_malformed_frames_keep_the_session_open() -> None:
    packets = iter(
        [
            {"type": "websocket.receive", "bytes": b"too short"},
            {"type": "websocket.receive", "text": "{not json"},
            {
                "type": "websocket.receive",
                "text": json.dumps({"action": "interrupt"}),
            },
            {"type": "websocket.disconnect"},
        ]
    )
    websocket = Mock()
    websocket.receive = AsyncMock(side_effect=lambda: next(packets))
    session = Mock()
    session.handle_action = AsyncMock()

    await _receive_actions(websocket, session, frame_samples=512)

    assert session.report_protocol_error.call_count == 2
    session.handle_action.assert_awaited_once_with({"action": "interrupt"})


def test_same_origin_check_can_be_disabled_for_development() -> None:
    config = AppConfig()
    config.web.require_same_origin = False

    assert _origin_allowed("https://evil.example", None, config)

    config.web.allowed_origins = ["https://voice.local"]
    assert not _origin_allowed("https://evil.example", None, config)
