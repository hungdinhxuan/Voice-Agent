from __future__ import annotations

import asyncio
import threading

import numpy as np

from app.asr.base import ASRService
from app.cancellation import TurnCancellation
from app.config import ASRConfig


def _torch_dtype(name: str):
    import torch

    try:
        return getattr(torch, name)
    except AttributeError as exc:
        raise ValueError(f"Torch dtype không hợp lệ: {name}") from exc


class Qwen3ASRService(ASRService):
    def __init__(self, config: ASRConfig) -> None:
        self.config = config
        self.processor = None
        self.model = None
        self._inference_lock = threading.Lock()

    async def load(self) -> None:
        await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> None:
        from transformers import AutoModelForMultimodalLM, AutoProcessor

        dtype = _torch_dtype(self.config.dtype)
        self.processor = AutoProcessor.from_pretrained(self.config.model)
        self.model = AutoModelForMultimodalLM.from_pretrained(
            self.config.model,
            dtype=dtype,
            device_map={"": self.config.device},
            low_cpu_mem_usage=True,
        )
        self.model.eval()

    async def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int,
        cancellation: TurnCancellation | None = None,
    ) -> str:
        if sample_rate != 16000:
            raise ValueError("Qwen3-ASR cần audio 16 kHz.")
        if self.model is None or self.processor is None:
            raise RuntimeError("ASR chưa được load.")
        if cancellation:
            cancellation.raise_if_cancelled()
        text = await asyncio.to_thread(self._transcribe_sync, audio)
        if cancellation:
            cancellation.raise_if_cancelled()
        return text.strip()

    def _transcribe_sync(self, audio: np.ndarray) -> str:
        import torch

        assert self.processor is not None and self.model is not None
        with self._inference_lock:
            inputs = self.processor.apply_transcription_request(
                audio=np.asarray(audio, dtype=np.float32),
                language=self.config.language,
            ).to(self.model.device, self.model.dtype)
            with torch.inference_mode():
                output_ids = self.model.generate(**inputs, max_new_tokens=self.config.max_new_tokens)
        generated = output_ids[:, inputs["input_ids"].shape[1] :]
        decoded = self.processor.decode(generated, return_format="transcription_only")
        return decoded[0] if decoded else ""
