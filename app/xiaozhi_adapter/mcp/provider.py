from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.cancellation import TurnCancellation
from app.tools import ToolCall, ToolSpec
from app.xiaozhi_adapter.mcp.client import DeviceMcpClient, McpError
from app.xiaozhi_adapter.mcp.tool_mapping import DeviceToolCatalog, extract_tool_text


Logger = Callable[[str, str], None]


class DeviceToolProvider:
    """Exposes the ESP32's MCP tools to the voice agent as plain tools.

    This is the whole seam: the agent sees `app.tools.ToolProvider`, the device
    sees JSON-RPC. The `ToolCall.id` the LLM produced and the JSON-RPC request id
    are deliberately different identifiers - `DeviceMcpClient` owns the latter
    and correlates on it, while the former only ever appears in the message
    history handed back to the model.
    """

    def __init__(
        self,
        client: DeviceMcpClient,
        *,
        log: Logger | None = None,
    ) -> None:
        self._client = client
        self._catalog = DeviceToolCatalog()
        self._log = log or (lambda message, level: None)
        self.ready = False

    @property
    def catalog(self) -> DeviceToolCatalog:
        return self._catalog

    async def start(self) -> None:
        """Run the MCP handshake and load the tool list.

        A device that fails here is still a usable voice device, so failure is
        logged and leaves the provider empty rather than killing the session.
        """

        try:
            await self._client.initialize()
            tools = await self._client.list_tools()
        except McpError as exc:
            self._log(f"[MCP] khởi tạo thất bại: {exc}", "error")
            return
        skipped = self._catalog.replace(tools)
        if skipped:
            self._log(f"[MCP] bỏ qua tool không hợp lệ: {', '.join(skipped)}", "error")
        self.ready = True
        name = self._client.server_info.get("name", "?")
        version = self._client.server_info.get("version", "?")
        self._log(
            f"[MCP] {name} {version} cung cấp {len(self._catalog)} tool: "
            f"{', '.join(self._catalog.names)}",
            "info",
        )

    def specs(self) -> list[ToolSpec]:
        return self._catalog.specs() if self.ready else []

    async def call(self, call: ToolCall, cancellation: TurnCancellation) -> str:
        cancellation.raise_if_cancelled()
        device_name = self._catalog.device_name(call.name)
        result = await self._client.call_tool(device_name, _as_arguments(call.arguments))
        return extract_tool_text(result)


def _as_arguments(arguments: Any) -> dict[str, Any]:
    return dict(arguments) if isinstance(arguments, dict) else {}
