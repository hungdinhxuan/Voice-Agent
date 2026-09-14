from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import replace
from typing import Any, Protocol

from app.config import AppConfig
from app.runtime import ModelRuntime
from app.state import ConversationState
from app.xiaozhi_adapter.agent import events as agent_events
from app.xiaozhi_adapter.agent.client import AgentSession
from app.xiaozhi_adapter.agent.events import AgentEvent
from app.xiaozhi_adapter.audio.pacing import PacedFrameSender
from app.xiaozhi_adapter.audio.pipeline import DownlinkPipeline, UplinkPipeline
from app.xiaozhi_adapter.logging import SessionLogger
from app.xiaozhi_adapter.mcp.client import DeviceMcpClient
from app.xiaozhi_adapter.mcp.provider import DeviceToolProvider
from app.xiaozhi_adapter.protocol import messages as protocol
from app.xiaozhi_adapter.protocol.binary import decode_uplink_audio, encode_downlink_audio
from app.xiaozhi_adapter.protocol.messages import (
    AbortMessage,
    AudioParams,
    ClientHello,
    ListenMessage,
    McpMessage,
    ProtocolError,
    UnknownMessage,
)
from app.xiaozhi_adapter.protocol.state import DeviceState, DeviceStateTracker
from app.xiaozhi_adapter.session.audio_sink import XiaozhiAudioSink


# Without this the model narrates the action instead of performing it: with the
# stock conversational system prompt, qwen3.5:4b answers "tôi đang tăng âm lượng
# lên 70%" and never emits a tool call. Adding one sentence makes it call the
# tool reliably, in both languages and for both polite and imperative phrasing.
DEVICE_TOOL_PROMPT = {
    "vi": (
        "Bạn điều khiển một thiết bị thật. Khi người dùng yêu cầu thao tác trên "
        "thiết bị, hãy gọi tool tương ứng trước, đừng chỉ nói là đã làm."
    ),
    "en": (
        "You control a real device. When the user asks for a device action, call "
        "the matching tool first instead of only saying that you did it."
    ),
}


class DeviceTransport(Protocol):
    """The two operations the adapter needs from a WebSocket."""

    async def send_json(self, message: dict[str, Any]) -> None: ...

    async def send_bytes(self, payload: bytes) -> None: ...


class DeviceSession:
    """One ESP32 connection.

    Everything mutable lives here, so two devices sharing the process share only
    the model weights in `ModelRuntime`. Nothing is global.

    Task ownership: the agent task, the TTS pacing task, the agent event pump and
    the MCP handshake are all created here and all cancelled in `close()`, so no
    task can outlive the socket.
    """

    def __init__(
        self,
        config: AppConfig,
        runtime: ModelRuntime,
        transport: DeviceTransport,
        *,
        device_id: str,
        client_id: str,
    ) -> None:
        self.config = config
        self.settings = config.xiaozhi
        self.runtime = runtime
        self.transport = transport
        self.device_id = device_id
        self.client_id = client_id
        self.session_id = uuid.uuid4().hex
        self.connected_at = time.time()
        self.hello: ClientHello | None = None
        self.device = DeviceStateTracker()
        self.log = SessionLogger(device_id=device_id, session_id=self.session_id)
        self.audio_params = AudioParams(
            sample_rate=self.settings.downlink_sample_rate,
            frame_duration_ms=self.settings.frame_duration_ms,
        )
        self.protocol_errors = 0
        self.uplink_frames_dropped = 0
        self._agent: AgentSession | None = None
        self._sink: XiaozhiAudioSink | None = None
        self._uplink: UplinkPipeline | None = None
        self._pacer: PacedFrameSender | None = None
        self._mcp: DeviceMcpClient | None = None
        self._tools: DeviceToolProvider | None = None
        self._tasks: list[asyncio.Task[Any]] = []
        self._turn_started_at: float | None = None
        self._speech_end_at: float | None = None
        self._closed = False

    # ---------------------------------------------------------------- lifecycle

    async def open(self, hello: ClientHello) -> None:
        """Run the handshake and bring up every per-session task."""

        if hello.version != self.settings.protocol_version:
            raise ProtocolError(
                f"Protocol-Version {hello.version} chưa được hỗ trợ, cần "
                f"{self.settings.protocol_version}."
            )
        self.hello = hello
        self.log.event(
            "hello",
            version=hello.version,
            uplink_sample_rate=hello.audio.sample_rate,
            uplink_frame_ms=hello.audio.frame_duration_ms,
            mcp=hello.supports_mcp,
            aec=hello.server_side_aec,
        )

        await self.runtime.load_language(self.settings.language)

        if hello.supports_mcp and self.settings.mcp_enabled:
            self._mcp = DeviceMcpClient(
                self._send_mcp_payload,
                timeout=self.settings.mcp_timeout_seconds,
                on_notification=self._on_mcp_notification,
            )
            self._tools = DeviceToolProvider(self._mcp, log=self.log.message)

        self._uplink = UplinkPipeline(
            agent_sample_rate=self.config.audio.sample_rate,
            agent_block_size=self.config.audio.block_size,
            # hello.audio is what the device sends up; self.audio_params is what
            # the server sends down. They are different directions and, for a PCM
            # device, different formats.
            source_format=hello.audio.format,
            device_sample_rate=hello.audio.sample_rate,
        )
        # A device without an Opus encoder is assumed to have no decoder either,
        # so `pcm` in hello switches both directions. The server hello below then
        # tells it what it is about to receive.
        self.audio_params = replace(self.audio_params, format=hello.audio.format)
        language_config = self.config.for_language(self.settings.language)
        downlink = DownlinkPipeline(
            agent_sample_rate=language_config.audio.output_sample_rate,
            device_sample_rate=self.audio_params.sample_rate,
            frame_duration_ms=self.audio_params.frame_duration_ms,
            bitrate=self.settings.opus_bitrate,
            target_format=self.audio_params.format,
        )
        self._pacer = PacedFrameSender(
            self.audio_params.frame_duration_ms,
            self._send_audio_frame,
            prebuffer_frames=self.settings.prebuffer_frames,
            max_queued_frames=self.settings.max_queued_frames,
        )
        self._sink = XiaozhiAudioSink(
            downlink,
            self._pacer,
            can_send=lambda: self.device.accepts_audio,
            log=self.log.message,
        )
        self._agent = AgentSession(
            self.config,
            self.runtime,
            self._sink,
            language=self.settings.language,
            tools=self._tools,
            max_tool_rounds=self.settings.max_tool_rounds,
            system_prompt_suffix=(
                DEVICE_TOOL_PROMPT[self.settings.language] if self._tools is not None else ""
            ),
        )
        agent = self._agent
        self.log.bind_turn(lambda: agent.turn_id)
        await self._sink.start()
        await self._agent.start()

        await self.transport.send_json(
            protocol.build_server_hello(self.session_id, self.audio_params)
        )
        self.device.on_handshake_complete()
        self.log.event(
            "ready",
            downlink_sample_rate=self.audio_params.sample_rate,
            frame_ms=self.audio_params.frame_duration_ms,
            language=self.settings.language,
            elapsed_ms=round((time.time() - self.connected_at) * 1000),
        )

        self._spawn(self._pump_agent_events(), "xiaozhi-events")
        if self._tools is not None:
            self._spawn(self._tools.start(), "xiaozhi-mcp-init")

    async def close(self) -> None:
        """Tear the session down in dependency order and leave nothing running."""

        if self._closed:
            return
        self._closed = True
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        if self._mcp is not None:
            await self._mcp.close()
        if self._agent is not None:
            await self._agent.close()
        if self._sink is not None:
            await self._sink.close()
        if self._uplink is not None:
            self._uplink.close()
        self.log.event(
            "closed",
            duration_s=round(time.time() - self.connected_at, 1),
            protocol_errors=self.protocol_errors,
            uplink_frames_dropped=self.uplink_frames_dropped,
        )

    # ------------------------------------------------------------------ inbound

    async def handle_text(self, raw: str) -> None:
        """Dispatch one text frame. A bad frame is logged, never fatal."""

        try:
            message = protocol.parse_client_message(raw)
        except ProtocolError as exc:
            self.protocol_errors += 1
            self.log.message(f"[PROTOCOL] bỏ qua text frame: {exc}", "error")
            return
        if isinstance(message, ListenMessage):
            await self._on_listen(message)
        elif isinstance(message, AbortMessage):
            await self._on_abort(message)
        elif isinstance(message, McpMessage):
            self._on_mcp(message)
        elif isinstance(message, ClientHello):
            self.log.message("[PROTOCOL] bỏ qua hello trùng lặp.", "error")
        elif isinstance(message, UnknownMessage):
            self.log.message(f"[PROTOCOL] chưa hỗ trợ type={message.type}.", "info")

    async def handle_binary(self, payload: bytes) -> None:
        """Feed one uplink Opus packet into the agent.

        Audio is only accepted while the device is Listening. A `realtime`
        device keeps its microphone open during playback, and without
        server-side AEC that audio is its own speaker, which would trip the VAD.
        """

        if self._uplink is None or self._agent is None:
            return
        if self.device.state is not DeviceState.LISTENING:
            self.uplink_frames_dropped += 1
            return
        try:
            packet = decode_uplink_audio(payload, self.settings.protocol_version)
            frames = self._uplink.push(packet)
        except ProtocolError as exc:
            self.protocol_errors += 1
            self.log.message(f"[AUDIO] bỏ qua gói uplink: {exc}", "error")
            return
        except Exception as exc:
            self.protocol_errors += 1
            self.log.message(
                f"[AUDIO] giải mã Opus thất bại: {type(exc).__name__}: {exc}",
                "error",
            )
            return
        for frame in frames:
            self._agent.push_audio(frame)

    async def _on_listen(self, message: ListenMessage) -> None:
        previous = self.device.state
        self.device.on_listen(message.state, message.mode)
        self.log.event(
            "listen",
            state=message.state,
            mode=message.mode or self.device.mode.value,
            device_state=self.device.state.value,
        )
        if message.state == "start":
            if self._uplink is not None:
                self._uplink.reset()
            if previous is DeviceState.SPEAKING:
                await self._stop_speaking("listen start")
        elif message.state == "stop":
            # Push-to-talk release: the device owns end-of-speech in manual mode.
            if self._agent is not None:
                await self._agent.end_utterance()
        elif message.state == "detect":
            if self._uplink is not None:
                self._uplink.reset()
            if message.text:
                self.log.event("wake_word", text=message.text)

    async def _on_abort(self, message: AbortMessage) -> None:
        self.log.event("abort", reason=message.reason or "none")
        if self._agent is not None:
            await self._agent.cancel(f"xiaozhi abort ({message.reason or 'button'})")
        await self._stop_speaking("abort")

    def _on_mcp(self, message: McpMessage) -> None:
        if self._mcp is None:
            self.log.message("[MCP] nhận mcp nhưng phiên MCP chưa bật.", "error")
            return
        self.log.event(
            "mcp_in",
            mcp_id=message.payload.get("id"),
            method=message.payload.get("method"),
            error=bool(message.payload.get("error")),
        )
        self._mcp.handle_payload(message.payload)

    def _on_mcp_notification(self, method: str, params: dict[str, Any]) -> None:
        self.log.event("mcp_notification", method=method, params=sorted(params))

    # ----------------------------------------------------------------- outbound

    async def _send_mcp_payload(self, payload: dict[str, Any]) -> None:
        self.log.event(
            "mcp_out",
            mcp_id=payload.get("id"),
            method=payload.get("method"),
        )
        await self.transport.send_json(protocol.build_mcp(self.session_id, payload))

    async def _send_audio_frame(self, packet: bytes) -> None:
        await self.transport.send_bytes(
            encode_downlink_audio(packet, self.settings.protocol_version)
        )

    async def _start_speaking(self, text: str) -> None:
        """Send `stt` then `tts start`, which is what unlocks device playback.

        `Application::OnIncomingAudio` only queues packets while the device is
        Speaking, so this has to precede the first audio frame.
        """

        await self.transport.send_json(protocol.build_stt(self.session_id, text))
        if self.device.accepts_audio:
            return
        await self.transport.send_json(protocol.build_tts(self.session_id, "start"))
        self.device.on_tts_start()
        self._turn_started_at = time.monotonic()

    async def _stop_speaking(self, reason: str) -> None:
        if self.device.state is not DeviceState.SPEAKING:
            return
        if self._sink is not None:
            await self._sink.clear()
        await self.transport.send_json(protocol.build_tts(self.session_id, "stop"))
        self.device.on_tts_stop()
        self.log.event(
            "tts_stop",
            reason=reason,
            device_state=self.device.state.value,
            turn_ms=(
                round((time.monotonic() - self._turn_started_at) * 1000)
                if self._turn_started_at is not None
                else None
            ),
        )
        self._turn_started_at = None

    # -------------------------------------------------------------- agent pump

    async def _pump_agent_events(self) -> None:
        assert self._agent is not None
        async for event in self._agent.events():
            try:
                await self._on_agent_event(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.log.message(
                    f"[AGENT] lỗi khi xử lý {event.kind}: {type(exc).__name__}: {exc}",
                    "error",
                )

    async def _on_agent_event(self, event: AgentEvent) -> None:
        if event.kind == agent_events.TRANSCRIPT:
            text = event.text()
            if self.settings.log_transcripts:
                self.log.event("stt", chars=len(text), text=text)
            else:
                self.log.event("stt", chars=len(text))
            await self._start_speaking(text)
        elif event.kind == agent_events.SENTENCE:
            await self.transport.send_json(
                protocol.build_tts(self.session_id, "sentence_start", event.text())
            )
        elif event.kind == agent_events.STATE:
            await self._on_agent_state(event.state())
        elif event.kind == agent_events.METRIC:
            self.log.event(
                "metric",
                metric=event.name(),
                value=event.payload.get("value"),
                unit=event.payload.get("unit"),
            )
        elif event.kind in {agent_events.TOOL_CALL, agent_events.TOOL_RESULT}:
            self.log.event(event.kind, tool=event.name())
        elif event.kind == agent_events.LOG:
            # A turn that dies inside the agent is otherwise invisible here.
            if event.payload.get("level") == "error":
                self.log.message(str(event.payload.get("message")), "error")
        elif event.kind == agent_events.FATAL:
            self.log.message(f"[AGENT] {event.payload.get('message')}", "error")

    async def _on_agent_state(self, state: str) -> None:
        if state == ConversationState.PROCESSING.value:
            self._speech_end_at = time.monotonic()
        elif state in {ConversationState.IDLE.value, ConversationState.INTERRUPTED.value}:
            await self._stop_speaking(f"agent {state.lower()}")

    # ----------------------------------------------------------------- reporting

    def _spawn(self, coroutine, name: str) -> None:
        self._tasks.append(asyncio.create_task(coroutine, name=name))

    def snapshot(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "device_id": self.device_id,
            "client_id": self.client_id,
            "connected_at": self.connected_at,
            "device_state": self.device.state.value,
            "listen_mode": self.device.mode.value,
            "agent_state": self._agent.state if self._agent is not None else "unknown",
            "protocol_version": self.hello.version if self.hello is not None else None,
            "downlink_sample_rate": self.audio_params.sample_rate,
            "frame_duration_ms": self.audio_params.frame_duration_ms,
            "mcp_ready": self._tools.ready if self._tools is not None else False,
            "mcp_tools": len(self._tools.catalog) if self._tools is not None else 0,
            "mcp_pending": self._mcp.pending_count if self._mcp is not None else 0,
            "queued_audio_frames": self._pacer.queued if self._pacer is not None else 0,
            "audio_frames_sent": self._pacer.frames_sent if self._pacer is not None else 0,
            "protocol_errors": self.protocol_errors,
            "uplink_frames_dropped": self.uplink_frames_dropped,
            "dropped_events": self._agent.dropped_events if self._agent is not None else 0,
        }
