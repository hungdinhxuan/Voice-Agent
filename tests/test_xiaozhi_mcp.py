from __future__ import annotations

import asyncio

import pytest

from app.cancellation import TurnCancellation
from app.tools import ToolCall
from app.xiaozhi_adapter.mcp.client import DeviceMcpClient, McpError
from app.xiaozhi_adapter.mcp.provider import DeviceToolProvider
from app.xiaozhi_adapter.mcp.tool_mapping import (
    DeviceToolCatalog,
    extract_tool_text,
    sanitize_tool_name,
)
from app.xiaozhi_adapter.protocol.state import DeviceState
from app.xiaozhi_adapter.testing import FakeTool, FakeXiaozhiDevice


VOLUME_TOOL = {
    "name": "self.audio_speaker.set_volume",
    "description": "Set the volume",
    "inputSchema": {
        "type": "object",
        "properties": {"volume": {"type": "integer", "minimum": 0, "maximum": 100}},
        "required": ["volume"],
    },
}


class ScriptedDevice:
    """Minimal MCP peer: records requests and replies from a script."""

    def __init__(self, client_holder: dict) -> None:
        self.requests: list[dict] = []
        self.replies: dict[str, object] = {}
        self._holder = client_holder

    async def send(self, payload: dict) -> None:
        self.requests.append(payload)
        method = payload.get("method")
        reply = self.replies.get(method)
        if reply is None:
            return
        result = reply(payload) if callable(reply) else reply
        if result is None:
            return
        asyncio.get_running_loop().call_soon(
            self._holder["client"].handle_payload,
            {"jsonrpc": "2.0", "id": payload["id"], **result},
        )


def make_client(timeout: float = 1.0) -> tuple[DeviceMcpClient, ScriptedDevice, list]:
    holder: dict = {}
    device = ScriptedDevice(holder)
    notifications: list[tuple[str, dict]] = []
    client = DeviceMcpClient(
        device.send,
        timeout=timeout,
        on_notification=lambda method, params: notifications.append((method, params)),
    )
    holder["client"] = client
    return client, device, notifications


# ------------------------------------------------------------------- mapping


def test_tool_names_are_sanitized_for_the_llm() -> None:
    assert sanitize_tool_name("self.audio_speaker.set_volume") == "self_audio_speaker_set_volume"
    assert sanitize_tool_name("plain_name-1") == "plain_name-1"


def test_catalog_maps_sanitized_names_back_to_device_names() -> None:
    catalog = DeviceToolCatalog()

    assert catalog.replace([VOLUME_TOOL]) == []
    assert catalog.names == ["self_audio_speaker_set_volume"]
    assert catalog.device_name("self_audio_speaker_set_volume") == (
        "self.audio_speaker.set_volume"
    )
    with pytest.raises(KeyError):
        catalog.device_name("nope")


def test_catalog_normalizes_the_schema_subset_the_device_emits() -> None:
    catalog = DeviceToolCatalog()
    catalog.replace([
        {
            "name": "self.x",
            "description": "d",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "on": {"type": "boolean", "default": False, "junk": 1},
                    "name": {"type": "string", "maxLength": 8},
                },
                "required": ["name", "ghost"],
            },
        }
    ])

    spec = catalog.specs()[0]
    assert spec.parameters["properties"]["on"] == {"type": "boolean", "default": False}
    assert spec.parameters["properties"]["name"] == {"type": "string", "maxLength": 8}
    # A `required` entry with no matching property would break tool calling.
    assert spec.parameters["required"] == ["name"]


def test_catalog_reports_unusable_and_colliding_tools() -> None:
    catalog = DeviceToolCatalog()

    skipped = catalog.replace([
        VOLUME_TOOL,
        # Sanitizes to the same name as the tool above, so it must not silently
        # shadow it.
        {"name": "self/audio/speaker/set/volume"},
        {"description": "no name at all"},
    ])

    assert catalog.names == ["self_audio_speaker_set_volume"]
    assert catalog.device_name("self_audio_speaker_set_volume") == (
        "self.audio_speaker.set_volume"
    )
    assert skipped == ["self/audio/speaker/set/volume", "None"]


def test_tool_result_text_extraction() -> None:
    assert extract_tool_text({"content": [{"type": "text", "text": "true"}]}) == "true"
    assert extract_tool_text(
        {"content": [{"type": "text", "text": "no"}], "isError": True}
    ).startswith("Tool error:")
    # An unexpected shape must still produce something the model can read.
    assert extract_tool_text({"weird": 1})


# -------------------------------------------------------------------- client


async def test_initialize_sends_a_numeric_id_and_no_notifications() -> None:
    client, device, _ = make_client()
    device.replies["initialize"] = {
        "result": {"protocolVersion": "2024-11-05", "serverInfo": {"name": "b", "version": "1"}}
    }

    await client.initialize()

    request = device.requests[0]
    assert request["jsonrpc"] == "2.0"
    # mcp_server.cc requires cJSON_IsNumber(id) and drops anything else.
    assert isinstance(request["id"], int)
    assert request["method"] == "initialize"
    assert client.server_info == {"name": "b", "version": "1"}
    # The device ignores every `notifications/*` method, so none is sent.
    assert [item["method"] for item in device.requests] == ["initialize"]


async def test_tools_list_follows_the_cursor_until_it_is_absent() -> None:
    client, device, _ = make_client()
    pages = [
        {"result": {"tools": [{"name": "a"}], "nextCursor": "b"}},
        {"result": {"tools": [{"name": "b"}], "nextCursor": "c"}},
        # Last page: the firmware omits nextCursor entirely.
        {"result": {"tools": [{"name": "c"}]}},
    ]
    device.replies["tools/list"] = lambda _: pages.pop(0)

    tools = await client.list_tools()

    assert [tool["name"] for tool in tools] == ["a", "b", "c"]
    cursors = [item.get("params", {}).get("cursor") for item in device.requests]
    assert cursors == [None, "b", "c"]
    # withUserTools is never sent, so the device keeps privileged tools hidden.
    assert all("withUserTools" not in item.get("params", {}) for item in device.requests)


async def test_tools_list_treats_an_empty_cursor_as_the_end() -> None:
    client, device, _ = make_client()
    device.replies["tools/list"] = {"result": {"tools": [{"name": "a"}], "nextCursor": ""}}

    assert len(await client.list_tools()) == 1
    assert len(device.requests) == 1


async def test_tools_list_stops_on_a_repeating_cursor() -> None:
    client, device, _ = make_client()
    device.replies["tools/list"] = {"result": {"tools": [], "nextCursor": "loop"}}

    with pytest.raises(McpError, match="lặp lại"):
        await client.list_tools()


async def test_call_tool_correlates_each_reply_with_its_own_request() -> None:
    client, device, _ = make_client()
    device.replies["tools/call"] = lambda payload: {
        "result": {"content": [{"type": "text", "text": f"id={payload['id']}"}]}
    }

    first, second = await asyncio.gather(
        client.call_tool("a", {}),
        client.call_tool("b", {}),
    )

    ids = [item["id"] for item in device.requests]
    assert len(set(ids)) == 2
    assert first["content"][0]["text"] == f"id={ids[0]}"
    assert second["content"][0]["text"] == f"id={ids[1]}"


async def test_tool_error_is_raised_with_the_device_code() -> None:
    client, device, _ = make_client()
    device.replies["tools/call"] = {
        "error": {"code": -32602, "message": "Unknown tool: self.nope"}
    }

    with pytest.raises(McpError) as raised:
        await client.call_tool("self.nope", {})

    assert raised.value.code == -32602
    assert "Unknown tool" in raised.value.error_message


async def test_a_silent_device_times_out_and_the_request_is_cleaned_up() -> None:
    client, device, _ = make_client(timeout=0.05)
    device.replies["tools/call"] = None

    with pytest.raises(McpError, match="hết thời gian"):
        await client.call_tool("a", {})

    assert client.pending_count == 0


async def test_a_reply_that_arrives_after_the_timeout_is_ignored() -> None:
    client, device, _ = make_client(timeout=0.05)
    device.replies["tools/call"] = None
    with pytest.raises(McpError):
        await client.call_tool("a", {})

    # The device answers late; nothing is waiting, and this must not raise.
    client.handle_payload({"jsonrpc": "2.0", "id": 1, "result": {"content": []}})

    assert client.pending_count == 0


async def test_notifications_are_delivered_and_never_answered() -> None:
    client, device, notifications = make_client()

    client.handle_payload(
        {"jsonrpc": "2.0", "method": "notifications/state_changed", "params": {"newState": "idle"}}
    )

    assert notifications == [("notifications/state_changed", {"newState": "idle"})]
    assert device.requests == []


def test_malformed_payloads_do_not_raise() -> None:
    client, _, _ = make_client()

    client.handle_payload({})
    client.handle_payload({"id": "not-a-number", "result": {}})
    client.handle_payload({"id": 999, "result": {}})
    client.handle_payload({"id": True, "result": {}})

    assert client.pending_count == 0


async def test_close_fails_every_pending_request() -> None:
    client, device, _ = make_client(timeout=10.0)
    device.replies["tools/call"] = None
    pending = asyncio.create_task(client.call_tool("a", {}))
    await asyncio.sleep(0)

    await client.close()

    with pytest.raises(McpError, match="đã đóng"):
        await pending
    with pytest.raises(McpError, match="đã đóng"):
        await client.call_tool("a", {})


# ------------------------------------------------------------------ provider


async def test_provider_loads_tools_and_calls_them_by_device_name() -> None:
    client, device, _ = make_client()
    device.replies["initialize"] = {"result": {"serverInfo": {"name": "b", "version": "1"}}}
    device.replies["tools/list"] = {"result": {"tools": [VOLUME_TOOL]}}
    device.replies["tools/call"] = {
        "result": {"content": [{"type": "text", "text": "true"}], "isError": False}
    }
    provider = DeviceToolProvider(client)

    await provider.start()
    result = await provider.call(
        ToolCall(id="call_0", name="self_audio_speaker_set_volume", arguments={"volume": 70}),
        TurnCancellation(),
    )

    assert provider.ready
    assert [spec.name for spec in provider.specs()] == ["self_audio_speaker_set_volume"]
    assert result == "true"
    call = next(item for item in device.requests if item["method"] == "tools/call")
    # The LLM sees the sanitized name; the device must receive its own.
    assert call["params"]["name"] == "self.audio_speaker.set_volume"
    assert call["params"]["arguments"] == {"volume": 70}


async def test_provider_stays_empty_when_the_handshake_fails() -> None:
    client, device, _ = make_client(timeout=0.05)
    device.replies["initialize"] = None
    messages: list[str] = []
    provider = DeviceToolProvider(client, log=lambda text, level: messages.append(text))

    await provider.start()

    # A device whose MCP is broken is still a usable voice device.
    assert not provider.ready
    assert provider.specs() == []
    assert any("MCP" in message for message in messages)


async def test_provider_refuses_a_tool_the_device_never_advertised() -> None:
    client, device, _ = make_client()
    device.replies["initialize"] = {"result": {}}
    device.replies["tools/list"] = {"result": {"tools": [VOLUME_TOOL]}}
    provider = DeviceToolProvider(client)
    await provider.start()

    with pytest.raises(KeyError):
        await provider.call(ToolCall(id="c", name="self_reboot", arguments={}), TurnCancellation())


async def test_provider_call_respects_turn_cancellation() -> None:
    client, device, _ = make_client()
    device.replies["initialize"] = {"result": {}}
    device.replies["tools/list"] = {"result": {"tools": [VOLUME_TOOL]}}
    provider = DeviceToolProvider(client)
    await provider.start()
    cancellation = TurnCancellation()
    cancellation.cancel()

    with pytest.raises(asyncio.CancelledError):
        await provider.call(
            ToolCall(id="c", name="self_audio_speaker_set_volume", arguments={"volume": 1}),
            cancellation,
        )


# --------------------------------------------------------- through a session


async def test_session_runs_the_mcp_handshake_over_the_transport(xiaozhi_harness) -> None:
    device = FakeXiaozhiDevice(tools=[FakeTool(name="self.audio_speaker.set_volume")])
    harness = await xiaozhi_harness(device=device)

    await device.wait_for(lambda item: item.session._tools.ready if item.session else False)

    methods = [request.get("method") for request in device.mcp_requests]
    assert methods == ["initialize", "tools/list"]
    assert harness.session.snapshot()["mcp_tools"] == 1
    assert harness.session.snapshot()["mcp_ready"] is True


async def test_session_paginates_the_device_tool_list(xiaozhi_harness) -> None:
    tools = [FakeTool(name=f"self.tool_{index}") for index in range(5)]
    device = FakeXiaozhiDevice(tools=tools, tools_page_size=2)
    harness = await xiaozhi_harness(device=device)

    await device.wait_for(lambda item: item.session._tools.ready if item.session else False)

    assert harness.session.snapshot()["mcp_tools"] == 5
    assert [request.get("method") for request in device.mcp_requests].count("tools/list") == 3


async def test_session_hides_user_only_tools_from_the_model(xiaozhi_harness) -> None:
    device = FakeXiaozhiDevice(tools=[
        FakeTool(name="self.audio_speaker.set_volume"),
        FakeTool(name="self.reboot", user_only=True),
    ])
    harness = await xiaozhi_harness(device=device)

    await device.wait_for(lambda item: item.session._tools.ready if item.session else False)

    names = harness.session._tools.catalog.names
    # Privileged actions must not become autonomous LLM tools.
    assert names == ["self_audio_speaker_set_volume"]


async def test_session_without_mcp_support_skips_the_handshake(xiaozhi_harness) -> None:
    device = FakeXiaozhiDevice(supports_mcp=False)
    harness = await xiaozhi_harness(device=device)

    assert device.mcp_requests == []
    assert harness.session._tools is None
    assert harness.session.snapshot()["mcp_ready"] is False


async def test_mcp_message_without_a_session_is_ignored(xiaozhi_harness) -> None:
    device = FakeXiaozhiDevice(supports_mcp=False)
    harness = await xiaozhi_harness(device=device)

    await device.send_notification("notifications/state_changed", {"newState": "idle"})

    assert harness.session.protocol_errors == 0


async def test_llm_tool_call_reaches_the_device_and_the_result_comes_back(
    xiaozhi_harness, stub_brain
) -> None:
    device = FakeXiaozhiDevice(tools=[
        FakeTool(name="self.audio_speaker.set_volume", result="volume set to 70")
    ])
    brain = stub_brain(
        tool_calls=[
            ToolCall(id="call_0", name="self_audio_speaker_set_volume", arguments={"volume": 70})
        ]
    )
    harness = await xiaozhi_harness(device=device, brain=brain)
    await device.wait_for(lambda item: item.session._tools.ready if item.session else False)

    await device.listen_start("auto")
    await device.send_opus(await harness.opus_packets(12))
    await device.wait_for(lambda item: "stop" in item.tts_states(), timeout=8.0)

    call = next(
        request for request in device.mcp_requests if request.get("method") == "tools/call"
    )
    assert call["params"]["name"] == "self.audio_speaker.set_volume"
    assert call["params"]["arguments"] == {"volume": 70}
    # The model got a second round after the tool result and still spoke.
    assert brain.tool_rounds_seen == 1
    assert device.audio_packets


async def test_tool_result_after_turn_cancellation_is_dropped(
    xiaozhi_harness, stub_brain
) -> None:
    device = FakeXiaozhiDevice(tools=[
        FakeTool(name="self.audio_speaker.set_volume", delay=5.0)
    ])
    brain = stub_brain(
        tool_calls=[
            ToolCall(id="call_0", name="self_audio_speaker_set_volume", arguments={"volume": 70})
        ]
    )
    harness = await xiaozhi_harness(device=device, brain=brain)
    await device.wait_for(lambda item: item.session._tools.ready if item.session else False)
    await device.listen_start("auto")
    await device.send_opus(await harness.opus_packets(12))
    await device.wait_for(
        lambda item: any(r.get("method") == "tools/call" for r in item.mcp_requests),
        timeout=8.0,
    )

    await device.abort()
    await asyncio.sleep(0.2)

    # The turn task died with the pending call, so nothing is left waiting and
    # the slow reply cannot resurrect the cancelled turn.
    assert harness.session._mcp.pending_count == 0
    assert harness.session.device.state is not DeviceState.SPEAKING
