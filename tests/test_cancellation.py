import asyncio

import pytest

from app.cancellation import TurnCancellation
from app.orchestrator import _usable_transcript


async def test_cancellation_reaches_async_and_model_thread() -> None:
    cancellation = TurnCancellation()
    waiter = asyncio.create_task(cancellation.wait())
    cancellation.cancel()
    await waiter
    assert cancellation.cancelled
    assert cancellation.thread_event.is_set()
    with pytest.raises(asyncio.CancelledError):
        cancellation.raise_if_cancelled()


def test_rejects_non_latin_noise_transcript_for_vietnamese() -> None:
    assert _usable_transcript("Xin chào bạn")
    assert not _usable_transcript("下梅子。")
