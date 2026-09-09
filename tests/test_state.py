import pytest

from app.state import ConversationState, InvalidStateTransition, StateMachine


def test_normal_and_interrupted_transitions() -> None:
    machine = StateMachine()
    machine.transition(ConversationState.LISTENING)
    machine.transition(ConversationState.PROCESSING)
    machine.transition(ConversationState.SPEAKING)
    machine.transition(ConversationState.INTERRUPTED)
    machine.transition(ConversationState.LISTENING)
    assert machine.state is ConversationState.LISTENING


def test_rejects_invalid_transition() -> None:
    machine = StateMachine()
    with pytest.raises(InvalidStateTransition):
        machine.transition(ConversationState.SPEAKING)

