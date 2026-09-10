from __future__ import annotations

from app.config import TTSConfig
from app.tts.base import TTSService


def create_tts_service(config: TTSConfig) -> TTSService:
    if config.provider == "vieneu":
        from app.tts.vieneu import VieNeuTTSService

        return VieNeuTTSService(config)
    if config.provider == "kokoro":
        from app.tts.kokoro import KokoroTTSService

        return KokoroTTSService(config)
    raise ValueError(f"TTS provider không được hỗ trợ: {config.provider}")
