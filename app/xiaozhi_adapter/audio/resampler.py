from __future__ import annotations

import numpy as np
import soxr


class StreamResampler:
    """Stateful mono float32 resampler.

    Streaming matters here: a stateless call per 60 ms packet leaves a
    discontinuity at every packet boundary, which the VAD hears as clicks.
    A pass-through is used when the rates already match so no filter delay is
    paid for nothing.
    """

    def __init__(self, source_rate: int, target_rate: int) -> None:
        self.source_rate = source_rate
        self.target_rate = target_rate
        self._stream = (
            None
            if source_rate == target_rate
            else soxr.ResampleStream(source_rate, target_rate, 1, dtype="float32")
        )

    @property
    def passthrough(self) -> bool:
        return self._stream is None

    def push(self, samples: np.ndarray) -> np.ndarray:
        audio = np.asarray(samples, dtype=np.float32).reshape(-1)
        if self._stream is None:
            return audio
        if audio.size == 0:
            return np.empty(0, dtype=np.float32)
        return np.asarray(self._stream.resample_chunk(audio), dtype=np.float32).reshape(-1)

    def flush(self) -> np.ndarray:
        if self._stream is None:
            return np.empty(0, dtype=np.float32)
        tail = self._stream.resample_chunk(np.empty(0, dtype=np.float32), last=True)
        return np.asarray(tail, dtype=np.float32).reshape(-1)


class FrameBlocker:
    """Re-blocks a variable-length sample stream into fixed-size frames.

    Both directions need it: 960-sample uplink packets do not divide into the
    agent's 512-sample VAD blocks, and TTS chunks are arbitrary lengths that
    have to become exact Opus frames.
    """

    def __init__(self, frame_samples: int) -> None:
        if frame_samples < 1:
            raise ValueError("frame_samples phải lớn hơn 0.")
        self.frame_samples = frame_samples
        self._buffer = np.empty(0, dtype=np.float32)

    @property
    def pending(self) -> int:
        return int(self._buffer.size)

    def push(self, samples: np.ndarray) -> list[np.ndarray]:
        audio = np.asarray(samples, dtype=np.float32).reshape(-1)
        if audio.size:
            self._buffer = np.concatenate((self._buffer, audio))
        frames: list[np.ndarray] = []
        count = self._buffer.size // self.frame_samples
        for index in range(count):
            start = index * self.frame_samples
            frames.append(self._buffer[start : start + self.frame_samples].copy())
        if count:
            self._buffer = self._buffer[count * self.frame_samples :].copy()
        return frames

    def flush(self) -> list[np.ndarray]:
        """Zero-pad whatever is left into one final frame."""

        if self._buffer.size == 0:
            return []
        padding = self.frame_samples - self._buffer.size
        frame = np.concatenate((self._buffer, np.zeros(padding, dtype=np.float32)))
        self._buffer = np.empty(0, dtype=np.float32)
        return [frame]

    def reset(self) -> None:
        self._buffer = np.empty(0, dtype=np.float32)
