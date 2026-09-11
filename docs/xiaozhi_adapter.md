# Xiaozhi ESP32 Adapter

Lets official [`78/xiaozhi-esp32`](https://github.com/78/xiaozhi-esp32) devices talk to
this project's voice agent. The adapter is a protocol and transport layer only — it
translates, it does not think.

```text
Xiaozhi owns the device protocol.
This project owns the voice intelligence.
xiaozhi_adapter only translates between them.
```

Protocol details and the reasoning behind every design decision are in
[`xiaozhi_protocol_research.md`](xiaozhi_protocol_research.md), which was written from
firmware source rather than documentation. Read that before changing protocol behaviour.

## 1. Architecture

```text
xiaozhi-esp32
      │  Xiaozhi WebSocket protocol: JSON control + Opus audio + MCP
      ▼
┌──────────────────────────── app/xiaozhi_adapter ────────────────────────────┐
│                                                                            │
│  server.py            /xiaozhi/v1/ endpoint, auth, session registry        │
│  session/             DeviceSession: one socket, one isolated session      │
│    device_session.py    message dispatch, turn lifecycle, task ownership   │
│    audio_sink.py        AudioOutput implementation for the agent           │
│  protocol/            messages, binary framing, shadow device state        │
│  audio/               Opus codec, resampling, re-blocking, TTS pacing      │
│  mcp/                 device MCP client, tool mapping, ToolProvider         │
│  agent/               AgentSession: the generic voice-agent boundary       │
│                                                                            │
└────────────────────────────────┬───────────────────────────────────────────┘
                                 │  app.tools.ToolProvider + AudioOutput
                                 ▼
              Existing voice agent: VAD → ASR → LLM → TTS
                    (app/orchestrator.py, app/runtime.py)
```

One WebSocket connection is one `DeviceSession`. Sessions share only the model weights
held by `ModelRuntime`; there is no mutable global conversation state. Every task the
session starts — the agent, the TTS pacing loop, the agent event pump, the MCP
handshake — is created by `DeviceSession` and cancelled in `DeviceSession.close()`, so
nothing outlives the socket.

Nothing under `app/xiaozhi_adapter/` imports an ASR, LLM or TTS class. The only contact
with the agent is `AgentSession` (audio in, typed events out, cancel, close), the
`AudioOutput` protocol for TTS audio, and `app.tools.ToolProvider` for tool calling.

## 2. Supported protocol versions

**Protocol version 1 only.** The payload of a WebSocket binary frame is a bare Opus
packet, so the frame boundary is the packet boundary.

Versions 2 and 3 prepend a header (documented in the research notes, big-endian fields)
and are **refused at the handshake** with a message telling you to set
`websocket.version = 1` on the device. Version 1 is the firmware default and the only
one the reference backend implements for direct WebSocket connections, so stock devices
are unaffected.

## 3. Audio formats

| Direction | Format | Set by |
| --- | --- | --- |
| Uplink (microphone) | Opus, 16000 Hz, mono, 60 ms frames (960 samples) | Hard-coded in firmware; not negotiable |
| Downlink (speaker) | Opus, 24000 Hz, mono, 60 ms frames (1440 samples) | `xiaozhi.downlink_sample_rate` / `xiaozhi.frame_duration_ms` |

24000 Hz is the firmware's own default, the reference backend's default, and the output
rate of 23 of the 24 board configs. Opus accepts 8000/12000/16000/24000/48000.

Input and output rates differ, and neither matches the agent end to end. All conversion
is confined to `app/xiaozhi_adapter/audio/`:

```text
device mic  Opus 16 kHz/60 ms
  → libopus decode (FFmpeg always emits 48 kHz, 2880 samples/packet)
  → soxr 48 kHz → 16 kHz (stateful; a stateless call per packet clicks at every
    packet boundary)
  → re-block to 512-sample frames, the agent's VAD block size
  → VoiceOrchestrator.feed_audio

agent TTS  float32 PCM @ 48 kHz (VieNeu/vi) or 24 kHz (Kokoro/en)
  → soxr → 24 kHz (a no-op for the English voice)
  → re-block to 1440-sample Opus frames
  → paced binary WebSocket frames
```

The 48 kHz decode is a property of FFmpeg's Opus decoder, not a choice. libopus' own C
API can decode straight to 16 kHz, but no pip-installable Windows build of `opuslib`
ships `libopus`; PyAV bundles it.

### Why pacing is mandatory

The device decode queue holds `1200 / frame_duration` packets — 20 packets, 1.2 s — and
`PushPacketToDecodeQueue` **drops silently** when full. Sending a TTS stream at wire
speed destroys the audio. `audio/pacing.py` schedules on an absolute monotonic turn
start plus a running playback position, never `sleep(frame_duration)` per frame, so
per-iteration scheduling error cannot accumulate. The first
`xiaozhi.prebuffer_frames` frames go out as a burst to cut first-audio latency.

Queue overflow is handled by **backpressure**, not by dropping: `submit` awaits room.
Dropping would lose speech, and the producer is a per-turn task that is cancelled on
barge-in anyway.

## 4. Voice-agent API contract

The adapter depends on exactly three things from the agent.

**`AgentSession`** (`agent/client.py`) wraps `VoiceOrchestrator`:

| Method | Contract |
| --- | --- |
| `start()` | Launches the agent task. |
| `push_audio(frame)` | float32 mono, `audio.sample_rate` (16000), exactly `audio.block_size` (512) samples. |
| `cancel(reason)` | Cancels the turn in flight (barge-in). |
| `end_utterance()` | Closes the current utterance now, for push-to-talk. Drains queued audio first. |
| `events()` | Async iterator of `AgentEvent(kind, payload)`. |
| `close()` | Cancels the agent and releases it. |

Events consumed: `transcript` → `stt` + `tts start`; `tts_sentence` → `tts
sentence_start`; `state` → `tts stop` on IDLE/INTERRUPTED; `metric`, `log`,
`tool_call`, `tool_result`, `fatal` → structured logs.

**`AudioOutput`** (`app/audio/output.py`) — `session/audio_sink.py` implements the same
protocol as `SpeakerOutput` and `BrowserAudioOutput`, so the orchestrator is unchanged.
Two differences from the browser sink: an ESP32 sends no playback acknowledgements, so
"first played" and "drained" come from the pacer's own schedule; and frames are dropped
when the device is not Speaking, because the firmware would discard them anyway.

Stale-turn suppression reuses the orchestrator's existing per-turn id rather than a
second generation counter. Anything arriving for a turn that is no longer active is
dropped, which is what stops late TTS from a cancelled turn reaching the device.

**`app.tools.ToolProvider`** — `specs()` and `call(call, cancellation)`. A single
awaitable keeps request and result correlated by construction: if the turn is cancelled,
the awaiting task dies with it, so no late tool result can resurrect it.

## 5. MCP behaviour

The device is the MCP **server**; the adapter is the client. JSON-RPC 2.0 travels
in-band as `{"type":"mcp","payload":…}`. MCP and audio share the transport but are
implemented separately: `mcp/` never touches audio, `audio/` never touches JSON-RPC.

On a hello with `features.mcp == true`, the adapter runs `initialize` then `tools/list`,
normalises the schemas and hands them to the LLM. Flow:

```text
ESP32 --tools/list result--> adapter --normalise--> LLM
LLM --tool_call--> adapter --tools/call--> ESP32 --result--> adapter --> LLM
```

Behaviour dictated by `main/mcp_server.cc`:

- **Numeric request ids.** The firmware requires `cJSON_IsNumber(id)` and drops anything
  else *silently*. One monotonic counter serves every request, unlike the reference
  backend's fixed ids 1 and 2, which collide with its own tool-call counter.
- **No `notifications/initialized`.** The firmware returns early for any method starting
  with `notifications`, so inbound notifications are accepted and ignored.
- **Pagination.** The `tools/list` cursor is a tool *name* and is inclusive. The firmware
  omits `nextCursor` entirely on the last page, so missing and empty both mean done.
  Repeating cursors and runaway page counts are refused.
- **`withUserTools` is never sent.** The device then defaults it to false, keeping
  privileged tools — `self.reboot`, `self.get_system_info`, `self.screen.get_info`,
  the asset and screenshot helpers — out of the model's reach. Do not change this
  without deciding what may be invoked autonomously.
- **Tool names are sanitised.** Device tools are named like
  `self.audio_speaker.set_volume`; most tool-calling schemas reject dots.
  `DeviceToolCatalog` keeps the bidirectional map, so the LLM only ever sees sanitised
  names and only the catalog knows device names. A name that sanitises into a collision
  is skipped rather than allowed to shadow another tool.
- **`ToolCall.id` and the JSON-RPC id are different identifiers.** The former is minted
  for the model; the latter is owned by `DeviceMcpClient`. They are never assumed equal.
- **Failure is not fatal.** A device whose MCP handshake fails is still a usable voice
  device: the provider stays empty and the session continues. Unparseable MCP payloads
  are logged and skipped — the firmware builds error JSON by string concatenation
  without escaping, so a tool error containing a quote arrives malformed.

### Device tools need a prompt hint

With the stock conversational system prompt, `qwen3.5:4b` answers *"tôi đang tăng âm
lượng lên 70%"* and never emits a tool call. `DeviceSession` appends one sentence
(`DEVICE_TOOL_PROMPT`) when the device advertises MCP, which makes the call fire
reliably in both languages and for both polite and imperative phrasing. If you swap the
LLM and tool calls stop happening, check this first.

## 6. Configuration

All keys live under `xiaozhi:` in `config.yaml`.

```yaml
xiaozhi:
  enabled: false              # off by default
  path: /xiaozhi/v1/          # registered with and without the trailing slash
  access_token: null          # null disables auth; required on a LAN-exposed server
  language: vi                # vi or en, fixed for the session
  max_sessions: 4
  protocol_version: 1         # only 1 is supported
  downlink_sample_rate: 24000 # 8000/12000/16000/24000/48000
  frame_duration_ms: 60       # 20, 40 or 60; must match what we advertise
  opus_bitrate: 24000
  prebuffer_frames: 5         # immediate burst; must stay under 1200/frame_duration
  max_queued_frames: 100      # bounded outgoing queue, then backpressure
  mcp_enabled: true
  mcp_timeout_seconds: 10.0
  max_tool_rounds: 3
```

`AppConfig.validate()` rejects an out-of-range sample rate or frame duration, a
`prebuffer_frames` that would overrun the device decode queue, and a missing
`access_token` when `web.host` is not loopback.

### Authentication

The device sends `Authorization: Bearer <token>`. The firmware prepends `Bearer ` only
when the stored token contains no space, so a token stored with the prefix arrives
verbatim; both forms are accepted. `Device-Id` (MAC) and `Client-Id` (UUID) are read
from headers, with query-parameter fallbacks for test clients.

A request carrying an `Origin` header is refused unless it is in
`web.allowed_origins`: an ESP32 never sends `Origin`, so anything that does is a page,
not a device.

## 7. Pointing a device at this server

1. Enable the adapter and start the server (§8). It shares the port with the web UI.
2. Find the LAN address of the machine, for example `192.168.1.25`.
3. On the device, set the WebSocket URL to `ws://192.168.1.25:8080/xiaozhi/v1/`, the
   token if you configured one, and `version = 1`. These live in the `websocket` NVS
   namespace, set through the device's own configuration path (`wss://` needs
   `web.tls_certfile`/`web.tls_keyfile`).
4. Off loopback the server requires TLS, an access token of at least 16 characters, and
   `web.allowed_origins` — see §6 and the config validation messages.

This project does **not** provide an OTA endpoint, so it cannot hand the URL to the
device automatically the way `xiaozhi-esp32-server` does.

Check `GET /api/xiaozhi` for connected devices, negotiated audio parameters, MCP
readiness, tool count, pending MCP requests and queued audio frames.

## 8. Running locally

```bash
uv sync
ollama serve                      # in another terminal
```

Set `xiaozhi.enabled: true` in `config.yaml`, then:

```bash
uv run python main.py --web
```

The models load before the port opens, so the first start is slow. Verify:

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/api/xiaozhi
```

Structured logs carry `device_id`, `session_id` and `turn_id` on every line, plus the
MCP request id where relevant. Transcript lines record a character count, never the
text, and microphone audio, Opus payloads and tokens are never logged.

```text
event=hello version=1 uplink_sample_rate=16000 mcp=True aec=False
event=ready downlink_sample_rate=24000 frame_ms=60 language=vi elapsed_ms=1
event=metric metric=asr value=1032 unit=ms
event=mcp_out mcp_id=3 method=tools/call
event=tts_stop reason=agent idle device_state=listening turn_ms=344
```

Timing metrics emitted per turn: `utterance`, `asr`, `first_token`, `generation`,
`first_audio`, `total` (speech end to first played audio) and `tool_call`.

## 9. Running the tests

```bash
uv run pytest                                   # whole suite
uv run pytest tests/test_xiaozhi_endpoint.py    # one file
```

No GPU and no model downloads: `tests/conftest.py` scripts the brain (a stub VAD plus a
scripted ASR/LLM/TTS) so the tests exercise the adapter, not the models. Opus codecs and
the pacing clock are real.

| File | Covers |
| --- | --- |
| `test_xiaozhi_protocol.py` | hello negotiation and rejection, every client message, framing, shadow state machine |
| `test_xiaozhi_audio.py` | codec round trip, corrupt packets, resampling, re-blocking, pacing, drift, backpressure |
| `test_xiaozhi_session.py` | full turns, TTS state sequence, listen modes, barge-in, stale audio, reconnect, teardown |
| `test_xiaozhi_mcp.py` | initialize, pagination, correlation, errors, timeouts, notifications, user-only tools |
| `test_xiaozhi_endpoint.py` | routing, auth, session cap, handshake gate, receive loop, registry |

`app/xiaozhi_adapter/testing/` ships `FakeXiaozhiDevice`, which speaks the client half
of the protocol and reproduces the firmware's real MCP quirks. It is also usable for
manual verification.

**Physical ESP32 testing is still required before claiming compatibility.** Everything
above runs against a fake client.

## 10. Unsupported Xiaozhi features

Deferred deliberately. The session layer should not need rewriting to add any of them.

| Feature | Status |
| --- | --- |
| Binary protocol versions 2 and 3 | Refused at the handshake; layouts documented in the research notes |
| MQTT + UDP transport | Not implemented (`binary.py` is the framing seam) |
| Full-duplex `realtime` mode with server-side AEC | A `realtime` hello is accepted and treated as half-duplex; uplink audio is ignored while the device plays, because without AEC that audio is its own speaker and would trip the VAD |
| Vision (`initialize.params.capabilities.vision`) | Not advertised |
| Wake-word audio verification (`listen state=detect`) | Logged; the following microphone audio drives the turn normally |
| `notify`, `alert`, `system`, `custom`, `llm` emotion | Never sent |
| Dynamic text glyph push | `features.glyph_push` ignored |
| OTA / provisioning endpoint, device-management UI | Not provided; set the device URL by hand (§7) |
| Cloud MCP aggregation, music subsystem | Not implemented |
| `withUserTools: true` and privileged device tools | Deliberately never requested (§5) |
| Idle keepalive | Not sent. The firmware treats a channel with no inbound frame for 120 s as dead but reopens it transparently on the next wake word or button press |
| Language switching mid-session | The session language is fixed by `xiaozhi.language`; the web UI's per-session switch is not exposed to devices |

## 11. Upstream attribution

Design ideas and protocol behaviour were taken from
[`78/xiaozhi-esp32`](https://github.com/78/xiaozhi-esp32) (MIT) and
[`xinnan-tech/xiaozhi-esp32-server`](https://github.com/xinnan-tech/xiaozhi-esp32-server)
(MIT), at the commits recorded in the research notes. No upstream code is copied
verbatim; the pacing algorithm and the MCP correlation design are reimplemented. ASR,
VAD, LLM, TTS, memory, database and management-UI code from the reference backend is
deliberately not used — this project already has those.
