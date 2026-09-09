from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.cancellation import TurnCancellation
from app.config import LLMConfig
from app.llm.base import LLMService


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

    async def generate_stream(
        self,
        messages: list[dict[str, str]],
        cancellation: TurnCancellation,
    ) -> AsyncIterator[str]:
        payload = {
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
                token = part.get("message", {}).get("content", "")
                if token:
                    yield token
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
