from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.cancellation import TurnCancellation
from app.config import LLMConfig
from app.llm.base import LLMService
from app.tools import LLMDelta, ToolCall, ToolSpec


class OllamaLLMService(LLMService):
    """Streaming Qwen3.5 client for a fully local Ollama server."""

    def __init__(self, config: LLMConfig, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(base_url=config.host, timeout=None)

    async def load(self) -> None:
        try:
            response = await self._client.post(
                "/api/generate",
                json={
                    "model": self.config.model,
                    "prompt": "",
                    "stream": False,
                    "keep_alive": self.config.keep_alive,
                },
            )
            response.raise_for_status()
        except httpx.ConnectError as exc:
            raise RuntimeError(
                f"Không kết nối được Ollama tại {self.config.host}. Hãy chạy 'ollama serve'."
            ) from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise RuntimeError(
                    f"Ollama chưa có model {self.config.model}. Hãy chạy "
                    f"'ollama pull {self.config.model}'."
                ) from exc
            raise RuntimeError(f"Ollama load model thất bại: {exc.response.text}") from exc

    @property
    def supports_tools(self) -> bool:
        return True

    async def generate_stream(
        self,
        messages: list[dict[str, str]],
        cancellation: TurnCancellation,
    ) -> AsyncIterator[str]:
        async for part in self._stream(self._payload(messages), cancellation):
            token = part.get("message", {}).get("content", "")
            if token:
                yield token

    async def generate_turn(
        self,
        messages: list[dict],
        cancellation: TurnCancellation,
        tools: list[ToolSpec] | None = None,
    ) -> AsyncIterator[LLMDelta]:
        payload = self._payload(messages, tools)
        index = 0
        async for part in self._stream(payload, cancellation):
            message = part.get("message", {})
            token = message.get("content", "")
            if token:
                yield LLMDelta(text=token)
            calls, index = _parse_tool_calls(message.get("tool_calls"), index)
            if calls:
                yield LLMDelta(tool_calls=calls)

    def _payload(
        self,
        messages: list[dict],
        tools: list[ToolSpec] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
            "think": self.config.enable_thinking,
            "keep_alive": self.config.keep_alive,
            "options": {
                "temperature": self.config.temperature,
                "top_p": self.config.top_p,
                "top_k": self.config.top_k,
                "num_predict": self.config.max_tokens,
                "num_ctx": self.config.context_length,
            },
        }
        if tools:
            payload["tools"] = [tool.to_openai() for tool in tools]
        return payload

    async def _stream(
        self,
        payload: dict[str, Any],
        cancellation: TurnCancellation,
    ) -> AsyncIterator[dict[str, Any]]:
        request = self._client.build_request("POST", "/api/chat", json=payload)
        response: httpx.Response | None = None
        try:
            response = await self._client.send(request, stream=True)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                await response.aread()
                raise RuntimeError(f"Ollama generation thất bại: {response.text}") from exc
            async for line in response.aiter_lines():
                cancellation.raise_if_cancelled()
                if not line:
                    continue
                part = json.loads(line)
                if error := part.get("error"):
                    raise RuntimeError(f"Ollama generation error: {error}")
                yield part
                if part.get("done"):
                    break
        except httpx.ConnectError as exc:
            raise RuntimeError(f"Mất kết nối Ollama tại {self.config.host}.") from exc
        finally:
            if response is not None:
                await response.aclose()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _parse_tool_calls(raw: Any, index: int) -> tuple[tuple[ToolCall, ...], int]:
    """Ollama returns arguments as an object and supplies no call id, so mint one."""

    if not isinstance(raw, list):
        return (), index
    calls: list[ToolCall] = []
    for item in raw:
        function = item.get("function") if isinstance(item, dict) else None
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        call_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(call_id, str) or not call_id:
            call_id = f"call_{index}"
        index += 1
        calls.append(ToolCall(id=call_id, name=name, arguments=arguments))
    return tuple(calls), index
