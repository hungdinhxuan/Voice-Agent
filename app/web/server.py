from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import AppConfig
from app.orchestrator import VoiceOrchestrator
from app.web.events import EventBroker
from app.web.model_catalog import build_model_catalog


STATIC_DIR = Path(__file__).with_name("static")


def create_web_app(config: AppConfig) -> FastAPI:
    broker = EventBroker()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        def publish(event: dict[str, Any]) -> None:
            if event.get("type") == "ready":
                event = {**event, "models": build_model_catalog(config)}
            broker.publish(event)

        orchestrator = VoiceOrchestrator(config, event_handler=publish)
        app.state.orchestrator = orchestrator
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
            }
        )
        sender = asyncio.create_task(_send_events(websocket, queue))
        receiver = asyncio.create_task(_receive_actions(websocket, app.state.orchestrator))
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

    return app


async def _send_events(
    websocket: WebSocket,
    queue: asyncio.Queue[dict[str, Any]],
) -> None:
    while True:
        await websocket.send_json(await queue.get())


async def _receive_actions(websocket: WebSocket, orchestrator: VoiceOrchestrator) -> None:
    try:
        while True:
            message = await websocket.receive_json()
            action = message.get("action")
            if action == "interrupt":
                await orchestrator.interrupt()
            elif action == "clear_history":
                orchestrator.clear_history()
    except WebSocketDisconnect:
        return


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
