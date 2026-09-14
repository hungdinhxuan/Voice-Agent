from __future__ import annotations

import numpy as np

from app.xiaozhi_adapter.audio.codec import OpusDecoder, OpusEncoder
from app.xiaozhi_adapter.audio.resampler import FrameBlocker, StreamResampler
from app.xiaozhi_adapter.protocol.messages import AUDIO_FORMAT, PCM_FORMAT

_INT16_SCALE = 32768.0


class UplinkPipeline:
    """Device audio packets to voice-agent input frames.

    One packet in, zero or more agent frames out: 960 uplink samples do not
    divide into the agent's 512-sample blocks, so frames accumulate.

    Opus is what official firmware sends. A device that declared `pcm` sends
    little-endian 16-bit samples at its own rate instead, and the only thing
    that changes is which step turns bytes into floats - resampling and
    re-blocking are identical either way.
    """

    def __init__(
        self,
        agent_sample_rate: int,
        agent_block_size: int,
        *,
        source_format: str = AUDIO_FORMAT,
        device_sample_rate: int = 16000,
    ) -> None:
        if source_format == PCM_FORMAT:
            self._decoder: OpusDecoder | None = None
            source_rate = device_sample_rate
        else:
            self._decoder = OpusDecoder()
            # FFmpeg's Opus decoder always hands back 48 kHz whatever the device said.
            source_rate = self._decoder.sample_rate
        self._resampler = StreamResampler(source_rate, agent_sample_rate)
        self._blocker = FrameBlocker(agent_block_size)

    def push(self, packet: bytes) -> list[np.ndarray]:
        if self._decoder is None:
            pcm = _decode_pcm(packet)
        else:
            pcm = self._decoder.decode(packet)
        if pcm.size == 0:
            return []
        return self._blocker.push(self._resampler.push(pcm))

    def reset(self) -> None:
        """Drop buffered audio without touching the decoder's Opus state."""

        self._blocker.reset()

    def close(self) -> None:
        if self._decoder is not None:
            self._decoder.close()


def _decode_pcm(packet: bytes) -> np.ndarray:
    """Little-endian int16 to float32 in [-1, 1).

    A trailing odd byte is dropped rather than raising: a truncated frame is a
    transport hiccup, and losing half a sample is not worth killing a session.
    """

    usable = len(packet) - (len(packet) % 2)
    if usable <= 0:
        return np.empty(0, dtype=np.float32)
    samples = np.frombuffer(packet, dtype="<i2", count=usable // 2)
    return samples.astype(np.float32) / _INT16_SCALE


class DownlinkPipeline:
    """Voice-agent TTS PCM to device audio packets.

    Opus for official firmware. A device that declared `pcm` gets raw
    little-endian 16-bit frames instead: it has no Opus encoder, so assuming it
    has a decoder would be optimistic. The cost is bandwidth, and it is bounded
    in practice because a device only receives while it is not sending.
    """

    def __init__(
        self,
        agent_sample_rate: int,
        device_sample_rate: int,
        frame_duration_ms: int,
        bitrate: int,
        *,
        target_format: str = AUDIO_FORMAT,
    ) -> None:
        self.agent_sample_rate = agent_sample_rate
        self._device_sample_rate = device_sample_rate
        self._frame_duration_ms = frame_duration_ms
        if target_format == PCM_FORMAT:
            self._encoder: OpusEncoder | None = None
            frame_samples = device_sample_rate * frame_duration_ms // 1000
        else:
            self._encoder = OpusEncoder(device_sample_rate, frame_duration_ms, bitrate)
            frame_samples = self._encoder.frame_samples
            self._frame_duration_ms = self._encoder.frame_duration_ms
        self._resampler = StreamResampler(agent_sample_rate, device_sample_rate)
        self._blocker = FrameBlocker(frame_samples)

    @property
    def frame_duration_ms(self) -> int:
        return self._frame_duration_ms

    def _pack(self, frame: np.ndarray) -> list[bytes]:
        if self._encoder is not None:
            return list(self._encoder.encode(frame))
        clipped = np.clip(frame, -1.0, 1.0) * (_INT16_SCALE - 1)
        return [clipped.astype("<i2").tobytes()]

    def begin_turn(self) -> None:
        """Start a fresh resample/blocking state so turns cannot bleed together."""

        self._resampler = StreamResampler(self.agent_sample_rate, self._device_sample_rate)
        self._blocker.reset()

    def push(self, samples: np.ndarray) -> list[bytes]:
        packets: list[bytes] = []
        for frame in self._blocker.push(self._resampler.push(samples)):
            packets.extend(self._pack(frame))
        return packets

    def flush(self) -> list[bytes]:
        """Emit the tail of the turn, zero-padded to a whole frame."""

        packets: list[bytes] = []
        for frame in self._blocker.push(self._resampler.flush()):
            packets.extend(self._pack(frame))
        for frame in self._blocker.flush():
            packets.extend(self._pack(frame))
        return packets

    def close(self) -> None:
        if self._encoder is not None:
            self._encoder.close()
