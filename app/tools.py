from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.cancellation import TurnCancellation


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One callable tool, described with a JSON Schema object for its arguments."""

    name: str
    description: str
    parameters: dict[str, Any]

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LLMDelta:
    """One step of a model turn: streamed text, or the tool calls it asked for."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


@runtime_checkable
class ToolProvider(Protocol):
    """Supplies tools to the LLM and executes them.

    A single awaitable call keeps request and result correlated by construction,
    so nothing has to reconcile a tool result with the turn it belongs to: if the
    turn is cancelled, the awaiting task dies with it.
    """

    def specs(self) -> list[ToolSpec]: ...

    async def call(self, call: ToolCall, cancellation: TurnCancellation) -> str: ...


def assistant_tool_message(calls: tuple[ToolCall, ...]) -> dict[str, Any]:
    """The assistant turn that requested tools, fed back for the next round.

    `arguments` is an object, not the JSON-encoded string OpenAI uses: Ollama
    parses this field itself and rejects a string with
    `Value looks like object, but can't find closing '}' symbol`.
    """

    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in calls
        ],
    }


def tool_result_message(call: ToolCall, result: str) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call.id,
        "tool_name": call.name,
        "content": result,
    }
