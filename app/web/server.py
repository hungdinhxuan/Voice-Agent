from __future__ import annotations

import asyncio
import json
import logging
import re
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

from app.config import AppConfig, WebConfig
from app.runtime import ModelRuntime
from app.web.model_catalog import build_language_catalogs, build_model_catalog
from app.web.sessions import VoiceSessionRegistry, WebVoiceSession
from app.xiaozhi_adapter.server import DeviceSessionRegistry, register_xiaozhi_endpoint


STATIC_DIR = Path(__file__).with_name("static")
_AUDIO_HEADER = struct.Struct("<4sIIId")
_TOKEN_QUERY = re.compile(r"(token=)[^&\s\"']+")


def create_web_app(config: AppConfig) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime = ModelRuntime(config)
        await runtime.load()
        if config.xiaozhi.enabled:
            await runtime.load_language(config.xiaozhi.language)
        registry = VoiceSessionRegistry(config, runtime)
        devices = DeviceSessionRegistry(config, runtime)
        app.state.runtime = runtime
        app.state.sessions = registry
        app.state.xiaozhi_sessions = devices
        try:
            yield
        finally:
            await devices.close_all()
            await registry.close_all()
            await runtime.close()

    app = FastAPI(title="Local Bilingual Voice Agent", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    register_xiaozhi_endpoint(app, config)

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
        default_config = config.for_language(config.web.default_language)
        return {
            "ok": app.state.runtime.loaded,
            "state": sessions[0]["state"] if len(sessions) == 1 else "IDLE",
            "session_count": len(sessions),
            "backend": config.llm.backend,
            "model": config.llm.model,
            "asr_backend": default_config.asr.backend,
            "asr_model": default_config.asr.model,
            "default_language": config.web.default_language,
            "supported_languages": ["vi", "en"],
        }

    @app.get("/api/models")
    async def models() -> dict[str, Any]:
        return {
            "default_language": config.web.default_language,
            "models": build_model_catalog(config.for_language(config.web.default_language)),
            "languages": build_language_catalogs(config),
        }

    @app.get("/api/sessions")
    async def sessions() -> dict[str, Any]:
        return {"sessions": app.state.sessions.snapshots()}

    @app.get("/api/runtime")
    async def runtime() -> dict[str, Any]:
        return {"runtime": app.state.runtime.snapshot()}

    @app.get("/api/xiaozhi")
    async def xiaozhi() -> dict[str, Any]:
        return {
            "enabled": config.xiaozhi.enabled,
            "path": config.xiaozhi.path,
            "language": config.xiaozhi.language,
            "protocol_version": config.xiaozhi.protocol_version,
            "devices": app.state.xiaozhi_sessions.snapshots(),
        }

    @app.websocket("/ws")
    async def websocket_events(websocket: WebSocket) -> None:
        if not _origin_allowed(websocket.headers.get("origin"), config) or not _authorized(
            config.web.access_token,
            websocket.headers.get("authorization"),
            websocket.query_params.get("token"),
        ):
            await websocket.close(code=1008)
            return
        if app.state.sessions.count >= config.web.max_sessions:
            await websocket.close(code=1013)
            return
        await websocket.accept()
        session = app.state.sessions.open()
        queue = session.subscribe()
        await websocket.send_json(
            {
                "type": "hello",
                "backend": config.llm.backend,
                "model": config.llm.model,
                "asr_backend": session.config.asr.backend,
                "asr_model": session.config.asr.model,
                "models": build_model_catalog(session.config),
                "state": session.orchestrator.state.state.value,
                "session_id": session.id,
                "language": session.language,
                "supported_languages": ["vi", "en"],
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
            try:
                payload = packet.get("bytes")
                if payload is not None:
                    session.push_audio(decode_browser_audio(payload, frame_samples))
                    continue
                raw_message = packet.get("text")
                if raw_message is None:
                    continue
                await session.handle_action(json.loads(raw_message))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                session.report_protocol_error(f"{type(exc).__name__}: {exc}")
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
    logging.getLogger("uvicorn.access").addFilter(RedactTokenFilter())
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
    if origin is None:
        return True
    if config.web.allowed_origins:
        return origin in config.web.allowed_origins
    if not config.web.require_same_origin:
        return True
    return origin in _loopback_origins(config.web)


def _loopback_origins(web: WebConfig) -> set[str]:
    """WebSocket bỏ qua CORS, nên trang web lạ không được phép mở session cục bộ."""

    scheme = "https" if web.tls_certfile else "http"
    hosts = ("127.0.0.1", "localhost", "[::1]")
    origins = {f"{scheme}://{host}:{web.port}" for host in hosts}
    if web.port == (443 if scheme == "https" else 80):
        origins |= {f"{scheme}://{host}" for host in hosts}
    return origins


class RedactTokenFilter(logging.Filter):
    """Access log không được giữ access token trong query string."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _TOKEN_QUERY.sub(r"\1***", item) if isinstance(item, str) else item
                for item in record.args
            )
        return True
