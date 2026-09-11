from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from enum import Enum

import numpy as np

from app.config import VADConfig


class VADEventType(str, Enum):
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"


@dataclass(slots=True)
class VADEvent:
    kind: VADEventType
    audio: np.ndarray | None = None


class SileroVADSegmenter:
    def __init__(self, config: VADConfig, sample_rate: int = 16000, frame_samples: int = 512) -> None:
        from silero_vad import load_silero_vad

        self.config = config
        self.sample_rate = sample_rate
        self.frame_samples = frame_samples
        self.model = load_silero_vad(onnx=True)
        frame_ms = frame_samples * 1000 / sample_rate
        self._speech_frames = max(1, math.ceil(config.min_speech_ms / frame_ms))
        self._silence_frames = max(1, math.ceil(config.min_silence_ms / frame_ms))
        self._pad_frames = max(0, math.ceil(config.speech_pad_ms / frame_ms))
        self._pre_roll: deque[np.ndarray] = deque(maxlen=max(1, self._pad_frames + self._speech_frames))
        self._buffer: list[np.ndarray] = []
        self._speech_run = 0
        self._silence_run = 0
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def reset(self) -> None:
        if hasattr(self.model, "reset_states"):
            self.model.reset_states()
        self._pre_roll.clear()
        self._buffer.clear()
        self._speech_run = 0
        self._silence_run = 0
        self._active = False

    def flush(self) -> VADEvent | None:
        """Close the current utterance now, without waiting for trailing silence.

        Needed when the client owns end-of-speech (a push-to-talk release) rather
        than the server's silence detector.
        """

        if not self._active or not self._buffer:
            return None
        utterance = np.concatenate(self._buffer).astype(np.float32, copy=False)
        self._buffer = []
        self._speech_run = 0
        self._silence_run = 0
        self._active = False
        return VADEvent(VADEventType.SPEECH_END, utterance)

    def process(self, frame: np.ndarray) -> list[VADEvent]:
        import torch

        audio = np.asarray(frame, dtype=np.float32).reshape(-1)
        if audio.size != self.frame_samples:
            raise ValueError(f"Silero VAD cần frame {self.frame_samples} samples, nhận {audio.size}.")
        probability = float(self.model(torch.from_numpy(audio), self.sample_rate).item())
        is_speech = probability >= self.config.threshold
        events: list[VADEvent] = []

        if not self._active:
            self._pre_roll.append(audio.copy())
            self._speech_run = self._speech_run + 1 if is_speech else 0
            if self._speech_run >= self._speech_frames:
                self._active = True
                self._buffer = list(self._pre_roll)
                self._pre_roll.clear()
                self._silence_run = 0
                events.append(VADEvent(VADEventType.SPEECH_START))
            return events

        self._buffer.append(audio.copy())
        self._silence_run = 0 if is_speech else self._silence_run + 1
        if self._silence_run >= self._silence_frames:
            keep_silence = min(self._pad_frames, self._silence_run)
            if self._silence_run > keep_silence:
                del self._buffer[-(self._silence_run - keep_silence) :]
            utterance = np.concatenate(self._buffer).astype(np.float32, copy=False)
            self._buffer = []
            self._speech_run = 0
            self._silence_run = 0
            self._active = False
            events.append(VADEvent(VADEventType.SPEECH_END, utterance))
        return events

