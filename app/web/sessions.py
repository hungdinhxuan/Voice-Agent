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
        self.base_config = config
        self.runtime = runtime
        self.language = runtime.default_language
        self.config = config.for_language(self.language)
        self.broker = EventBroker()
        self.metrics: dict[str, dict[str, Any]] = {}
        self.audio_capabilities: dict[str, Any] = {}
        self.connected_at = time.time()
        self.last_active_at = self.connected_at
        self.switching_language = False
        self._language_lock = asyncio.Lock()
        self.task: asyncio.Task[None] | None = None
        self._create_orchestrator()

    def _create_orchestrator(self) -> None:
        self.speaker = BrowserAudioOutput(self.config.audio, self._publish)
        self.orchestrator = VoiceOrchestrator(
            self.config,
            event_handler=self._publish,
            speaker=self.speaker,
            use_local_microphone=False,
            runtime=self.runtime,
            language=self.language,
        )

    def start(self) -> None:
        if self.task is not None:
            return
        self.task = asyncio.create_task(self.orchestrator.run(), name=f"voice-session-{self.id}")
        self.task.add_done_callback(self._report_failure)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        return self.broker.subscribe()

    def push_audio(self, frame: np.ndarray) -> None:
        self.last_active_at = time.time()
        if not self.switching_language:
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
        elif action == "set_language":
            await self.switch_language(str(message.get("language", "")))

    def report_protocol_error(self, message: str) -> None:
        """Một frame hỏng chỉ được bỏ qua, không được đóng session."""

        self._publish({"type": "protocol_error", "message": message})

    async def switch_language(self, language: str) -> None:
        code = language.casefold()
        if code == self.language:
            self._publish(
                {
                    "type": "language_changed",
                    "language": code,
                    "models": build_model_catalog(self.config),
                }
            )
            return
        async with self._language_lock:
            if code not in {"vi", "en"}:
                self._publish(
                    {
                        "type": "language_error",
                        "language": code,
                        "message": f"Ngôn ngữ không được hỗ trợ: {language}",
                    }
                )
                return
            self.switching_language = True
            await self.orchestrator.disconnect_audio_client()
            self._publish({"type": "language_loading", "language": code})
            try:
                await self.runtime.load_language(code)
            except Exception as exc:
                self._publish(
                    {
                        "type": "language_error",
                        "language": code,
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                )
                self.switching_language = False
                return

            await self._stop_orchestrator()
            self.language = code
            self.config = self.base_config.for_language(code)
            self._create_orchestrator()
            self._publish(
                {
                    "type": "language_changed",
                    "language": code,
                    "models": build_model_catalog(self.config),
                }
            )
            self._publish({"type": "history_cleared"})
            self.switching_language = False
            self.start()

    async def close(self) -> None:
        await self._stop_orchestrator()

    async def _stop_orchestrator(self) -> None:
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
            "language": self.language,
            "switching_language": self.switching_language,
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
            event = {
                **event,
                "language": self.language,
                "models": build_model_catalog(self.config),
            }
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
