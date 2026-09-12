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



def test_history_replays_tool_calls_so_the_model_keeps_calling_them() -> None:
    """The measured failure: with only the prose kept, qwen3.5:4b stopped calling
    the robot tools from the third turn on and merely claimed the action."""

    history = ConversationHistory("system", max_turns=4)
    call = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "1", "type": "function",
             "function": {"name": "self_chassis_forward", "arguments": {"distance_cm": 20}}}
        ],
    }
    result = {"role": "tool", "tool_call_id": "1", "tool_name": "self_chassis_forward",
              "content": "sent F20"}
    history.commit("đi tới trước", "Đã đi tới trước.", [call, result])
    history.commit("còn pin không", "Còn 87 phần trăm.")

    assert history.messages == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "đi tới trước"},
        call,
        result,
        {"role": "assistant", "content": "Đã đi tới trước."},
        {"role": "user", "content": "còn pin không"},
        {"role": "assistant", "content": "Còn 87 phần trăm."},
    ]


def test_history_does_not_alias_the_tool_messages_it_was_given() -> None:
    history = ConversationHistory("system", max_turns=2)
    result = {"role": "tool", "tool_call_id": "1", "content": "sent F20"}
    history.commit("u", "a", [result])
    result["content"] = "đã bị sửa"
    assert history.messages[2]["content"] == "sent F20"
    history.messages[2]["content"] = "sửa qua bản trả về"
    assert history.messages[2]["content"] == "sent F20"
