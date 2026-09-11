from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

from app.xiaozhi_adapter.session.device_session import DeviceSession


@dataclass
class FakeTool:
    """One tool the fake device advertises over MCP."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    user_only: bool = False
    result: str = "true"
    is_error: bool = False
    error: tuple[int, str] | None = None
    delay: float = 0.0
    never_reply: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


class FakeXiaozhiDevice:
    """In-process stand-in for a 78/xiaozhi-esp32 device.

    Speaks the client half of the protocol and, for MCP, reproduces the quirks
    that `main/mcp_server.cc` actually has rather than what the documentation
    says: a non-numeric `id` is dropped silently, methods starting with
    `notifications` are ignored, the `tools/list` cursor is a tool name and is
    inclusive, `nextCursor` is omitted entirely on the last page, and user-only
    tools are hidden unless `withUserTools` is true.

    Lets the adapter be tested without hardware. It is not a substitute for
    testing against a real device.
    """

    def __init__(
        self,
        *,
        protocol_version: int = 1,
        supports_mcp: bool = True,
        supports_aec: bool = False,
        uplink_sample_rate: int = 16000,
        uplink_frame_duration_ms: int = 60,
        tools: list[FakeTool] | None = None,
        tools_page_size: int = 100,
    ) -> None:
        self.protocol_version = protocol_version
        self.supports_mcp = supports_mcp
        self.supports_aec = supports_aec
        self.uplink_sample_rate = uplink_sample_rate
        self.uplink_frame_duration_ms = uplink_frame_duration_ms
        self.tools = tools or []
        self.tools_page_size = tools_page_size
        self.transport = LoopbackTransport(self)
        self.json_messages: list[dict[str, Any]] = []
        self.audio_packets: list[bytes] = []
        self.audio_arrival: list[float] = []
        self.mcp_requests: list[dict[str, Any]] = []
        self.session: DeviceSession | None = None
        self._replies: list[asyncio.Task[None]] = []
        self._changed = asyncio.Event()

    # ------------------------------------------------------------------- wiring

    def attach(self, session: DeviceSession) -> None:
        self.session = session

    async def detach(self) -> None:
        for task in self._replies:
            task.cancel()
        for task in self._replies:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._replies.clear()

    # ------------------------------------------------------- device -> adapter

    def hello_message(self) -> dict[str, Any]:
        features: dict[str, bool] = {"mcp": self.supports_mcp}
        if self.supports_aec:
            features["aec"] = True
        return {
            "type": "hello",
            "version": self.protocol_version,
            "features": features,
            "transport": "websocket",
            "audio_params": {
                "format": "opus",
                "sample_rate": self.uplink_sample_rate,
                "channels": 1,
                "frame_duration": self.uplink_frame_duration_ms,
            },
        }

    async def listen_start(self, mode: str = "auto") -> None:
        await self._send({"type": "listen", "state": "start", "mode": mode})

    async def listen_stop(self) -> None:
        await self._send({"type": "listen", "state": "stop"})

    async def wake_word(self, text: str) -> None:
        await self._send({"type": "listen", "state": "detect", "text": text})

    async def abort(self, reason: str | None = None) -> None:
        message: dict[str, Any] = {"type": "abort"}
        if reason is not None:
            message["reason"] = reason
        await self._send(message)

    async def send_opus(self, packets: list[bytes]) -> None:
        assert self.session is not None
        for packet in packets:
            await self.session.handle_binary(packet)

    async def _send(self, message: dict[str, Any]) -> None:
        assert self.session is not None
        message.setdefault("session_id", self.session.session_id)
        await self.session.handle_text(json.dumps(message))

    # ------------------------------------------------------- adapter -> device

    def on_json(self, message: dict[str, Any]) -> None:
        self.json_messages.append(message)
        self._changed.set()
        if message.get("type") == "mcp":
            payload = message.get("payload")
            if isinstance(payload, dict):
                self.mcp_requests.append(payload)
                self._replies.append(
                    asyncio.create_task(self._reply_to_mcp(payload), name="fake-mcp")
                )

    def on_bytes(self, payload: bytes) -> None:
        self.audio_packets.append(payload)
        self.audio_arrival.append(time.monotonic())
        self._changed.set()

    # -------------------------------------------------------------- assertions

    def messages_of(self, kind: str) -> list[dict[str, Any]]:
        return [message for message in self.json_messages if message.get("type") == kind]

    def tts_states(self) -> list[str]:
        return [message.get("state") for message in self.messages_of("tts")]

    def message_kinds(self) -> list[str]:
        return [str(message.get("type")) for message in self.json_messages]

    async def wait_for(self, predicate, timeout: float = 2.0) -> None:
        """Wait until `predicate(self)` holds, or fail the test with context."""

        deadline = time.monotonic() + timeout
        while not predicate(self):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"Hết thời gian chờ. Đã nhận: {self.message_kinds()}, "
                    f"{len(self.audio_packets)} gói audio."
                )
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except TimeoutError:
                continue

    # -------------------------------------------------------- fake MCP server

    async def _reply_to_mcp(self, payload: dict[str, Any]) -> None:
        if payload.get("jsonrpc") != "2.0":
            return
        method = payload.get("method")
        if not isinstance(method, str) or method.startswith("notifications"):
            return
        request_id = payload.get("id")
        if not isinstance(request_id, int) or isinstance(request_id, bool):
            return
        params = payload.get("params")
        params = params if isinstance(params, dict) else {}

        if method == "initialize":
            await self._result(request_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-board", "version": "1.0.0"},
            })
        elif method == "tools/list":
            await self._result(request_id, self._tools_list(params))
        elif method == "tools/call":
            await self._tools_call(request_id, params)
        else:
            await self._error(request_id, -32601, f"Method not implemented: {method}")

    def _tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        with_user_tools = params.get("withUserTools") is True
        visible = [tool for tool in self.tools if with_user_tools or not tool.user_only]
        cursor = params.get("cursor")
        start = 0
        if isinstance(cursor, str) and cursor:
            names = [tool.name for tool in visible]
            # The firmware cursor is inclusive: iteration resumes *at* the name.
            start = names.index(cursor) if cursor in names else len(visible)
        page = visible[start : start + self.tools_page_size]
        result: dict[str, Any] = {"tools": [tool.to_json() for tool in page]}
        remaining = visible[start + self.tools_page_size :]
        if remaining:
            result["nextCursor"] = remaining[0].name
        return result

    async def _tools_call(self, request_id: int, params: dict[str, Any]) -> None:
        name = params.get("name")
        tool = next((item for item in self.tools if item.name == name), None)
        if tool is None:
            await self._error(request_id, -32602, f"Unknown tool: {name}")
            return
        if tool.never_reply:
            return
        if tool.delay:
            await asyncio.sleep(tool.delay)
        if tool.error is not None:
            await self._error(request_id, tool.error[0], tool.error[1])
            return
        await self._result(request_id, {
            "content": [{"type": "text", "text": tool.result}],
            "isError": tool.is_error,
        })

    async def send_notification(self, method: str, params: dict[str, Any]) -> None:
        await self._send_mcp({"jsonrpc": "2.0", "method": method, "params": params})

    async def _result(self, request_id: int, result: dict[str, Any]) -> None:
        await self._send_mcp({"jsonrpc": "2.0", "id": request_id, "result": result})

    async def _error(self, request_id: int, code: int, message: str) -> None:
        await self._send_mcp(
            {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
        )

    async def _send_mcp(self, payload: dict[str, Any]) -> None:
        await self._send({"type": "mcp", "payload": payload})


class LoopbackTransport:
    """`DeviceTransport` that hands frames straight to a `FakeXiaozhiDevice`."""

    def __init__(self, device: FakeXiaozhiDevice) -> None:
        self._device = device

    async def send_json(self, message: dict[str, Any]) -> None:
        self._device.on_json(message)

    async def send_bytes(self, payload: bytes) -> None:
        self._device.on_bytes(payload)
