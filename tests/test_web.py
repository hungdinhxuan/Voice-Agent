from pathlib import Path

from app.web.events import EventBroker
from app.web.server import STATIC_DIR


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
