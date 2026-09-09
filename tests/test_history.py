from app.conversation.history import ConversationHistory


def test_history_keeps_system_and_last_complete_turns() -> None:
    history = ConversationHistory("system", max_turns=2)
    history.commit("u1", "a1")
    history.commit("u2", "a2")
    history.commit("u3", "a3")
    assert history.messages == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
    ]

