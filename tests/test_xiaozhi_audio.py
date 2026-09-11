from __future__ import annotations

import asyncio
import time

import numpy as np
import pytest

from app.xiaozhi_adapter.audio.codec import OpusCodecError, OpusDecoder, OpusEncoder
from app.xiaozhi_adapter.audio.pacing import PacedFrameSender
from app.xiaozhi_adapter.audio.pipeline import DownlinkPipeline, UplinkPipeline
from app.xiaozhi_adapter.audio.resampler import FrameBlocker, StreamResampler


def tone(samples: int, rate: int, frequency: float = 220.0) -> np.ndarray:
    axis = np.arange(samples, dtype=np.float32) / rate
    return (0.3 * np.sin(2 * np.pi * frequency * axis)).astype(np.float32)


# --------------------------------------------------------------------- codec


def test_uplink_frame_is_960_samples_at_16_khz() -> None:
    encoder = OpusEncoder(16000, 60, 24000)
    try:
        assert encoder.frame_samples == 960
    finally:
        encoder.close()


def test_downlink_frame_matches_the_negotiated_duration() -> None:
    encoder = OpusEncoder(24000, 60, 24000)
    try:
        # The device sizes its decode buffer as rate/1000 * frame_duration.
        assert encoder.frame_samples == 1440
    finally:
        encoder.close()


def test_encoder_rejects_a_block_of_the_wrong_length() -> None:
    encoder = OpusEncoder(24000, 60, 24000)
    try:
        with pytest.raises(OpusCodecError, match="1440 samples"):
            encoder.encode(np.zeros(1000, dtype=np.float32))
    finally:
        encoder.close()


def test_opus_round_trip_preserves_the_signal() -> None:
    encoder = OpusEncoder(16000, 60, 32000)
    decoder = OpusDecoder()
    try:
        source = tone(960 * 10, 16000)
        packets: list[bytes] = []
        for index in range(10):
            packets.extend(encoder.encode(source[index * 960 : (index + 1) * 960]))
        assert len(packets) == 10

        decoded = np.concatenate([decoder.decode(packet) for packet in packets])
        # FFmpeg's Opus decoder always emits 48 kHz, whatever the packet rate.
        assert decoder.sample_rate == 48000
        assert decoded.size == 10 * 2880
        # Opus has codec delay, so compare energy rather than sample alignment.
        assert 0.1 < float(np.sqrt(np.mean(decoded[2880:] ** 2))) < 0.5
    finally:
        encoder.close()
        decoder.close()


def test_decoder_survives_empty_and_corrupt_packets() -> None:
    decoder = OpusDecoder()
    try:
        assert decoder.decode(b"").size == 0
        # Undecodable input must not take the session down.
        decoder.decode(b"\x00\x01\x02\x03")
    finally:
        decoder.close()


# ----------------------------------------------------------------- resampling


def test_resampler_passes_through_when_rates_match() -> None:
    resampler = StreamResampler(24000, 24000)
    block = tone(480, 24000)

    assert resampler.passthrough
    assert np.array_equal(resampler.push(block), block)
    assert resampler.flush().size == 0


def test_resampler_conserves_duration_across_chunks() -> None:
    resampler = StreamResampler(48000, 16000)

    produced = sum(resampler.push(tone(2880, 48000)).size for _ in range(10))
    produced += resampler.flush().size

    # 10 x 60 ms at 48 kHz is 600 ms, which is 9600 samples at 16 kHz.
    assert abs(produced - 9600) <= 32


def test_frame_blocker_splits_960_into_512_sample_agent_frames() -> None:
    blocker = FrameBlocker(512)

    first = blocker.push(np.arange(960, dtype=np.float32))
    second = blocker.push(np.arange(960, dtype=np.float32))

    assert [frame.size for frame in first] == [512]
    assert [frame.size for frame in second] == [512, 512]
    assert blocker.pending == 384


def test_frame_blocker_zero_pads_only_on_flush() -> None:
    blocker = FrameBlocker(512)
    blocker.push(np.ones(100, dtype=np.float32))

    tail = blocker.flush()

    assert len(tail) == 1
    assert tail[0].size == 512
    assert float(tail[0][-1]) == 0.0
    assert blocker.flush() == []


def test_frame_blocker_reset_drops_buffered_audio() -> None:
    blocker = FrameBlocker(512)
    blocker.push(np.ones(300, dtype=np.float32))

    blocker.reset()

    assert blocker.pending == 0
    assert blocker.flush() == []


# ------------------------------------------------------------------ pipelines


def test_uplink_pipeline_yields_only_agent_sized_frames() -> None:
    encoder = OpusEncoder(16000, 60, 24000)
    pipeline = UplinkPipeline(agent_sample_rate=16000, agent_block_size=512)
    try:
        frames: list[np.ndarray] = []
        for index in range(12):
            block = tone(960 * 12, 16000)[index * 960 : (index + 1) * 960]
            for packet in encoder.encode(block):
                frames.extend(pipeline.push(packet))

        assert frames
        assert all(frame.size == 512 for frame in frames)
        assert all(frame.dtype == np.float32 for frame in frames)
        # 12 x 60 ms is 720 ms, i.e. 11520 samples, i.e. 22 whole blocks.
        assert 19 <= len(frames) <= 23
    finally:
        encoder.close()
        pipeline.close()


def test_downlink_pipeline_encodes_whole_frames_and_flushes_the_tail() -> None:
    pipeline = DownlinkPipeline(
        agent_sample_rate=48000,
        device_sample_rate=24000,
        frame_duration_ms=60,
        bitrate=24000,
    )
    try:
        pipeline.begin_turn()
        # 2880 samples at 48 kHz is 1440 at 24 kHz: exactly one frame.
        assert pipeline.push(tone(2880, 48000)) == []
        packets = pipeline.push(tone(2880 * 3, 48000))

        assert 2 <= len(packets) <= 4
        assert all(packet for packet in packets)
        assert len(pipeline.flush()) >= 1
    finally:
        pipeline.close()


def test_downlink_pipeline_does_not_carry_audio_between_turns() -> None:
    pipeline = DownlinkPipeline(48000, 24000, 60, 24000)
    try:
        pipeline.begin_turn()
        pipeline.push(tone(1000, 48000))

        pipeline.begin_turn()

        assert pipeline.flush() == []
    finally:
        pipeline.close()


# -------------------------------------------------------------------- pacing


async def test_pacing_sends_the_prebuffer_immediately_then_paces() -> None:
    sent: list[float] = []

    async def send(_: bytes) -> None:
        sent.append(time.monotonic())

    pacer = PacedFrameSender(60, send, prebuffer_frames=3, max_queued_frames=64)
    pacer.start()
    pacer.begin_turn()
    started = time.monotonic()
    for index in range(6):
        await pacer.submit(f"frame-{index}".encode())
    await pacer.wait_drained(5.0)
    await pacer.close()

    assert len(sent) == 6
    # The first three go out as a burst to cut first-audio latency.
    assert sent[2] - started < 0.05
    # The rest follow the 60 ms frame clock, so six frames span ~300 ms.
    assert 0.24 <= sent[5] - started <= 0.45


async def test_pacing_does_not_accumulate_drift() -> None:
    sent: list[float] = []

    async def send(_: bytes) -> None:
        sent.append(time.monotonic())
        # A slow socket must not push the schedule out frame by frame.
        await asyncio.sleep(0.01)

    pacer = PacedFrameSender(20, send, prebuffer_frames=0, max_queued_frames=64)
    pacer.start()
    pacer.begin_turn()
    started = time.monotonic()
    for index in range(10):
        await pacer.submit(f"frame-{index}".encode())
    await pacer.wait_drained(5.0)
    await pacer.close()

    elapsed = sent[-1] - started
    # Naive sleep(frame) plus 10 ms of send cost would take ~270 ms.
    assert 0.17 <= elapsed <= 0.24


async def test_clear_drops_queued_frames_and_resets_the_clock() -> None:
    sent: list[bytes] = []

    async def send(frame: bytes) -> None:
        sent.append(frame)

    pacer = PacedFrameSender(60, send, prebuffer_frames=1, max_queued_frames=64)
    pacer.start()
    pacer.begin_turn()
    for index in range(8):
        await pacer.submit(f"stale-{index}".encode())
    await asyncio.sleep(0)
    pacer.clear()
    await asyncio.sleep(0.05)

    assert len(sent) <= 2
    assert pacer.queued == 0
    assert pacer.remaining_playback_seconds() == 0.0
    await pacer.close()


async def test_submit_applies_backpressure_instead_of_dropping_audio() -> None:
    release = asyncio.Event()

    async def send(_: bytes) -> None:
        await release.wait()

    pacer = PacedFrameSender(60, send, prebuffer_frames=0, max_queued_frames=2)
    pacer.start()
    pacer.begin_turn()
    # One frame is in flight inside `send`, so the queue itself holds two more.
    for frame in (b"a", b"b", b"c"):
        await pacer.submit(frame)
    blocked = asyncio.create_task(pacer.submit(b"d"))
    await asyncio.sleep(0.05)

    assert not blocked.done()
    assert pacer.queued == 2

    release.set()
    await asyncio.wait_for(blocked, 2.0)
    await pacer.close()
