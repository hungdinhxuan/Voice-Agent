from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import suppress
from typing import Any

import numpy as np

from app.config import AppConfig
from app.orchestrator import VoiceOrchestrator
from app.runtime import ModelRuntime
from app.web.audio import BrowserAudioOutput
from app.web.events import EventBroker
from app.web.model_catalog import build_model_catalog


class WebVoiceSession:
    """Owns one client's conversation state while sharing model weights."""

    def __init__(self, config: AppConfig, runtime: ModelRuntime) -> None:
        self.id = uuid.uuid4().hex
        self.config = config
        self.runtime = runtime
        self.broker = EventBroker()
        self.metrics: dict[str, dict[str, Any]] = {}
        self.audio_capabilities: dict[str, Any] = {}
        self.connected_at = time.time()
        self.last_active_at = self.connected_at
        self.speaker = BrowserAudioOutput(config.audio, self._publish)
        self.orchestrator = VoiceOrchestrator(
            config,
            event_handler=self._publish,
            speaker=self.speaker,
            use_local_microphone=False,
            runtime=runtime,
        )
        self.task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self.task is not None:
            return
        self.task = asyncio.create_task(self.orchestrator.run(), name=f"voice-session-{self.id}")
        self.task.add_done_callback(self._report_failure)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        return self.broker.subscribe()

    def push_audio(self, frame: np.ndarray) -> None:
        self.last_active_at = time.time()
        self.orchestrator.feed_audio(frame)

    async def handle_action(self, message: dict[str, Any]) -> None:
        self.last_active_at = time.time()
        action = message.get("action")
        if action == "interrupt":
            await self.orchestrator.interrupt()
        elif action == "clear_history":
            self.orchestrator.clear_history()
        elif action == "audio_started":
            self.speaker.mark_started(int(message["turn_id"]))
        elif action == "audio_drained":
            self.speaker.mark_drained(int(message["turn_id"]))
        elif action == "audio_capabilities":
            self.audio_capabilities = dict(message.get("capabilities") or {})
        elif action == "audio_gap":
            self._publish(
                {
                    "type": "metric",
                    "name": "audio_gap",
                    "value": int(message.get("missing", 1)),
                    "unit": "chunks",
                }
            )

    async def close(self) -> None:
        await self.orchestrator.disconnect_audio_client()
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        self.task = None

    def snapshot(self) -> dict[str, Any]:
        state = self.orchestrator.state.state.value
        return {
            "id": self.id,
            "state": state,
            "connected_at": self.connected_at,
            "last_active_at": self.last_active_at,
            "dropped_events": self.broker.dropped,
            "metrics": self.metrics,
            "audio_capabilities": self.audio_capabilities,
        }

    def _publish(self, event: dict[str, Any]) -> None:
        self.last_active_at = time.time()
        event = {"session_id": self.id, **event}
        if event.get("type") == "ready":
            event = {**event, "models": build_model_catalog(self.config)}
        if event.get("type") == "metric" and "name" in event:
            self.metrics[str(event["name"])] = {
                "value": event.get("value"),
                "unit": event.get("unit"),
                "time": time.time(),
            }
        self.broker.publish(event)

    def _report_failure(self, done: asyncio.Task[None]) -> None:
        if done.cancelled():
            return
        error = done.exception()
        if error is not None:
            self._publish({"type": "fatal", "message": f"{type(error).__name__}: {error}"})


class VoiceSessionRegistry:
    def __init__(self, config: AppConfig, runtime: ModelRuntime) -> None:
        self.config = config
        self.runtime = runtime
        self._sessions: dict[str, WebVoiceSession] = {}

    def open(self) -> WebVoiceSession:
        session = WebVoiceSession(self.config, self.runtime)
        self._sessions[session.id] = session
        session.start()
        return session

    async def close(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is not None:
            await session.close()

    async def close_all(self) -> None:
        sessions = list(self._sessions.values())
        self._sessions.clear()
        await asyncio.gather(*(session.close() for session in sessions), return_exceptions=True)

    def snapshots(self) -> list[dict[str, Any]]:
        return [session.snapshot() for session in self._sessions.values()]

    @property
    def count(self) -> int:
        return len(self._sessions)
