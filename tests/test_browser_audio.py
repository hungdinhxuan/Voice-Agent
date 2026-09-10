import asyncio

import numpy as np
import pytest

from app.config import AudioConfig
from app.web.audio import BrowserAudioOutput


@pytest.mark.asyncio
async def test_browser_output_streams_pcm_and_waits_for_client_ack() -> None:
    events: list[dict[str, object]] = []
    output = BrowserAudioOutput(AudioConfig(), events.append)
    samples = np.array([-1.0, 0.0, 1.0], dtype=np.float32)
    output.begin_turn(7)

    await output.enqueue(7, samples)

    chunk = events[-1]
    assert chunk["type"] == "audio_chunk"
    assert chunk["turn_id"] == 7
    decoded = np.frombuffer(chunk["pcm"], dtype="<f4")
    np.testing.assert_array_equal(decoded, samples)

    first_played = asyncio.create_task(output.wait_first_played(7))
    await asyncio.sleep(0)
    assert not first_played.done()
    output.mark_started(7)
    await first_played

    drained = asyncio.create_task(output.wait_drained(7))
    await asyncio.sleep(0)
    assert events[-1] == {"type": "audio_end", "turn_id": 7}
    assert not drained.done()
    output.mark_drained(7)
    await drained


@pytest.mark.asyncio
async def test_browser_output_clear_cancels_client_playback() -> None:
    events: list[dict[str, object]] = []
    output = BrowserAudioOutput(AudioConfig(), events.append)
    output.begin_turn(3)
    await output.enqueue(3, np.ones(4, dtype=np.float32))

    await output.clear()

    assert events[-1] == {"type": "audio_clear", "turn_id": 3}


@pytest.mark.asyncio
async def test_browser_output_gives_up_when_client_never_acknowledges() -> None:
    events: list[dict[str, object]] = []
    output = BrowserAudioOutput(AudioConfig(), events.append, ack_grace=0.01)
    output.begin_turn(11)
    await output.enqueue(11, np.ones(480, dtype=np.float32))

    await output.wait_first_played(11)
    await output.wait_drained(11)

    warnings = [event["message"] for event in events if event["type"] == "log"]
    assert any("audio_started" in message for message in warnings)
    assert any("audio_drained" in message for message in warnings)


@pytest.mark.asyncio
async def test_browser_output_waits_even_when_audio_is_not_queued_yet() -> None:
    events: list[dict[str, object]] = []
    output = BrowserAudioOutput(AudioConfig(), events.append, ack_grace=5.0)
    output.begin_turn(5)

    first_played = asyncio.create_task(output.wait_first_played(5))
    await asyncio.sleep(0)
    assert not first_played.done()

    await output.enqueue(5, np.ones(3, dtype=np.float32))
    output.mark_started(5)
    await first_played
