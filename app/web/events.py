from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any


_PLAYBACK_CONTROL = frozenset({"audio_end", "audio_clear"})


class EventBroker:
    def __init__(self, history_size: int = 200, queue_size: int = 256) -> None:
        self._history: deque[dict[str, Any]] = deque(maxlen=history_size)
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._queue_size = queue_size
        self._sequence = 0
        self._dropped = 0

    @property
    def dropped(self) -> int:
        return self._dropped

    def publish(self, event: dict[str, Any]) -> None:
        self._sequence += 1
        enriched = {"id": self._sequence, "time": time.time(), **event}
        if event.get("type") not in {"audio_chunk", "audio_end", "audio_clear"}:
            self._history.append(enriched)
        for queue in tuple(self._subscribers):
            if queue.full() and not self._make_room(queue, enriched):
                continue
            queue.put_nowait(enriched)

    def _make_room(
        self,
        queue: asyncio.Queue[dict[str, Any]],
        incoming: dict[str, Any],
    ) -> bool:
        buffered: list[dict[str, Any]] = []
        while not queue.empty():
            buffered.append(queue.get_nowait())

        victim = _victim_index(buffered, incoming)
        if victim is None:
            for item in buffered:
                queue.put_nowait(item)
            self._dropped += 1
            return False

        del buffered[victim]
        for item in buffered:
            queue.put_nowait(item)
        self._dropped += 1
        return True

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
        for event in self._history:
            if not queue.full():
                queue.put_nowait(event)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)


def _victim_index(
    buffered: list[dict[str, Any]],
    incoming: dict[str, Any],
) -> int | None:
    """Drop audio first, then other events, and never a playback control event."""

    for index, item in enumerate(buffered):
        if item.get("type") == "audio_chunk":
            return index
    if incoming.get("type") == "audio_chunk":
        return None
    for index, item in enumerate(buffered):
        if item.get("type") not in _PLAYBACK_CONTROL:
            return index
    return 0 if incoming.get("type") in _PLAYBACK_CONTROL else None
