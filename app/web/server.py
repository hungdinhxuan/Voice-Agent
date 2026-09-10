from __future__ import annotations

import asyncio
import json
import secrets
import struct
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
import numpy as np
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import AppConfig
from app.runtime import ModelRuntime
from app.web.model_catalog import build_model_catalog
from app.web.sessions import VoiceSessionRegistry, WebVoiceSession


STATIC_DIR = Path(__file__).with_name("static")
_AUDIO_HEADER = struct.Struct("<4sIIId")


def create_web_app(config: AppConfig) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime = ModelRuntime(config)
        await runtime.load()
        registry = VoiceSessionRegistry(config, runtime)
        app.state.runtime = runtime
        app.state.sessions = registry
        try:
            yield
        finally:
            await registry.close_all()
            await runtime.close()

    app = FastAPI(title="Local Vietnamese Voice Agent", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def protect_runtime_api(request: Request, call_next):
        if request.url.path.startswith("/api/") or request.url.path == "/health":
            if not _origin_allowed(request.headers.get("origin"), config):
                return JSONResponse({"detail": "Origin không được phép."}, status_code=403)
            if not _authorized(
                config.web.access_token,
                request.headers.get("authorization"),
                request.query_params.get("token"),
            ):
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        sessions = app.state.sessions.snapshots()
        return {
            "ok": app.state.runtime.loaded,
            "state": sessions[0]["state"] if len(sessions) == 1 else "IDLE",
            "session_count": len(sessions),
            "backend": config.llm.backend,
            "model": config.llm.model,
            "asr_backend": config.asr.backend,
            "asr_model": config.asr.model,
        }

    @app.get("/api/models")
    async def models() -> dict[str, Any]:
        return {"models": build_model_catalog(config)}

    @app.get("/api/sessions")
    async def sessions() -> dict[str, Any]:
        return {"sessions": app.state.sessions.snapshots()}

    @app.get("/api/runtime")
    async def runtime() -> dict[str, Any]:
        return {"runtime": app.state.runtime.snapshot()}

    @app.websocket("/ws")
    async def websocket_events(websocket: WebSocket) -> None:
        if not _origin_allowed(websocket.headers.get("origin"), config) or not _authorized(
            config.web.access_token,
            websocket.headers.get("authorization"),
            websocket.query_params.get("token"),
        ):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        session = app.state.sessions.open()
        queue = session.subscribe()
        await websocket.send_json(
            {
                "type": "hello",
                "backend": config.llm.backend,
                "model": config.llm.model,
                "asr_backend": config.asr.backend,
                "asr_model": config.asr.model,
                "models": build_model_catalog(config),
                "state": session.orchestrator.state.state.value,
                "session_id": session.id,
                "audio_mode": "browser",
                "audio_transport": "binary-pcm-v1",
                "playback_prebuffer_ms": config.web.playback_prebuffer_ms,
            }
        )
        sender = asyncio.create_task(_send_events(websocket, queue))
        receiver = asyncio.create_task(
            _receive_actions(
                websocket,
                session,
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
            session.broker.unsubscribe(queue)
            await app.state.sessions.close(session.id)

    return app


async def _send_events(
    websocket: WebSocket,
    queue: asyncio.Queue[dict[str, Any]],
) -> None:
    while True:
        event = await queue.get()
        if event.get("type") == "audio_chunk":
            await websocket.send_bytes(encode_browser_audio(event))
        else:
            await websocket.send_json(event)


async def _receive_actions(
    websocket: WebSocket,
    session: WebVoiceSession,
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
                session.push_audio(decode_browser_audio(payload, frame_samples))
                continue
            raw_message = packet.get("text")
            if raw_message is None:
                continue
            message = json.loads(raw_message)
            await session.handle_action(message)
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


def encode_browser_audio(event: dict[str, Any]) -> bytes:
    pcm = event.get("pcm")
    if not isinstance(pcm, bytes):
        raise TypeError("audio_chunk.pcm phải là bytes.")
    header = _AUDIO_HEADER.pack(
        b"VAO1",
        int(event["turn_id"]),
        int(event["sample_rate"]),
        int(event["sequence"]),
        float(event.get("time", 0.0)),
    )
    return header + pcm


async def serve_web(config: AppConfig) -> None:
    app = create_web_app(config)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=config.web.host,
            port=config.web.port,
            log_level="info",
            ssl_certfile=config.web.tls_certfile,
            ssl_keyfile=config.web.tls_keyfile,
        )
    )
    await server.serve()


def _authorized(expected: str | None, authorization: str | None, query_token: str | None) -> bool:
    if expected is None:
        return True
    bearer = ""
    if authorization and authorization.startswith("Bearer "):
        bearer = authorization[7:]
    supplied = bearer or query_token or ""
    return secrets.compare_digest(supplied, expected)


def _origin_allowed(origin: str | None, config: AppConfig) -> bool:
    allowed = config.web.allowed_origins
    return origin is None or not allowed or origin in allowed
