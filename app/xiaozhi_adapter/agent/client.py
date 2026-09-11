from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

import numpy as np

from app.audio.output import AudioOutput
from app.config import AppConfig
from app.orchestrator import VoiceOrchestrator
from app.runtime import ModelRuntime
from app.tools import ToolProvider
from app.xiaozhi_adapter.agent import events as agent_events
from app.xiaozhi_adapter.agent.events import AgentEvent


class AgentSession:
    """Long-lived streaming session with the local voice agent.

    The generic half of the boundary: audio in, typed events out, cancel, close.
    Nothing here knows about WebSockets, Opus or MCP. The Xiaozhi side never
    touches ASR, LLM or TTS classes - only this.

    The agent already exposes an in-process async interface, so there is no
    request-per-chunk anywhere: `push_audio` hands frames to the orchestrator's
    bounded queue and TTS audio is pushed to the injected `AudioOutput`.
    """

    def __init__(
        self,
        config: AppConfig,
        runtime: ModelRuntime,
        speaker: AudioOutput,
        *,
        language: str,
        tools: ToolProvider | None = None,
        max_tool_rounds: int = 3,
        system_prompt_suffix: str = "",
        event_queue_size: int = 256,
    ) -> None:
        self.config = config.for_language(language)
        if system_prompt_suffix:
            self.config = replace(
                self.config,
                conversation=replace(
                    self.config.conversation,
                    system_prompt=(
                        f"{self.config.conversation.system_prompt.rstrip()}\n"
                        f"{system_prompt_suffix}"
                    ),
                ),
            )
        self.language = language
        self._events: asyncio.Queue[AgentEvent] = asyncio.Queue(maxsize=event_queue_size)
        self._dropped_events = 0
        self._task: asyncio.Task[None] | None = None
        self._orchestrator = VoiceOrchestrator(
            self.config,
            event_handler=self._publish,
            speaker=speaker,
            use_local_microphone=False,
            runtime=runtime,
            language=language,
            tools=tools,
            max_tool_rounds=max_tool_rounds,
        )

    @property
    def input_sample_rate(self) -> int:
        return self.config.audio.sample_rate

    @property
    def input_block_size(self) -> int:
        return self.config.audio.block_size

    @property
    def output_sample_rate(self) -> int:
        return self.config.audio.output_sample_rate

    @property
    def dropped_events(self) -> int:
        return self._dropped_events

    @property
    def state(self) -> str:
        return self._orchestrator.state.state.value

    @property
    def turn_id(self) -> int:
        return self._orchestrator.turn_id

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._orchestrator.run(), name="xiaozhi-agent")
        self._task.add_done_callback(self._report_failure)

    def push_audio(self, frame: np.ndarray) -> None:
        self._orchestrator.feed_audio(frame)

    async def cancel(self, reason: str) -> None:
        await self._orchestrator.interrupt(source=reason)

    async def end_utterance(self) -> bool:
        return await self._orchestrator.end_utterance()

    async def events(self) -> AsyncIterator[AgentEvent]:
        while True:
            yield await self._events.get()

    async def close(self) -> None:
        await self._orchestrator.disconnect_audio_client()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def _publish(self, event: dict[str, Any]) -> None:
        """Called from the orchestrator's synchronous event hook.

        Drops the oldest event when the consumer falls behind: a stalled
        transport must not be able to grow this without bound, and losing a log
        line is better than losing audio pacing.
        """

        kind = str(event.get("type", ""))
        payload = {name: value for name, value in event.items() if name != "type"}
        if self._events.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._events.get_nowait()
            self._dropped_events += 1
        self._events.put_nowait(AgentEvent(kind=kind, payload=payload))

    def _report_failure(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self._publish(
                {
                    "type": agent_events.FATAL,
                    "message": f"{type(error).__name__}: {error}",
                }
            )
