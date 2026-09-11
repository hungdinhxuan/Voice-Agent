from __future__ import annotations

import json
import re
from typing import Any

from app.tools import ToolSpec


_UNSAFE = re.compile(r"[^a-zA-Z0-9_-]")
_ALLOWED_PROPERTY_KEYS = frozenset(
    {"type", "description", "default", "minimum", "maximum", "maxLength", "enum"}
)


def sanitize_tool_name(name: str) -> str:
    """Make a device tool name safe for tool-calling APIs.

    Device tools are named like `self.audio_speaker.set_volume`; most function
    calling schemas reject dots. Upstream does the same thing.
    """

    return _UNSAFE.sub("_", name)


class DeviceToolCatalog:
    """The device's advertised tools, translated for our LLM layer.

    Holds the sanitized-name to device-name mapping. The LLM only ever sees
    sanitized names, and only this class knows the device names.
    """

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._device_names: dict[str, str] = {}

    def __len__(self) -> int:
        return len(self._specs)

    @property
    def names(self) -> list[str]:
        return list(self._specs)

    def replace(self, tools: list[dict[str, Any]]) -> list[str]:
        """Load a `tools/list` result. Returns the names that were skipped."""

        self._specs.clear()
        self._device_names.clear()
        skipped: list[str] = []
        for tool in tools:
            device_name = tool.get("name")
            if not isinstance(device_name, str) or not device_name:
                skipped.append(str(tool.get("name")))
                continue
            safe_name = sanitize_tool_name(device_name)
            if safe_name in self._specs:
                skipped.append(device_name)
                continue
            description = tool.get("description")
            self._specs[safe_name] = ToolSpec(
                name=safe_name,
                description=description if isinstance(description, str) else "",
                parameters=_normalize_schema(tool.get("inputSchema")),
            )
            self._device_names[safe_name] = device_name
        return skipped

    def specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def device_name(self, safe_name: str) -> str:
        try:
            return self._device_names[safe_name]
        except KeyError as exc:
            raise KeyError(f"Thiết bị không có tool {safe_name}.") from exc


def _normalize_schema(schema: Any) -> dict[str, Any]:
    """Keep the JSON Schema subset the device actually emits.

    `mcp_server.cc` only produces boolean/integer/string properties with
    `default`, `minimum`/`maximum` and `maxLength`, and omits `required`
    entirely when nothing is required.
    """

    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}, "required": []}
    raw_properties = schema.get("properties")
    properties: dict[str, Any] = {}
    if isinstance(raw_properties, dict):
        for name, definition in raw_properties.items():
            if isinstance(definition, dict):
                properties[str(name)] = {
                    key: value
                    for key, value in definition.items()
                    if key in _ALLOWED_PROPERTY_KEYS
                }
    raw_required = schema.get("required")
    required = (
        [item for item in raw_required if isinstance(item, str) and item in properties]
        if isinstance(raw_required, list)
        else []
    )
    return {
        "type": schema.get("type", "object"),
        "properties": properties,
        "required": required,
    }


def extract_tool_text(result: dict[str, Any]) -> str:
    """Flatten a `tools/call` result into the text the LLM gets back.

    The device replies `{"content":[{"type":"text","text":"..."}],"isError":…}`.
    A failure is reported as text rather than raised: the model should be able to
    tell the user that the device refused, not have the turn collapse.
    """

    content = result.get("content")
    parts: list[str] = []
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    text = "\n".join(parts) if parts else json.dumps(result, ensure_ascii=False)
    if result.get("isError") is True:
        return f"Tool error: {text}"
    return text
