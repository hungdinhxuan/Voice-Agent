import asyncio
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from app.cancellation import TurnCancellation
from app.config import AppConfig
from app.orchestrator import VoiceOrchestrator, _matches_interrupt_phrase
from app.state import ConversationState
from app.utils.timing import TurnTiming


@pytest.mark.asyncio
async def test_speech_during_playback_becomes_interrupt_candidate() -> None:
    orchestrator = VoiceOrchestrator(AppConfig())
    orchestrator.state.transition(ConversationState.LISTENING)
    orchestrator.state.transition(ConversationState.PROCESSING)
    orchestrator.state.transition(ConversationState.SPEAKING)
    active_turn = asyncio.create_task(asyncio.sleep(60))
    orchestrator._turn_task = active_turn

    await orchestrator._on_speech_start()

    assert orchestrator.state.state is ConversationState.SPEAKING
    assert not active_turn.done()
    assert orchestrator._interrupt_candidate

    active_turn.cancel()
    await asyncio.gather(active_turn, return_exceptions=True)
    await orchestrator.llm.close()


@pytest.mark.asyncio
async def test_stop_phrase_cancels_active_turn() -> None:
    orchestrator = VoiceOrchestrator(AppConfig())
    orchestrator.state.transition(ConversationState.LISTENING)
    orchestrator.state.transition(ConversationState.PROCESSING)
    orchestrator.state.transition(ConversationState.SPEAKING)
    active_turn = asyncio.create_task(asyncio.sleep(60))
    orchestrator._turn_task = active_turn
    orchestrator._cancellation = cancellation = TurnCancellation()
    orchestrator._interrupt_candidate = True
    orchestrator.asr.transcribe = AsyncMock(return_value="Dừng lại!")

    await orchestrator._on_speech_end(np.zeros(16000, dtype="float32"))

    assert orchestrator.state.state is ConversationState.IDLE
    assert active_turn.cancelled()
    assert cancellation.cancelled
    await orchestrator.llm.close()


@pytest.mark.asyncio
async def test_non_stop_phrase_does_not_cancel_active_turn() -> None:
    orchestrator = VoiceOrchestrator(AppConfig())
    orchestrator.state.transition(ConversationState.LISTENING)
    orchestrator.state.transition(ConversationState.PROCESSING)
    orchestrator.state.transition(ConversationState.SPEAKING)
    active_turn = asyncio.create_task(asyncio.sleep(60))
    orchestrator._turn_task = active_turn
    orchestrator._interrupt_candidate = True
    orchestrator.asr.transcribe = AsyncMock(return_value="xin chào")

    await orchestrator._on_speech_end(np.zeros(16000, dtype="float32"))

    assert orchestrator.state.state is ConversationState.SPEAKING
    assert not active_turn.done()
    active_turn.cancel()
    await asyncio.gather(active_turn, return_exceptions=True)
    await orchestrator.llm.close()


@pytest.mark.parametrize("text", ["dừng", "DỪNG LẠI!", " ngừng lại. ", "Thôi!"])
def test_interrupt_phrase_matching_ignores_case_spacing_and_punctuation(text: str) -> None:
    assert _matches_interrupt_phrase(text, AppConfig().audio.interrupt_phrases)


@pytest.mark.asyncio
async def test_tts_receives_speech_safe_text() -> None:
    orchestrator = VoiceOrchestrator(AppConfig())
    received: list[str] = []

    async def synthesize(text: str, cancellation: TurnCancellation):
        del cancellation
        received.append(text)
        if False:
            yield np.empty(0, dtype=np.float32)

    orchestrator.tts.synthesize_stream = synthesize
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    await queue.put('**"Xin chào..."** [bạn] # nhé!!!')
    await queue.put(None)

    await orchestrator._consume_tts(1, queue, TurnTiming(), TurnCancellation())

    assert received == ["Xin chào. bạn nhé!"]
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
