from __future__ import annotations

from collections.abc import Sequence
from typing import Any


class ConversationHistory:
    """The conversation as the model will see it next turn.

    A turn that called tools keeps the call and its result, not just the
    sentence that came out at the end. Dropping them changes what the model
    learns from its own history: it sees "asked to move the robot, replied with
    a sentence saying it moved" and copies that, so from the third turn on it
    stops calling the tool and merely claims the action. Measured on
    qwen3.5:4b - 1 of 8 later turns called a tool without these messages, 8 of 8
    with them.
    """

    def __init__(self, system_prompt: str, max_turns: int) -> None:
        if max_turns < 1:
            raise ValueError("max_turns phải lớn hơn 0")
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self._turns: list[tuple[str, list[dict[str, Any]], str]] = []

    def messages_with_user(self, user_text: str) -> list[dict[str, Any]]:
        messages = self.messages
        messages.append({"role": "user", "content": user_text})
        return messages

    def commit(
        self,
        user_text: str,
        assistant_text: str,
        tool_messages: Sequence[dict[str, Any]] = (),
    ) -> None:
        self._turns.append((user_text, [dict(m) for m in tool_messages], assistant_text))
        if len(self._turns) > self.max_turns:
            self._turns = self._turns[-self.max_turns :]

    def clear(self) -> None:
        self._turns.clear()

    @property
    def messages(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]
        for user_text, tool_messages, assistant_text in self._turns:
            result.append({"role": "user", "content": user_text})
            result.extend(dict(m) for m in tool_messages)
            result.append({"role": "assistant", "content": assistant_text})
        return result
