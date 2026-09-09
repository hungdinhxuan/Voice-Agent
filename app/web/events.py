from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any


class EventBroker:
    def __init__(self, history_size: int = 200, queue_size: int = 256) -> None:
        self._history: deque[dict[str, Any]] = deque(maxlen=history_size)
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._queue_size = queue_size
        self._sequence = 0

    def publish(self, event: dict[str, Any]) -> None:
        self._sequence += 1
        enriched = {"id": self._sequence, "time": time.time(), **event}
        self._history.append(enriched)
        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(enriched)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
        for event in self._history:
            if not queue.full():
                queue.put_nowait(event)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

