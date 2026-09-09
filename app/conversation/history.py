from __future__ import annotations


class ConversationHistory:
    def __init__(self, system_prompt: str, max_turns: int) -> None:
        if max_turns < 1:
            raise ValueError("max_turns phải lớn hơn 0")
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self._turns: list[tuple[str, str]] = []

    def messages_with_user(self, user_text: str) -> list[dict[str, str]]:
        messages = self.messages
        messages.append({"role": "user", "content": user_text})
        return messages

    def commit(self, user_text: str, assistant_text: str) -> None:
        self._turns.append((user_text, assistant_text))
        if len(self._turns) > self.max_turns:
            self._turns = self._turns[-self.max_turns :]

    def clear(self) -> None:
        self._turns.clear()

    @property
    def messages(self) -> list[dict[str, str]]:
        result = [{"role": "system", "content": self.system_prompt}]
        for user_text, assistant_text in self._turns:
            result.append({"role": "user", "content": user_text})
            result.append({"role": "assistant", "content": assistant_text})
        return result
