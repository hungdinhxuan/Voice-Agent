from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import numpy as np

from app.asr.base import ASRService
from app.cancellation import TurnCancellation
from app.config import ASRConfig


class ParakeetCTCService(ASRService):
    def __init__(self, config: ASRConfig) -> None:
        self.config = config
        self.model = None
        self._inference_lock = threading.Lock()

    async def load(self) -> None:
        await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> None:
        import torch
        from huggingface_hub import hf_hub_download
        from nemo.collections.asr.models import ASRModel

        if self.config.checkpoint_file:
            checkpoint = hf_hub_download(
                repo_id=self.config.model,
                filename=self.config.checkpoint_file,
            )
            self.model = ASRModel.restore_from(
                restore_path=Path(checkpoint),
                map_location=torch.device(self.config.device),
            )
        else:
            self.model = ASRModel.from_pretrained(model_name=self.config.model)
            self.model = self.model.to(torch.device(self.config.device))
        self.model.eval()

    async def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int,
        cancellation: TurnCancellation | None = None,
    ) -> str:
        if sample_rate != 16000:
            raise ValueError("Parakeet cần audio 16 kHz.")
        if self.model is None:
            raise RuntimeError("ASR chưa được load.")
        if cancellation:
            cancellation.raise_if_cancelled()
        text = await asyncio.to_thread(self._transcribe_sync, audio)
        if cancellation:
            cancellation.raise_if_cancelled()
        return text.strip()

    def _transcribe_sync(self, audio: np.ndarray) -> str:
        import torch

        assert self.model is not None
        with self._inference_lock, torch.inference_mode():
            outputs = self.model.transcribe(
                audio=np.asarray(audio, dtype=np.float32),
                batch_size=1,
                num_workers=0,
                verbose=False,
            )
        if not outputs:
            return ""
        result = outputs[0]
        return str(result.text if hasattr(result, "text") else result)

    async def close(self) -> None:
        self.model = None
