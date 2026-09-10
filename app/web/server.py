from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import uvicorn
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import AppConfig
from app.orchestrator import VoiceOrchestrator
from app.web.audio import BrowserAudioInputRouter, BrowserAudioOutput
from app.web.events import EventBroker
from app.web.model_catalog import build_model_catalog


STATIC_DIR = Path(__file__).with_name("static")


def create_web_app(config: AppConfig) -> FastAPI:
    broker = EventBroker()
    audio_clients: set[WebSocket] = set()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        input_router: BrowserAudioInputRouter | None = None

        def publish(event: dict[str, Any]) -> None:
            if event.get("type") == "metric" and event.get("name") == "utterance":
                if input_router is not None:
                    input_router.release()
            if event.get("type") == "ready":
                event = {**event, "models": build_model_catalog(config)}
            broker.publish(event)

        browser_speaker = BrowserAudioOutput(config.audio, publish)
        orchestrator = VoiceOrchestrator(
            config,
            event_handler=publish,
            speaker=browser_speaker,
            use_local_microphone=False,
        )
        app.state.orchestrator = orchestrator
        app.state.browser_speaker = browser_speaker
        input_router = BrowserAudioInputRouter(orchestrator.feed_audio)
        app.state.input_router = input_router
        app.state.broker = broker
        task = asyncio.create_task(orchestrator.run(), name="voice-orchestrator")

        def report_failure(done: asyncio.Task[None]) -> None:
            if done.cancelled():
                return
            error = done.exception()
            if error is not None:
                broker.publish({"type": "fatal", "message": f"{type(error).__name__}: {error}"})

        task.add_done_callback(report_failure)
        app.state.orchestrator_task = task
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="Local Vietnamese Voice Agent", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        task: asyncio.Task[None] = app.state.orchestrator_task
        return {
            "ok": not task.done(),
            "state": app.state.orchestrator.state.state.value,
            "backend": config.llm.backend,
            "model": config.llm.model,
            "asr_backend": config.asr.backend,
            "asr_model": config.asr.model,
        }

    @app.get("/api/models")
    async def models() -> dict[str, Any]:
        return {"models": build_model_catalog(config)}

    @app.websocket("/ws")
    async def websocket_events(websocket: WebSocket) -> None:
        await websocket.accept()
        audio_clients.add(websocket)
        queue = broker.subscribe()
        await websocket.send_json(
            {
                "type": "hello",
                "backend": config.llm.backend,
                "model": config.llm.model,
                "asr_backend": config.asr.backend,
                "asr_model": config.asr.model,
                "models": build_model_catalog(config),
                "state": app.state.orchestrator.state.state.value,
                "audio_mode": "browser",
            }
        )
        sender = asyncio.create_task(_send_events(websocket, queue))
        receiver = asyncio.create_task(
            _receive_actions(
                websocket,
                app.state.orchestrator,
                app.state.browser_speaker,
                app.state.input_router,
                frame_samples=config.audio.block_size,
            )
        )
        try:
            done, pending = await asyncio.wait(
                {sender, receiver},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
        finally:
            broker.unsubscribe(queue)
            audio_clients.discard(websocket)
            app.state.input_router.disconnect(websocket)
            if not audio_clients:
                await app.state.orchestrator.disconnect_audio_client()

    return app


async def _send_events(
    websocket: WebSocket,
    queue: asyncio.Queue[dict[str, Any]],
) -> None:
    while True:
        await websocket.send_json(await queue.get())


async def _receive_actions(
    websocket: WebSocket,
    orchestrator: VoiceOrchestrator,
    browser_speaker: BrowserAudioOutput,
    input_router: BrowserAudioInputRouter,
    *,
    frame_samples: int,
) -> None:
    try:
        while True:
            packet = await websocket.receive()
            if packet["type"] == "websocket.disconnect":
                return
            payload = packet.get("bytes")
            if payload is not None:
                input_router.feed(websocket, decode_browser_audio(payload, frame_samples))
                continue
            raw_message = packet.get("text")
            if raw_message is None:
                continue
            message = json.loads(raw_message)
            action = message.get("action")
            if action == "interrupt":
                await orchestrator.interrupt()
            elif action == "clear_history":
                orchestrator.clear_history()
            elif action == "audio_started":
                browser_speaker.mark_started(int(message["turn_id"]))
            elif action == "audio_drained":
                browser_speaker.mark_drained(int(message["turn_id"]))
    except WebSocketDisconnect:
        return


def decode_browser_audio(payload: bytes, frame_samples: int) -> np.ndarray:
    expected_bytes = frame_samples * np.dtype("<f4").itemsize
    if len(payload) != expected_bytes:
        raise ValueError(f"Browser audio cần {expected_bytes} bytes, nhận {len(payload)}.")
    frame = np.frombuffer(payload, dtype="<f4").copy()
    if not np.isfinite(frame).all():
        raise ValueError("Browser audio chứa sample không hữu hạn.")
    return frame


async def serve_web(config: AppConfig) -> None:
    app = create_web_app(config)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=config.web.host,
            port=config.web.port,
            log_level="info",
        )
    )
    await server.serve()
