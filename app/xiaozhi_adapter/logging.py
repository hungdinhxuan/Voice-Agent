from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any


LOGGER = logging.getLogger("xiaozhi")

_LEVELS = {"info": logging.INFO, "error": logging.ERROR, "debug": logging.DEBUG}


def configure(level: int = logging.INFO) -> None:
    """Give the adapter's logger somewhere to write.

    Uvicorn configures only its own loggers, so without this every session log
    below WARNING is swallowed by the root logger's last-resort handler.
    """

    if LOGGER.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s xiaozhi %(message)s", "%H:%M:%S"))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(level)
    LOGGER.propagate = False


class SessionLogger:
    """Structured per-session logging.

    Every line carries `device_id`, `session_id` and `turn_id` so a latency
    complaint can be traced to one turn on one device.

    Never logs microphone audio, TTS audio, Opus payloads, `Authorization`
    headers or access tokens. Transcript lines record a character count, not the
    text, so a log file is not a transcript of the room.
    """

    def __init__(
        self,
        *,
        device_id: str,
        session_id: str,
        logger: logging.Logger | None = None,
    ) -> None:
        self.device_id = device_id
        self.session_id = session_id
        self._logger = logger or LOGGER
        self._turn_id: Callable[[], int] = lambda: 0

    def bind_turn(self, turn_id: Callable[[], int]) -> None:
        self._turn_id = turn_id

    def event(self, name: str, **fields: Any) -> None:
        self._logger.info("%s", self._format(name, fields))

    def message(self, text: str, level: str = "info") -> None:
        """Human-readable line, shaped like the voice agent's own logs."""

        self._logger.log(_LEVELS.get(level, logging.INFO), "%s %s", self._prefix(), text)

    def _prefix(self) -> str:
        return (
            f"device_id={self.device_id} session_id={self.session_id} "
            f"turn_id={self._turn_id()}"
        )

    def _format(self, name: str, fields: dict[str, Any]) -> str:
        parts = [self._prefix(), f"event={name}"]
        parts.extend(f"{key}={value}" for key, value in fields.items() if value is not None)
        return " ".join(parts)
