from unittest.mock import AsyncMock, Mock

from app.config import AppConfig
from app.runtime import ModelRuntime
from app.web.sessions import WebVoiceSession


def test_web_sessions_isolate_history_and_events() -> None:
    config = AppConfig()
    runtime = ModelRuntime(config)
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


async def test_web_session_switches_language_and_resets_history() -> None:
    config = AppConfig()
    runtime = ModelRuntime(config)
    runtime.load_language = AsyncMock()
    session = WebVoiceSession(config, runtime)
    session._stop_orchestrator = AsyncMock()
    session.start = Mock()
    events = session.subscribe()

    await session.switch_language("en")

    assert session.language == "en"
    assert session.config.asr.model == "nvidia/parakeet-tdt-0.6b-v3"
    assert session.config.tts.model == "hexgrad/Kokoro-82M"
    assert session.config.audio.output_sample_rate == 24000
    assert [events.get_nowait()["type"] for _ in range(3)] == [
        "language_loading",
        "language_changed",
        "history_cleared",
    ]
    runtime.load_language.assert_awaited_once_with("en")
    session.start.assert_called_once()


async def test_web_session_keeps_current_language_when_model_load_fails() -> None:
    config = AppConfig()
    runtime = ModelRuntime(config)
    runtime.load_language = AsyncMock(side_effect=RuntimeError("download failed"))
    session = WebVoiceSession(config, runtime)
    events = session.subscribe()

    await session.switch_language("en")

    assert session.language == "vi"
    assert not session.switching_language
    assert [events.get_nowait()["type"] for _ in range(2)] == [
        "language_loading",
        "language_error",
    ]
