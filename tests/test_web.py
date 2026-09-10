import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.web.events import EventBroker
from app.web.server import STATIC_DIR, _receive_actions, create_web_app, decode_browser_audio


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


def test_web_assets_exist() -> None:
    for name in ("index.html", "app.js", "styles.css", "mic-processor.js"):
        assert (Path(STATIC_DIR) / name).is_file()


def test_models_api_is_exposed_with_complete_catalog() -> None:
    app = create_web_app(AppConfig.load("config.yaml"))
    response = TestClient(app).get("/api/models")

    assert response.status_code == 200
    assert set(response.json()["models"]) == {"asr", "llm", "tts", "vad"}
    assert "/api/models" in app.openapi()["paths"]


def test_dashboard_renders_model_catalog() -> None:
    html = (Path(STATIC_DIR) / "index.html").read_text(encoding="utf-8")
    script = (Path(STATIC_DIR) / "app.js").read_text(encoding="utf-8")

    assert 'id="models"' in html
    assert 'href="/api/models"' in html
    assert "function renderModels(models)" in script


def test_web_mode_uses_browser_microphone_and_speaker() -> None:
    script = (Path(STATIC_DIR) / "app.js").read_text(encoding="utf-8")

    assert "navigator.mediaDevices.getUserMedia" in script
    assert "new AudioWorkletNode" in script
    assert "audio_chunk" in script


def test_browser_audio_frame_decodes_float32_pcm() -> None:
    expected = np.linspace(-1, 1, 512, dtype="<f4")
    actual = decode_browser_audio(expected.tobytes(), frame_samples=512)

    np.testing.assert_array_equal(actual, expected)


@pytest.mark.asyncio
async def test_websocket_routes_pcm_from_every_client() -> None:
    frame = np.linspace(-1, 1, 512, dtype="<f4")
    orchestrator = Mock()
    orchestrator.interrupt = AsyncMock()
    speaker = Mock()
    input_router = Mock()
    clients = []

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
        clients.append(websocket)

        await _receive_actions(
            websocket,
            orchestrator,
            speaker,
            input_router,
            frame_samples=512,
        )

    assert input_router.feed.call_count == 2
    assert [call.args[0] for call in input_router.feed.call_args_list] == clients
    for call in input_router.feed.call_args_list:
        np.testing.assert_array_equal(call.args[1], frame)
    assert speaker.mark_started.call_count == 2
    assert speaker.mark_drained.call_count == 2
