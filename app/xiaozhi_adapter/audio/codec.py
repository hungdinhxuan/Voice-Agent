from __future__ import annotations

import fractions

import av
import numpy as np


_INT16_SCALE = 32768.0


class OpusCodecError(RuntimeError):
    pass


class OpusDecoder:
    """Decodes single Xiaozhi Opus packets into float32 mono PCM.

    FFmpeg's Opus decoder always emits 48 kHz regardless of the rate the packet
    was encoded at, so `sample_rate` is fixed and callers must resample.
    """

    sample_rate = 48000

    def __init__(self) -> None:
        context = av.CodecContext.create("libopus", "r")
        context.layout = "mono"
        context.open()
        self._context: av.CodecContext | None = context

    def decode(self, packet: bytes) -> np.ndarray:
        """Return the decoded samples, or an empty array for an undecodable packet."""

        if self._context is None:
            raise OpusCodecError("Opus decoder đã đóng.")
        if not packet:
            return np.empty(0, dtype=np.float32)
        try:
            frames = self._context.decode(av.Packet(packet))
        except av.FFmpegError as exc:
            raise OpusCodecError(f"Không giải mã được gói Opus: {exc}") from exc
        blocks = []
        for frame in frames:
            samples = frame.to_ndarray()
            mono = samples[0] if samples.ndim == 2 else samples
            blocks.append(np.asarray(mono, dtype=np.float32) / _INT16_SCALE)
        if not blocks:
            return np.empty(0, dtype=np.float32)
        return np.concatenate(blocks)

    def close(self) -> None:
        """PyAV frees the codec context on collection; drop the reference."""

        self._context = None


class OpusEncoder:
    """Encodes fixed-size float32 mono blocks into Xiaozhi downlink Opus packets.

    The device sizes its decode buffer from the `frame_duration` advertised in the
    server hello, so every block handed to `encode` must be exactly
    `frame_samples` long.
    """

    def __init__(self, sample_rate: int, frame_duration_ms: int, bitrate: int) -> None:
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        context = av.CodecContext.create("libopus", "w")
        context.sample_rate = sample_rate
        context.layout = "mono"
        context.format = "s16"
        context.bit_rate = bitrate
        context.options = {
            "frame_duration": str(frame_duration_ms),
            "application": "audio",
        }
        context.open()
        self._context: av.CodecContext | None = context
        self._time_base = fractions.Fraction(1, sample_rate)
        self._pts = 0
        if context.frame_size != self.frame_samples:
            raise OpusCodecError(
                f"Opus encoder dùng frame {context.frame_size} samples, "
                f"cần {self.frame_samples}."
            )

    @property
    def frame_samples(self) -> int:
        return self.sample_rate * self.frame_duration_ms // 1000

    def encode(self, block: np.ndarray) -> list[bytes]:
        if self._context is None:
            raise OpusCodecError("Opus encoder đã đóng.")
        audio = np.asarray(block, dtype=np.float32).reshape(-1)
        if audio.size != self.frame_samples:
            raise OpusCodecError(
                f"Opus encoder cần {self.frame_samples} samples, nhận {audio.size}."
            )
        pcm = np.clip(audio * _INT16_SCALE, -_INT16_SCALE, _INT16_SCALE - 1).astype(np.int16)
        frame = av.AudioFrame.from_ndarray(pcm.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = self.sample_rate
        frame.time_base = self._time_base
        frame.pts = self._pts
        self._pts += self.frame_samples
        try:
            packets = self._context.encode(frame)
        except av.FFmpegError as exc:
            raise OpusCodecError(f"Không encode được Opus: {exc}") from exc
        return [bytes(packet) for packet in packets]

    def close(self) -> None:
        """PyAV frees the codec context on collection; drop the reference."""

        self._context = None
