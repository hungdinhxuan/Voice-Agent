from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import AsyncIterator
from typing import Any

from app.cancellation import TurnCancellation
from app.config import LLMConfig
from app.llm.base import LLMService


_END = object()


class Qwen35Service(LLMService):
    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.processor = None
        self.model = None
        self._generation_lock = threading.Lock()

    async def load(self) -> None:
        await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> None:
        import torch
        from transformers import AutoModelForMultimodalLM, AutoProcessor

        try:
            dtype = getattr(torch, self.config.dtype)
        except AttributeError as exc:
            raise ValueError(f"Torch dtype không hợp lệ: {self.config.dtype}") from exc
        self.processor = AutoProcessor.from_pretrained(self.config.hf_model)
        self.model = AutoModelForMultimodalLM.from_pretrained(
            self.config.hf_model,
            dtype=dtype,
            device_map={"": self.config.device},
            low_cpu_mem_usage=True,
        )
        self.model.eval()

    async def generate_stream(
        self,
        messages: list[dict[str, str]],
        cancellation: TurnCancellation,
    ) -> AsyncIterator[str]:
        import torch
        from transformers import StoppingCriteria, StoppingCriteriaList, TextIteratorStreamer

        if self.model is None or self.processor is None:
            raise RuntimeError("LLM chưa được load.")

        class StopOnCancel(StoppingCriteria):
            def __call__(self, *args: Any, **kwargs: Any) -> bool:
                del args, kwargs
                return cancellation.thread_event.is_set()

        inputs = self.processor.apply_chat_template(
            messages,
            add_generation_prompt=True,
            enable_thinking=self.config.enable_thinking,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        ).to(self.model.device)
        streamer = TextIteratorStreamer(
            self.processor.tokenizer,
            skip_prompt=True,
            skip_special_tokens=True,
            timeout=0.2,
        )
        generation_error: list[BaseException] = []
        kwargs = dict(
            **inputs,
            streamer=streamer,
            max_new_tokens=self.config.max_tokens,
            do_sample=self.config.do_sample,
            stopping_criteria=StoppingCriteriaList([StopOnCancel()]),
        )
        if self.config.do_sample:
            kwargs.update(
                temperature=self.config.temperature,
                top_p=self.config.top_p,
                top_k=self.config.top_k,
            )

        def generate() -> None:
            try:
                with self._generation_lock, torch.inference_mode():
                    if cancellation.thread_event.is_set():
                        streamer.on_finalized_text("", stream_end=True)
                        return
                    self.model.generate(**kwargs)
            except BaseException as exc:
                generation_error.append(exc)
                streamer.on_finalized_text("", stream_end=True)

        thread = threading.Thread(target=generate, name="qwen-generation", daemon=True)
        thread.start()
        try:
            while True:
                cancellation.raise_if_cancelled()
                item = await asyncio.to_thread(_next_streamer_item, streamer)
                if item is _END:
                    break
                if item:
                    yield item
            if generation_error:
                raise generation_error[0]
        finally:
            if thread.is_alive():
                cancellation.thread_event.set()
            await asyncio.to_thread(thread.join, 2.0)

    async def close(self) -> None:
        self.processor = None
        self.model = None


def _next_streamer_item(streamer: Any) -> object:
    try:
        return next(streamer)
    except queue.Empty:
        return ""
    except StopIteration:
        return _END
