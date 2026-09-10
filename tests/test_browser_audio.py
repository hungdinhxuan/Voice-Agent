import asyncio
import base64

import numpy as np
import pytest

from app.config import AudioConfig
from app.web.audio import BrowserAudioInputRouter, BrowserAudioOutput


def test_browser_input_router_avoids_mixing_client_streams() -> None:
    received: list[np.ndarray] = []
    router = BrowserAudioInputRouter(received.append)
    client_one = object()
    client_two = object()
    speech = np.full(512, 0.1, dtype=np.float32)

    assert router.feed(client_one, speech)
    assert not router.feed(client_two, speech)
    router.release()
    assert router.feed(client_two, speech)

    assert len(received) == 2


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
    decoded = np.frombuffer(base64.b64decode(str(chunk["pcm"])), dtype="<f4")
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
