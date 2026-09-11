from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


PayloadSender = Callable[[dict[str, Any]], Awaitable[None]]
NotificationHandler = Callable[[str, dict[str, Any]], None]

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "local-voice-agent-xiaozhi-adapter", "version": "1"}


class McpError(RuntimeError):
    """A JSON-RPC error returned by the device."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.error_message = message


class DeviceMcpClient:
    """MCP client for the MCP server running on the ESP32.

    The device is the MCP server; this is the client. Requests travel in-band as
    `{"type":"mcp","payload":<JSON-RPC>}`, so this class owns only JSON-RPC
    framing and request/response correlation - never audio.

    Two firmware constraints shape it (see the research notes):
    `mcp_server.cc` requires `id` to be a JSON **number** and silently drops
    anything else, and it returns early for any method starting with
    `notifications`, so no `notifications/initialized` is sent.
    """

    def __init__(
        self,
        send: PayloadSender,
        *,
        timeout: float = 10.0,
        on_notification: NotificationHandler | None = None,
    ) -> None:
        self._send = send
        self._timeout = timeout
        self._on_notification = on_notification
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._closed = False
        self.server_info: dict[str, Any] = {}

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    async def initialize(self) -> dict[str, Any]:
        result = await self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        })
        server_info = result.get("serverInfo")
        self.server_info = server_info if isinstance(server_info, dict) else {}
        return result

    async def list_tools(self, *, max_pages: int = 20) -> list[dict[str, Any]]:
        """Fetch every tool page.

        The cursor is a tool name and the firmware omits `nextCursor` entirely on
        the last page, so absent or empty both mean "done". `withUserTools` is
        never sent: the device then defaults it to false and keeps privileged
        tools such as `self.reboot` out of the listing.
        """

        tools: list[dict[str, Any]] = []
        cursor = ""
        seen: set[str] = set()
        for _ in range(max_pages):
            params = {"cursor": cursor} if cursor else {}
            result = await self._request("tools/list", params)
            page = result.get("tools")
            if not isinstance(page, list):
                raise McpError(-32603, "tools/list result thiếu mảng tools.")
            tools.extend(item for item in page if isinstance(item, dict))
            cursor = result.get("nextCursor") or ""
            if not isinstance(cursor, str) or not cursor:
                return tools
            if cursor in seen:
                raise McpError(-32603, f"tools/list cursor lặp lại: {cursor}")
            seen.add(cursor)
        raise McpError(-32603, f"tools/list vượt quá {max_pages} trang.")

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._request("tools/call", {"name": name, "arguments": arguments})

    def handle_payload(self, payload: dict[str, Any]) -> None:
        """Route one inbound `mcp.payload`. Never raises."""

        method = payload.get("method")
        if isinstance(method, str):
            if self._on_notification is not None:
                params = payload.get("params")
                self._on_notification(method, params if isinstance(params, dict) else {})
            return
        request_id = payload.get("id")
        if not isinstance(request_id, int) or isinstance(request_id, bool):
            return
        future = self._pending.pop(request_id, None)
        if future is None or future.done():
            return
        if "error" in payload:
            error = payload["error"]
            code = error.get("code", -32603) if isinstance(error, dict) else -32603
            message = error.get("message", "") if isinstance(error, dict) else str(error)
            future.set_exception(McpError(int(code), str(message)))
            return
        result = payload.get("result")
        future.set_result(result if isinstance(result, dict) else {})

    async def close(self) -> None:
        """Fail every outstanding request so no caller waits on a dead socket."""

        self._closed = True
        pending = list(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(McpError(-32603, "Phiên MCP đã đóng."))
        if pending:
            # Let the waiters observe the failure before the session tears down.
            await asyncio.sleep(0)

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._closed:
            raise McpError(-32603, "Phiên MCP đã đóng.")
        request_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params:
            payload["params"] = params
        try:
            await self._send(payload)
            return await asyncio.wait_for(future, self._timeout)
        except TimeoutError as exc:
            raise McpError(-32603, f"{method} hết thời gian sau {self._timeout:.0f}s.") from exc
        finally:
            self._pending.pop(request_id, None)
