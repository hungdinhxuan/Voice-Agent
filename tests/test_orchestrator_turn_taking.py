import asyncio
from unittest.mock import patch

import pytest

from app.config import AppConfig
from app.orchestrator import VoiceOrchestrator
from app.state import ConversationState


@pytest.mark.asyncio
async def test_speaker_echo_does_not_start_a_new_turn() -> None:
    orchestrator = VoiceOrchestrator(AppConfig())
    orchestrator.state.transition(ConversationState.LISTENING)
    orchestrator.state.transition(ConversationState.PROCESSING)
    orchestrator.state.transition(ConversationState.SPEAKING)
    active_turn = asyncio.create_task(asyncio.sleep(60))
    orchestrator._turn_task = active_turn

    await orchestrator._on_speech_start()

    assert orchestrator.state.state is ConversationState.SPEAKING
    assert not active_turn.done()

    active_turn.cancel()
    await asyncio.gather(active_turn, return_exceptions=True)
    await orchestrator.llm.close()


@pytest.mark.asyncio
async def test_microphone_waits_for_echo_guard_after_response() -> None:
    orchestrator = VoiceOrchestrator(AppConfig())
    orchestrator.state.transition(ConversationState.LISTENING)
    orchestrator.state.transition(ConversationState.PROCESSING)
    orchestrator.state.transition(ConversationState.SPEAKING)

    with patch("app.orchestrator.time.monotonic", return_value=10.0):
        orchestrator._transition(ConversationState.IDLE)
        assert not orchestrator._microphone_is_open()

    with patch("app.orchestrator.time.monotonic", return_value=10.7):
        assert orchestrator._microphone_is_open()

    await orchestrator.llm.close()
