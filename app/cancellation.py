from __future__ import annotations

import asyncio
import threading


class TurnCancellation:
    """Cancellation signal shared by asyncio orchestration and model threads."""

    def __init__(self) -> None:
        self._async_event = asyncio.Event()
        self._thread_event = threading.Event()

    @property
    def thread_event(self) -> threading.Event:
        return self._thread_event

    @property
    def cancelled(self) -> bool:
        return self._async_event.is_set()

    async def wait(self) -> None:
        await self._async_event.wait()

    def cancel(self) -> None:
        self._thread_event.set()
        self._async_event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError

