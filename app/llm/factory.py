from __future__ import annotations

from app.config import LLMConfig
from app.llm.base import LLMService
from app.llm.ollama import OllamaLLMService
from app.llm.qwen35 import Qwen35Service


def create_llm_service(config: LLMConfig) -> LLMService:
    if config.backend == "ollama":
        return OllamaLLMService(config)
    if config.backend == "transformers":
        return Qwen35Service(config)
    raise ValueError(f"LLM backend không hỗ trợ: {config.backend}")
