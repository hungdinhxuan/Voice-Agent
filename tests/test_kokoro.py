from collections.abc import Iterator

import numpy as np
import pytest

from app.cancellation import TurnCancellation
from app.config import AppConfig
from app.tts.factory import create_tts_service
from app.tts.kokoro import KokoroTTSService


class FakeKokoroPipeline:
    def __call__(self, text: str, voice: str) -> Iterator[tuple[str, str, np.ndarray]]:
        assert text == "Hello"
        assert voice == "af_heart"
        yield text, "həloʊ", np.array([0.25, -0.25], dtype=np.float32)


@pytest.mark.asyncio
async def test_kokoro_streams_float32_audio() -> None:
    service = KokoroTTSService(AppConfig().english.tts)
    service.pipeline = FakeKokoroPipeline()

    chunks = [
        chunk
        async for chunk in service.synthesize_stream("Hello", TurnCancellation())
    ]

    assert service.sample_rate == 24000
    np.testing.assert_array_equal(chunks[0], np.array([0.25, -0.25], dtype=np.float32))


def test_tts_factory_selects_kokoro_for_english() -> None:
    assert isinstance(create_tts_service(AppConfig().english.tts), KokoroTTSService)
