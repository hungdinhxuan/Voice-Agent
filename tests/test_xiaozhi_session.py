from __future__ import annotations

import asyncio
import json

import pytest

from app.state import ConversationState
from app.xiaozhi_adapter.protocol.messages import ProtocolError, parse_client_message
from app.xiaozhi_adapter.protocol.state import DeviceState
from app.xiaozhi_adapter.testing import FakeXiaozhiDevice


def hello_of(device: FakeXiaozhiDevice):
    return parse_client_message(json.dumps(device.hello_message()))


async def run_one_turn(harness, packets: int = 12) -> None:
    """Drive a full turn: listen start, mic audio, then wait for tts stop."""

    await harness.device.listen_start("auto")
    await harness.device.send_opus(await harness.opus_packets(packets))
    await harness.device.wait_for(lambda device: "stop" in device.tts_states(), timeout=8.0)


# ------------------------------------------------------------------ handshake


async def test_handshake_answers_with_a_valid_server_hello(xiaozhi_harness) -> None:
    harness = await xiaozhi_harness()

    hello = harness.device.messages_of("hello")[0]

    assert hello["transport"] == "websocket"
    assert hello["session_id"] == harness.session.session_id
    assert hello["audio_params"]["sample_rate"] == 24000
    assert hello["audio_params"]["frame_duration"] == 60
    # The firmware moves to Listening as soon as the hello lands.
    assert harness.session.device.state is DeviceState.LISTENING


async def test_handshake_rejects_an_unsupported_binary_version(xiaozhi_harness) -> None:
    device = FakeXiaozhiDevice(protocol_version=3)
    harness = await xiaozhi_harness(device=device, open_session=False)

    with pytest.raises(ProtocolError, match="Protocol-Version 3"):
        await harness.session.open(hello_of(device))

    assert harness.device.json_messages == []


async def test_each_device_gets_its_own_session_and_history(xiaozhi_harness) -> None:
    first = await xiaozhi_harness()
    second = await xiaozhi_harness()

    assert first.session.session_id != second.session.session_id
    first.session._agent._orchestrator.history.commit("một", "phản hồi")
    assert (
        first.session._agent._orchestrator.history.messages
        != second.session._agent._orchestrator.history.messages
    )


# ----------------------------------------------------------------- turn cycle


async def test_full_turn_emits_stt_tts_and_paced_opus(xiaozhi_harness) -> None:
    harness = await xiaozhi_harness()

    await run_one_turn(harness)

    kinds = [kind for kind in harness.device.message_kinds() if kind != "mcp"]
    assert kinds[0] == "hello"
    assert "stt" in kinds
    # tts start must precede audio: the device drops packets unless Speaking.
    assert harness.device.tts_states()[0] == "start"
    assert harness.device.tts_states()[-1] == "stop"
    assert kinds.index("stt") < kinds.index("tts")
    assert harness.device.messages_of("stt")[0]["text"] == harness.brain.transcript
    assert harness.device.audio_packets
    assert harness.session.device.state is DeviceState.LISTENING


async def test_sentence_start_precedes_the_audio_it_describes(xiaozhi_harness) -> None:
    harness = await xiaozhi_harness()

    await run_one_turn(harness)

    sentences = [
        message
        for message in harness.device.messages_of("tts")
        if message["state"] == "sentence_start"
    ]
    assert sentences
    assert sentences[0]["text"]


async def test_downlink_audio_is_paced_to_the_frame_clock(xiaozhi_harness) -> None:
    harness = await xiaozhi_harness(prebuffer_frames=2)

    await run_one_turn(harness)

    arrivals = harness.device.audio_arrival
    assert len(arrivals) >= 6
    span = arrivals[-1] - arrivals[0]
    # The queue would flush instantly if it were unpaced; the device only holds
    # 1200 / 60 = 20 packets before it starts dropping.
    assert span >= (len(arrivals) - 3) * 0.06 * 0.7


async def test_audio_is_dropped_while_the_device_is_not_speaking(xiaozhi_harness) -> None:
    harness = await xiaozhi_harness()
    sink = harness.session._sink

    # The device only queues packets in its Speaking state.
    assert not harness.session.device.accepts_audio
    sink.begin_turn(99)
    import numpy as np

    await sink.enqueue(99, np.full(48000, 0.1, dtype=np.float32))

    assert harness.device.audio_packets == []
    assert sink.frames_dropped_not_speaking > 0


async def test_two_turns_run_over_one_connection(xiaozhi_harness) -> None:
    harness = await xiaozhi_harness()

    await run_one_turn(harness)
    first_turn = harness.session._agent.turn_id
    harness.device.json_messages.clear()

    await run_one_turn(harness)

    assert harness.session._agent.turn_id > first_turn
    assert harness.device.tts_states()[-1] == "stop"


async def test_uplink_audio_outside_listening_is_counted_not_fed(xiaozhi_harness) -> None:
    harness = await xiaozhi_harness()
    await harness.device.listen_stop()
    frames_before = harness.vad.frames_seen

    await harness.device.send_opus(await harness.opus_packets(4))

    assert harness.vad.frames_seen == frames_before
    assert harness.session.uplink_frames_dropped == 4


async def test_manual_mode_listen_stop_closes_the_utterance(xiaozhi_harness) -> None:
    harness = await xiaozhi_harness()
    harness.vad.utterance_frames = 10_000  # never end on silence

    await harness.device.listen_start("manual")
    await harness.device.send_opus(await harness.opus_packets(3))
    await harness.device.listen_stop()
    await harness.device.wait_for(lambda device: "stop" in device.tts_states(), timeout=8.0)

    assert harness.device.messages_of("stt")
    # Manual mode leaves the device Idle rather than re-arming the microphone.
    assert harness.session.device.state is DeviceState.IDLE


async def test_wake_word_detect_is_logged_and_resets_the_buffer(xiaozhi_harness) -> None:
    harness = await xiaozhi_harness()

    await harness.device.wake_word("Hi XiaoZhi")

    assert harness.session.device.state is DeviceState.LISTENING


async def test_malformed_text_frame_does_not_end_the_session(xiaozhi_harness) -> None:
    harness = await xiaozhi_harness()

    await harness.session.handle_text("{ not json")
    await harness.session.handle_text(json.dumps({"type": "listen", "state": "bogus"}))
    await harness.session.handle_text(json.dumps({"type": "brand_new_message"}))

    assert harness.session.protocol_errors == 2
    await run_one_turn(harness)
    assert harness.device.tts_states()[-1] == "stop"


async def test_corrupt_opus_packet_is_skipped(xiaozhi_harness) -> None:
    harness = await xiaozhi_harness()
    await harness.device.listen_start("auto")

    await harness.device.send_opus([b"", b"\xff\xff\xff\xff"])

    assert harness.session.device.state is DeviceState.LISTENING


# ---------------------------------------------------------------- barge-in


async def test_abort_stops_tts_and_confirms_with_tts_stop(
    xiaozhi_harness, stub_brain
) -> None:
    brain = stub_brain(tts_chunks=60, tts_chunk_delay=0.02)
    harness = await xiaozhi_harness(brain=brain)
    await harness.device.listen_start("auto")
    await harness.device.send_opus(await harness.opus_packets(12))
    await harness.device.wait_for(lambda device: len(device.audio_packets) >= 2, timeout=8.0)
    sent_before_abort = len(harness.device.audio_packets)

    await harness.device.abort("wake_word_detected")
    await asyncio.sleep(0.3)

    assert harness.device.tts_states()[-1] == "stop"
    assert harness.session.device.state is DeviceState.LISTENING
    # Stale audio from the cancelled turn must not keep arriving.
    assert len(harness.device.audio_packets) - sent_before_abort <= 2
    assert harness.session._pacer.queued == 0


async def test_late_tts_audio_from_a_cancelled_turn_is_discarded(xiaozhi_harness) -> None:
    import numpy as np

    harness = await xiaozhi_harness()
    sink = harness.session._sink
    harness.session.device.on_tts_start()
    sink.begin_turn(7)
    await sink.enqueue(7, np.full(48000, 0.1, dtype=np.float32))
    delivered = len(harness.device.audio_packets)

    await sink.clear()
    await sink.enqueue(7, np.full(48000, 0.1, dtype=np.float32))
    await asyncio.sleep(0.05)

    assert len(harness.device.audio_packets) == delivered


async def test_abort_before_any_turn_is_harmless(xiaozhi_harness) -> None:
    harness = await xiaozhi_harness()

    await harness.device.abort()

    assert harness.device.tts_states() == []
    assert harness.session.device.state is DeviceState.LISTENING


async def test_a_new_turn_follows_an_abort_without_reconnecting(
    xiaozhi_harness, stub_brain
) -> None:
    brain = stub_brain(tts_chunks=60, tts_chunk_delay=0.02)
    harness = await xiaozhi_harness(brain=brain)
    await harness.device.listen_start("auto")
    await harness.device.send_opus(await harness.opus_packets(12))
    await harness.device.wait_for(lambda device: len(device.audio_packets) >= 2, timeout=8.0)
    await harness.device.abort()
    await asyncio.sleep(0.1)

    brain.tts_chunks = 3
    brain.tts_chunk_delay = 0.0
    harness.device.json_messages.clear()
    await run_one_turn(harness)

    assert harness.device.tts_states()[0] == "start"
    assert harness.device.tts_states()[-1] == "stop"


# ------------------------------------------------------------------ teardown


async def test_close_cancels_every_session_task(xiaozhi_harness, stub_brain) -> None:
    brain = stub_brain(tts_chunks=60, tts_chunk_delay=0.02)
    harness = await xiaozhi_harness(brain=brain)
    await harness.device.listen_start("auto")
    await harness.device.send_opus(await harness.opus_packets(12))
    await harness.device.wait_for(lambda device: len(device.audio_packets) >= 1, timeout=8.0)
    tasks = list(harness.session._tasks)

    await harness.session.close()
    await asyncio.sleep(0.05)

    assert tasks
    assert all(task.done() for task in tasks)
    assert harness.session._agent._task is None
    assert harness.session._pacer.queued == 0


async def test_close_is_idempotent(xiaozhi_harness) -> None:
    harness = await xiaozhi_harness()

    await harness.session.close()
    await harness.session.close()

    assert harness.session._closed


async def test_reconnect_starts_a_clean_session(xiaozhi_harness) -> None:
    first = await xiaozhi_harness()
    await run_one_turn(first)
    await first.session.close()

    second = await xiaozhi_harness()
    await run_one_turn(second)

    assert first.session.session_id != second.session.session_id
    assert second.device.tts_states()[-1] == "stop"
    assert second.session._agent._orchestrator.state.state is ConversationState.IDLE


async def test_snapshot_reports_the_session_without_leaking_content(
    xiaozhi_harness,
) -> None:
    harness = await xiaozhi_harness()

    snapshot = harness.session.snapshot()

    assert snapshot["device_id"] == "aa:bb:cc:dd:ee:ff"
    assert snapshot["protocol_version"] == 1
    assert snapshot["downlink_sample_rate"] == 24000
    assert snapshot["device_state"] == "listening"
    assert "text" not in snapshot
    assert "transcript" not in snapshot
