from types import SimpleNamespace

import numpy as np
import pytest

from app.asr.factory import create_asr_service
from app.asr.parakeet import ParakeetCTCService
from app.asr.qwen3_asr import Qwen3ASRService
from app.config import ASRConfig


class FakeParakeetModel:
    def transcribe(self, **kwargs):
        assert kwargs["audio"].dtype == np.float32
        assert kwargs["batch_size"] == 1
        assert kwargs["num_workers"] == 0
        assert kwargs["verbose"] is False
        return [SimpleNamespace(text=" Xin chào ")]


@pytest.mark.asyncio
async def test_parakeet_transcribes_in_memory_audio() -> None:
    service = ParakeetCTCService(ASRConfig())
    service.model = FakeParakeetModel()

    text = await service.transcribe(np.zeros(1600, dtype=np.float64), 16000)

    assert text == "Xin chào"


def test_asr_factory_keeps_qwen_rollback_path() -> None:
    parakeet = create_asr_service(ASRConfig())
    qwen = create_asr_service(
        ASRConfig(backend="qwen3", model="Qwen/Qwen3-ASR-1.7B-hf")
    )

    assert isinstance(parakeet, ParakeetCTCService)
    assert isinstance(qwen, Qwen3ASRService)
