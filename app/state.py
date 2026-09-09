from __future__ import annotations

from enum import Enum


class ConversationState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"


_ALLOWED: dict[ConversationState, set[ConversationState]] = {
    ConversationState.IDLE: {ConversationState.LISTENING},
    ConversationState.LISTENING: {ConversationState.PROCESSING, ConversationState.IDLE},
    ConversationState.PROCESSING: {
        ConversationState.SPEAKING,
        ConversationState.INTERRUPTED,
        ConversationState.IDLE,
    },
    ConversationState.SPEAKING: {ConversationState.INTERRUPTED, ConversationState.IDLE},
    ConversationState.INTERRUPTED: {ConversationState.LISTENING, ConversationState.IDLE},
}


class InvalidStateTransition(RuntimeError):
    pass


class StateMachine:
    def __init__(self) -> None:
        self._state = ConversationState.IDLE

    @property
    def state(self) -> ConversationState:
        return self._state

    def transition(self, target: ConversationState) -> None:
        if target == self._state:
            return
        if target not in _ALLOWED[self._state]:
            raise InvalidStateTransition(f"Không thể chuyển {self._state.value} sang {target.value}")
        self._state = target

