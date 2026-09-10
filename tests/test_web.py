from pathlib import Path

from fastapi.testclient import TestClient

from app.config import AppConfig
from app.web.events import EventBroker
from app.web.server import STATIC_DIR, create_web_app


def test_event_broker_replays_recent_events() -> None:
    broker = EventBroker(history_size=2)
    broker.publish({"type": "state", "state": "IDLE"})
    broker.publish({"type": "transcript", "text": "một"})
    broker.publish({"type": "transcript", "text": "hai"})
    queue = broker.subscribe()
    assert queue.get_nowait()["text"] == "một"
    assert queue.get_nowait()["text"] == "hai"


def test_web_assets_exist() -> None:
    for name in ("index.html", "app.js", "styles.css"):
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
