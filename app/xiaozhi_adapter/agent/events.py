from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


READY = "ready"
STATE = "state"
TRANSCRIPT = "transcript"
SENTENCE = "tts_sentence"
ASSISTANT_DONE = "assistant_done"
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"
METRIC = "metric"
LOG = "log"
FATAL = "fatal"


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """One thing the voice agent reported.

    Deliberately not a Xiaozhi type: the adapter translates these into protocol
    messages, so the agent never has to know a device exists.
    """

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)

    def text(self) -> str:
        value = self.payload.get("text")
        return value if isinstance(value, str) else ""

    def state(self) -> str:
        value = self.payload.get("state")
        return value if isinstance(value, str) else ""

    def name(self) -> str:
        value = self.payload.get("name")
        return value if isinstance(value, str) else ""
