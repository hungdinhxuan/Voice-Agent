from __future__ import annotations

import numpy as np

from app.xiaozhi_adapter.audio.codec import OpusDecoder, OpusEncoder
from app.xiaozhi_adapter.audio.resampler import FrameBlocker, StreamResampler


class UplinkPipeline:
    """Device Opus packets to voice-agent input frames.

    One packet in, zero or more agent frames out: 960 uplink samples do not
    divide into the agent's 512-sample blocks, so frames accumulate.
    """

    def __init__(self, agent_sample_rate: int, agent_block_size: int) -> None:
        self._decoder = OpusDecoder()
        self._resampler = StreamResampler(self._decoder.sample_rate, agent_sample_rate)
        self._blocker = FrameBlocker(agent_block_size)

    def push(self, packet: bytes) -> list[np.ndarray]:
        pcm = self._decoder.decode(packet)
        if pcm.size == 0:
            return []
        return self._blocker.push(self._resampler.push(pcm))

    def reset(self) -> None:
        """Drop buffered audio without touching the decoder's Opus state."""

        self._blocker.reset()

    def close(self) -> None:
        self._decoder.close()


class DownlinkPipeline:
    """Voice-agent TTS PCM to device Opus packets."""

    def __init__(
        self,
        agent_sample_rate: int,
        device_sample_rate: int,
        frame_duration_ms: int,
        bitrate: int,
    ) -> None:
        self.agent_sample_rate = agent_sample_rate
        self._encoder = OpusEncoder(device_sample_rate, frame_duration_ms, bitrate)
        self._device_sample_rate = device_sample_rate
        self._resampler = StreamResampler(agent_sample_rate, device_sample_rate)
        self._blocker = FrameBlocker(self._encoder.frame_samples)

    @property
    def frame_duration_ms(self) -> int:
        return self._encoder.frame_duration_ms

    def begin_turn(self) -> None:
        """Start a fresh resample/blocking state so turns cannot bleed together."""

        self._resampler = StreamResampler(self.agent_sample_rate, self._device_sample_rate)
        self._blocker.reset()

    def push(self, samples: np.ndarray) -> list[bytes]:
        packets: list[bytes] = []
        for frame in self._blocker.push(self._resampler.push(samples)):
            packets.extend(self._encoder.encode(frame))
        return packets

    def flush(self) -> list[bytes]:
        """Emit the tail of the turn, zero-padded to a whole Opus frame."""

        packets: list[bytes] = []
        for frame in self._blocker.push(self._resampler.flush()):
            packets.extend(self._encoder.encode(frame))
        for frame in self._blocker.flush():
            packets.extend(self._encoder.encode(frame))
        return packets

    def close(self) -> None:
        self._encoder.close()
