from __future__ import annotations

from app.asr.base import ASRService
from app.config import ASRConfig


def create_asr_service(config: ASRConfig) -> ASRService:
    if config.backend == "parakeet":
        from app.asr.parakeet import ParakeetCTCService

        return ParakeetCTCService(config)
    if config.backend == "qwen3":
        from app.asr.qwen3_asr import Qwen3ASRService

        return Qwen3ASRService(config)
    raise ValueError(f"ASR backend không được hỗ trợ: {config.backend}")
