import json

import httpx

from app.cancellation import TurnCancellation
from app.config import LLMConfig
from app.llm.factory import create_llm_service
from app.llm.ollama import OllamaLLMService


async def test_ollama_preload_and_stream() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={"done": True})
        content = "\n".join(
            [
                json.dumps({"message": {"content": "Xin "}, "done": False}),
                json.dumps({"message": {"content": "chào."}, "done": True}),
            ]
        )
        return httpx.Response(200, text=content)

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:11434",
        transport=httpx.MockTransport(handler),
    )
    config = LLMConfig()
    service = OllamaLLMService(config, client=client)
    await service.load()
    tokens = [
        token
        async for token in service.generate_stream(
            [{"role": "user", "content": "Chào"}],
            TurnCancellation(),
        )
    ]
    await client.aclose()

    assert tokens == ["Xin ", "chào."]
    preload = json.loads(requests[0].content)
    generation = json.loads(requests[1].content)
    assert preload["keep_alive"] == -1
    assert generation["think"] is False
    assert generation["options"]["num_ctx"] == 4096


async def test_factory_uses_configured_backend() -> None:
    service = create_llm_service(LLMConfig())
    assert isinstance(service, OllamaLLMService)
    await service.close()
