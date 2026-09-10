from types import SimpleNamespace

from app.config import AppConfig
from app.web.sessions import WebVoiceSession


def test_web_sessions_isolate_history_and_events() -> None:
    config = AppConfig()
    runtime = SimpleNamespace(asr=object(), llm=object(), tts=object(), loaded=True)
    first = WebVoiceSession(config, runtime)
    second = WebVoiceSession(config, runtime)
    first_events = first.subscribe()
    second_events = second.subscribe()

    first.orchestrator.history.commit("một", "phản hồi một")
    assert first.orchestrator.history.messages != second.orchestrator.history.messages
    first.orchestrator.clear_history()

    assert first.orchestrator.history.messages == second.orchestrator.history.messages
    assert first_events.get_nowait()["type"] == "history_cleared"
    assert second_events.empty()
    assert first.id != second.id
