# Xiaozhi ESP32 Protocol Research

Research notes for the `xiaozhi_adapter` component. Everything below was read from
upstream source at the commits listed in [Sources](#1-sources). Where the upstream
documentation and the firmware code disagree, the **code wins** and the discrepancy is
recorded in [§8](#8-documentation--code-discrepancies).

## 1. Sources

| Repository | Commit | Date | License |
| --- | --- | --- | --- |
| `78/xiaozhi-esp32` (firmware, source of truth) | `ac6deed3d8e75348475364bf40ad953c6cd48054` | 2026-09-10 | MIT |
| `xinnan-tech/xiaozhi-esp32-server` (reference backend) | `5aa46538d5087aaee99a5fc318689a0da0a2a9c7` | 2026-09-08 | MIT |

Files inspected:

**Firmware**

- `AGENTS.md`, `README.md`
- `docs/websocket.md`, `docs/mcp-protocol.md`, `docs/mcp-usage.md`
- `main/protocols/protocol.h`, `main/protocols/protocol.cc`
- `main/protocols/websocket_protocol.h`, `main/protocols/websocket_protocol.cc`
- `main/mcp_server.h`, `main/mcp_server.cc`
- `main/application.cc` (JSON dispatch, listen/abort call sites, state handlers)
- `main/device_state.h`, `main/device_state_machine.h`
- `main/audio/audio_service.h`, `main/audio/audio_service.cc`
- `main/audio/audio_codec.h`, `main/boards/*/config.h`

**Reference backend**

- `core/websocket_server.py`, `core/connection.py`
- `core/handle/helloHandle.py`, `core/handle/abortHandle.py`
- `core/handle/receiveAudioHandle.py`, `core/handle/sendAudioHandle.py`
- `core/handle/textHandler/listenMessageHandler.py`, `.../pingMessageHandler.py`
- `core/handle/textMessageType.py`, `core/handle/textMessageHandlerRegistry.py`
- `core/utils/audioRateController.py`, `core/utils/util.py` (`sanitize_tool_name`)
- `core/providers/tools/device_mcp/mcp_client.py`, `.../mcp_handler.py`
- `config.yaml` (`xiaozhi:` server-hello template)

Both repositories are MIT licensed. No upstream code is copied verbatim into this
project; the pacing algorithm and the MCP correlation design are reimplemented from the
behaviour described here. Attribution is recorded in `app/xiaozhi_adapter/README.md`.

## 2. Connection lifecycle

```text
ESP32 (Application::ContinueOpenAudioChannel)
  |
  +- WS handshake to the configured websocket.url
  |    headers: Authorization, Protocol-Version, Device-Id, Client-Id
  |
  +- client hello  {"type":"hello", version, features, transport, audio_params}
  |
  +- waits <=10 s for a text frame with type=="hello" AND transport=="websocket"
  |    (WebsocketProtocol::OpenAudioChannel, xEventGroupWaitBits 10000 ms)
  |
  +- server hello  {"type":"hello","transport":"websocket","session_id",...}
  |    -> stores session_id, server_sample_rate_, server_frame_duration_
  |    -> on_audio_channel_opened_() fires, device enters Listening
  |
  +- listen start  {"session_id","type":"listen","state":"start","mode":...}
  |    then continuous binary Opus frames (microphone)
  |
  +- server: stt -> tts start -> [tts sentence_start] -> binary Opus -> tts stop
  |
  +- abort   {"session_id","type":"abort"[,"reason":"wake_word_detected"]}
  |
  +- disconnect: CloseAudioChannel() just drops the socket.
       No goodbye message is sent on the WebSocket transport
       (websocket_protocol.cc: `(void)send_goodbye;`).
```

Reconnection is entirely device-driven: the next wake word / button press calls
`OpenAudioChannel()` again, which repeats the whole handshake. The server must treat
every WebSocket connection as a fresh session and must not try to resume state.

### 2.1 Handshake headers (confirmed in `websocket_protocol.cc:OpenAudioChannel`)

| Header | Value | Notes |
| --- | --- | --- |
| `Authorization` | `Bearer <token>` | Only sent when a token is configured. The firmware prepends `Bearer ` **only if the stored token contains no space**, so a token stored as `Bearer abc` is passed through unchanged. |
| `Protocol-Version` | `"1"` / `"2"` / `"3"` | Decimal string of the binary framing version; equals the `version` field of the client hello. |
| `Device-Id` | MAC address | `SystemInfo::GetMacAddress()`. Stable device identity. |
| `Client-Id` | UUID | `Board::GetUuid()`. Regenerated when NVS is erased or the firmware is fully reflashed. |

There is **no** `session_id` in the URL and no query string. Header names arrive
lower-cased through the HTTP layer, so the adapter must look them up case-insensitively.
The reference backend additionally accepts `device-id` / `client-id` / `authorization` as
**query parameters** as a fallback for browser test clients; current firmware never uses
that path.

### 2.2 Endpoint path

`/xiaozhi/v1/` is the ecosystem convention: it is what the OTA response and every
deployment document hands to devices (`docs/Deployment.md:208`,
`docs/firmware-build.md:39`). It is **not** enforced by the reference backend —
`websockets.serve(self._handle_connection, ...)` ignores the request path entirely, and
the firmware simply connects to whatever URL is stored in NVS.

Decision: serve `/xiaozhi/v1/` (and tolerate `/xiaozhi/v1`) so stock device
configuration works unchanged.

### 2.3 Channel idle timeout

`Protocol::IsTimeout()` reports the channel dead after **120 s without any inbound
frame** (text or binary; `last_incoming_time_` is updated by both). Nothing closes the
socket proactively — the flag is only read when the device next tries to act, and the
device then transparently reopens the channel. So the server does not have to send
keepalives, but a long-idle session will be silently replaced. The firmware never sends
`{"type":"ping"}`; the reference backend's ping handler exists for browser test clients
only.

## 3. Client to server JSON messages

All of these are emitted by `Protocol::*` in `main/protocols/protocol.cc` and therefore
have a fixed, hand-built shape.

### hello

```json
{
  "type": "hello",
  "version": 1,
  "features": {"mcp": true, "aec": true, "glyph_push": false},
  "text_font": {"bundle": "noto-v1", "charset": "common", "size": 20, "bpp": 4},
  "transport": "websocket",
  "audio_params": {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60}
}
```

- `features.mcp` is **always** `true` on current firmware (unconditional
  `cJSON_AddBoolToObject(features, "mcp", true)`).
- `features.aec` appears only when `CONFIG_USE_SERVER_AEC` is compiled in.
- `features.glyph_push` and `text_font` come from `Protocol::AddTextFontCapabilities`;
  `text_font` is present only when `glyph_push` is true.
- Uplink `audio_params` are **hard-coded**: `opus`, 16000 Hz, 1 channel,
  `frame_duration = OPUS_FRAME_DURATION_MS = 60` (`main/audio/audio_service.h:40`).
  They are not negotiable and not affected by the board.
- The client hello carries **no** `session_id` (the device does not have one yet).

### listen

```json
{"session_id": "...", "type": "listen", "state": "start", "mode": "auto|manual|realtime"}
{"session_id": "...", "type": "listen", "state": "stop"}
{"session_id": "...", "type": "listen", "state": "detect", "text": "Hi XiaoZhi"}
```

- `mode` is present only on `state: "start"`.
- `Application::GetDefaultListeningMode()` returns `realtime` when any AEC mode is
  active and `auto` otherwise. `manual` comes from the explicit start/stop-listening
  button events.
- **`auto` means the server owns end-of-utterance detection.** The device never sends
  `listen stop` in auto mode (`SendStopListening` is only reached from
  `HandleStopListeningEvent`, i.e. a manual stop). This maps directly onto our existing
  Silero VAD.
- After every `tts stop` in auto/realtime mode the device re-enters Listening and sends
  a **new** `listen start` (`HandleStateChangedEvent` -> `StartListeningAudio`), deferred
  until its playback queue has drained. So one `listen start` per turn is normal.
- `state: "detect"` is the wake-word notification. It is only sent when
  `CONFIG_SEND_WAKE_WORD_DATA` is enabled, and is preceded by the buffered wake-word
  Opus frames.

### abort

```json
{"session_id": "...", "type": "abort", "reason": "wake_word_detected"}
```

`reason` is present only for `kAbortReasonWakeWordDetected`. Sent on button press while
speaking, and on wake word while speaking or listening.

### mcp

```json
{"session_id": "...", "type": "mcp", "payload": {"...JSON-RPC 2.0...": true}}
```

See [§6](#6-device-side-mcp).

### Not sent by current firmware

`goodbye`, `ping`, `iot` (deprecated in favour of MCP), `server`, and anything
device-state related. There is no device to server "state" message: the server infers
device state from `listen`/`abort` and from the fact that it owns `tts` transitions.

## 4. Server to client JSON messages

Dispatched by `Application::OnIncomingJson` (`main/application.cc:578`). `hello` is
intercepted earlier by `WebsocketProtocol::OnData` and never reaches this handler.

### hello (required)

```json
{
  "type": "hello",
  "transport": "websocket",
  "session_id": "...",
  "audio_params": {"format": "opus", "sample_rate": 24000, "channels": 1, "frame_duration": 60}
}
```

- `transport` must equal `"websocket"`, otherwise `ParseServerHello` returns without
  setting the event and the device times out after 10 s. **It must also be present**:
  the `nullptr` check short-circuits but the following `ESP_LOGE` dereferences
  `transport->valuestring` unconditionally, so a hello without `transport` is a null
  dereference on the device.
- Only `audio_params.sample_rate` and `audio_params.frame_duration` are read. `format`
  and `channels` are ignored — the device always assumes mono Opus. Defaults if omitted:
  `server_sample_rate_ = 24000`, `server_frame_duration_ = 60` (`protocol.h:76-77`).
- `session_id` is optional; when absent the device echoes an empty string in every
  subsequent message.

### stt

```json
{"session_id": "...", "type": "stt", "text": "what the user said"}
```

Display only. May carry an optional glyph payload (`glyph-push` extension).

### tts

```json
{"session_id": "...", "type": "tts", "state": "start"}
{"session_id": "...", "type": "tts", "state": "sentence_start", "text": "..."}
{"session_id": "...", "type": "tts", "state": "stop"}
```

- `start` sets `aborted_ = false` and moves the device to **Speaking**. This gates audio
  reception (see §5.3) and is therefore mandatory before any downlink audio.
- `sentence_start` requires `text`; purely a subtitle update.
- `stop`: if the device is Speaking, `manual` mode goes to **Idle**, `auto`/`realtime` go
  back to **Listening** (and emit a fresh `listen start`).
- `state` values other than these three are ignored.

### llm

```json
{"session_id": "...", "type": "llm", "emotion": "happy", "text": "smile"}
```

Only `emotion` is read; `text` is ignored by the firmware.

### mcp

```json
{"session_id": "...", "type": "mcp", "payload": {"...JSON-RPC 2.0...": true}}
```

`payload` must be a JSON **object**, otherwise the message is dropped silently.

### system / alert / custom / notify

- `system` supports exactly one command: `{"command": "reboot"}`.
- `alert` requires all three of `status`, `message`, `emotion` as strings.
- `custom` requires `CONFIG_RECEIVE_CUSTOM_MESSAGE`.
- `notify` requires a non-empty `audio_url` and plays an out-of-band stream with
  optional `subtitles`.

None of these are needed for a voice conversation; the adapter does not send them.

## 5. Audio transport

### 5.1 Binary framing versions

Selected by the NVS `websocket.version` setting; echoed in the `Protocol-Version` header
and the hello `version` field. Default is **1** (`version_ = 1`, only overwritten when
the stored int is non-zero).

**Version 1** — the payload is a bare Opus packet. Nothing else. The WebSocket frame
type already distinguishes text from binary.

**Version 2** — `struct BinaryProtocol2`, 16-byte header, **all fields big-endian**
(`htons`/`htonl` in `SendAudio`):

```text
 0      2      4                8               12              16
 +------+------+----------------+---------------+---------------+
 |vers. | type |    reserved    |   timestamp   | payload_size  | payload...
 +------+------+----------------+---------------+---------------+
   u16    u16        u32              u32             u32
```

`type` is 0 for Opus and 1 for JSON. `timestamp` is milliseconds and exists for
server-side AEC.

**Version 3** — `struct BinaryProtocol3`, 4-byte header, `payload_size` big-endian:

```text
 0     1     2               4
 +-----+-----+---------------+
 |type |resv.| payload_size  | payload...
 +-----+-----+---------------+
   u8    u8        u16
```

Downlink parsing mirrors this in `WebsocketProtocol::OnData`. On downlink the device
uses `server_sample_rate_`/`server_frame_duration_` from the hello, **not** anything in
the binary header, so v2/v3 headers add nothing to the downlink except overhead.

**MVP decision: version 1 only.** The firmware default is 1, and the reference backend
only implements raw Opus for direct WebSocket connections (`core/connection.py:367-380`
decodes the whole binary message; the 16-byte header path is gated on
`conn_from_mqtt_gateway`). The adapter rejects hello with `version` 2 or 3 rather than
pretending to support them.

### 5.2 Uplink (microphone)

Fixed by `AS_OPUS_ENC_CONFIG()` and `OPUS_FRAME_DURATION_MS`:

| Property | Value |
| --- | --- |
| codec | Opus (`application_mode = AUDIO`) |
| sample rate | 16000 Hz |
| channels | 1 |
| frame duration | 60 ms, i.e. **960 samples per packet** |
| bitrate | `ESP_OPUS_BITRATE_AUTO` |

`audio_service.cc:294` confirms `samples = OPUS_FRAME_DURATION_MS * 16000 / 1000`.
Frames are sent one WebSocket binary message per Opus packet. Packet boundaries are
therefore message boundaries — there is no need to scan for framing.

### 5.3 Downlink (speaker) - the two hard constraints

1. **Audio is only accepted in the Speaking state.**

   ```cpp
   protocol_->OnIncomingAudio([this](std::unique_ptr<AudioStreamPacket> packet) {
       if (GetDeviceState() == kDeviceStateSpeaking) {
           audio_service_.PushPacketToDecodeQueue(std::move(packet));
       }
   });
   ```

   Anything sent outside Speaking is discarded without a diagnostic. `tts start` is what
   puts the device into Speaking.

2. **The device decode queue holds 20 packets.**
   `MAX_DECODE_PACKETS_IN_QUEUE = 1200 / OPUS_FRAME_DURATION_MS` = 20, i.e. **1.2 s** of
   audio. `PushPacketToDecodeQueue` is called with the default `wait = false` from the
   receive callback, so **an over-full queue silently drops the packet**. Flooding a TTS
   stream at wire speed destroys audio. Pacing is not an optimisation, it is a
   correctness requirement.

Downlink format comes from our own server hello, and the device reconfigures its decoder
from it (`SetDecodeSampleRate(packet->sample_rate, packet->frame_duration)`, where both
come from `server_sample_rate_`/`server_frame_duration_`). The decoder output buffer is
sized `sample_rate / 1000 * frame_duration`, so the Opus frame duration we encode **must
match** the `frame_duration` we advertised. If the negotiated rate differs from the board
output rate the device resamples and logs a distortion warning.

### 5.4 Chosen server audio parameters

`24000 Hz, mono, 60 ms` — matching the firmware's own default (`protocol.h:76`), the
reference backend default (`config.yaml: xiaozhi.audio_params`), and 23 of the 24 board
configs (`AUDIO_OUTPUT_SAMPLE_RATE 24000`; only one board uses 16000). Opus accepts
8000/12000/16000/24000/48000, so this stays configurable if a board needs something else.

### 5.5 Conversion inside the adapter

Input and output rates are **not** the same, and neither matches our voice agent
end to end:

```text
device mic   Opus 16 kHz / 60 ms
   -> libopus decode (ffmpeg always outputs 48 kHz s16 mono, 2880 samples/packet)
   -> soxr 48 kHz -> 16 kHz  (960 samples)
   -> re-block to 512-sample float32 frames   (agent block_size)
   -> VoiceOrchestrator.feed_audio

agent TTS    float32 PCM @ 48 kHz (VieNeu / vi) or 24 kHz (Kokoro / en)
   -> soxr -> 24 kHz  (no-op for the English voice)
   -> re-block to 1440-sample frames
   -> libopus encode 24 kHz / 60 ms
   -> paced binary WebSocket frames
```

The 48 kHz decoder output is a property of FFmpeg's Opus decoder, not a choice: the
libopus C API can decode straight to 16 kHz, but no pip-installable Windows build of
`opuslib` ships `libopus`, whereas PyAV bundles it. All of this lives in
`app/xiaozhi_adapter/audio/` and nowhere else.

## 6. Device-side MCP

The device is the MCP **server**; the adapter is the MCP client. Transport is the
in-band `{"type":"mcp","payload":...}` envelope. Read from
`main/mcp_server.cc:ParseMessage/GetToolsList/DoToolCall`.

### Request handling on the device

1. `payload.jsonrpc` must be exactly `"2.0"`, else the message is dropped.
2. `payload.method` must be a string, else dropped.
3. **Any method whose name starts with `notifications` returns immediately.** So client
   to device notifications are accepted and ignored; do not expect a reply, and do not
   bother sending `notifications/initialized`.
4. `payload.params`, if present, must be an object.
5. **`payload.id` must be a JSON number.** `cJSON_IsNumber` — a string id is rejected and
   the request is dropped *silently*, with no error response. This is checked *after* the
   notifications short-circuit, so requests must always carry an integer id.

### Methods

| Method | Behaviour |
| --- | --- |
| `initialize` | Optionally reads `params.capabilities.vision.{url,token}` and wires the camera upload endpoint. Replies `{"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":BOARD_NAME,"version":<fw>}}`. |
| `tools/list` | `params.cursor` (string) and `params.withUserTools` (bool, default false). |
| `tools/call` | `params.name` (string, required), `params.arguments` (object, optional). |
| anything else | `error -32601 "Method not implemented: <m>"` |

### tools/list pagination

`GetToolsList` walks the tool vector, budgeting `max_payload_size = 8000` bytes:

- The cursor is a **tool name**, and it is **inclusive** — iteration skips forward until
  `tool->name() == cursor`, then that tool is the first one emitted.
- On the last page the response has **no `nextCursor` key at all** (`json += "]}"`).
  A `nextCursor` key is only added when there is more to fetch. Treat *missing or empty*
  as "done".
- If even a single tool does not fit, the device replies `error -32603` instead of a
  result.

### Tool schema

```json
{
  "name": "self.audio_speaker.set_volume",
  "description": "...",
  "inputSchema": {
    "type": "object",
    "properties": {"volume": {"type": "integer", "minimum": 0, "maximum": 100}},
    "required": ["volume"]
  }
}
```

Property types are limited to `boolean`, `integer`, `string`, with optional `default`,
`minimum`/`maximum` (integer) and `maxLength` (string). A property with a `default` is
omitted from `required`. `required` is omitted entirely when empty. Tool names contain
dots, which most tool-calling APIs reject, so the adapter keeps a bidirectional
sanitised-name map (upstream does the same via `sanitize_tool_name`).

### Results and errors

```json
{"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"true"}],"isError":false}}
{"jsonrpc":"2.0","id":3,"error":{"code":-32601,"message":"..."}}
```

Error codes observed: `-32602` (missing/invalid params, unknown tool, bad argument type,
missing required argument, validation failure), `-32601` (unknown method), `-32603`
(internal / tool failure / tools-list overflow).

`ReplyError` builds its JSON by string concatenation and does **not** escape the message,
so a tool error containing a double quote produces malformed JSON on the wire. The
adapter must tolerate an unparseable MCP payload without killing the session.

### Regular vs. user-only tools

`AddTool` registers a regular tool; `AddUserOnlyTool` marks `user_only_` and the tool is
excluded from `tools/list` unless `withUserTools: true`. Current user-only tools include
`self.get_system_info`, `self.reboot`, `self.screen.get_info`,
`self.assets.set_download_url` and the screenshot/asset upload helpers — privileged
actions that should not be autonomously invocable.

**The adapter never sends `withUserTools: true`.** It is omitted entirely, which lets the
device default to `false`.

### Device-initiated messages

The device can send JSON-RPC notifications (no `id`) such as
`notifications/state_changed`, plus the responses to our requests. The adapter must
correlate strictly on `payload.id`.

## 7. Device state machine

`main/device_state.h`:
`Unknown, Starting, WifiConfiguring, Idle, Connecting, Listening, Speaking, Notifying,
Upgrading, Activating, AudioTesting, FatalError`.

The transitions that a WebSocket backend can influence:

```text
Idle           --(wake word / button)--> Connecting
Connecting     --(server hello)-------->  Listening
Listening      --(tts start)----------->  Speaking
Speaking       --(tts stop, auto)------>  Listening   [+ new "listen start"]
Speaking       --(tts stop, manual)---->  Idle
Listen|Speak   --(device abort)-------->  playback stops; server should confirm
any            --(socket closed)------->  Idle
```

The device owns this machine. The adapter deliberately does **not** run a second
authoritative state machine: it tracks the *device's* state as a shadow value derived
from the messages it sends and receives (`listen`/`abort` inbound, `tts` outbound), and
uses it only to decide whether downlink audio may be emitted. Conversation state
(idle/listening/processing/speaking) already lives in `app/state.py` inside the voice
agent and stays there.

## 8. Documentation / code discrepancies

The MCP document carries its own warning that it was AI-assisted. These are the concrete
mismatches found.

1. **`tools/list` last page.** `docs/mcp-protocol.md` shows `"nextCursor": "..."` in
   every response example. The code omits the key entirely on the final page.
   *Follow the code:* absent or empty means done.
2. **JSON-RPC `id` type.** The document does not constrain it. `mcp_server.cc` requires
   `cJSON_IsNumber(id)` and silently drops non-numeric ids, with no error reply.
   *Follow the code:* integers only.
3. **`notifications/*` from the backend.** The document's sequence diagram only shows
   device to backend notifications; it does not state that the device drops inbound ones.
   The code returns before any dispatch for methods starting with `notifications`.
   *Follow the code:* the MCP handshake is `initialize` then `tools/list`, with no
   `notifications/initialized`.
4. **`features` in the client hello.** `docs/websocket.md` says `features` is "optional
   and generated from compile-time configuration", implying `mcp` may be absent.
   `GetHelloMessage()` adds `"mcp": true` unconditionally on current firmware. The
   adapter still treats a missing `features.mcp` as "no MCP" so older firmware keeps
   working.
5. **`transport` in the server hello.** The document says the reply "must include"
   `transport`. The code additionally *null-dereferences* when it is missing, so this is
   a hard crash rather than a rejected handshake. Always send it.
6. **Server hello `audio_params`.** The document's example includes `format` and
   `channels`. `ParseServerHello` reads only `sample_rate` and `frame_duration`. Sending
   the other two is harmless and kept for readability.
7. **Downlink buffering.** `docs/websocket.md` never mentions the 20-packet / 1.2 s
   decode queue or the fact that over-full pushes are dropped rather than blocked. Only
   `audio_service.h:43` and `PushPacketToDecodeQueue` reveal it.
8. **"Frames received while listening are dropped."** The document's phrasing suggests
   only the Listening state drops audio. The code accepts audio **only** in Speaking, so
   Idle/Connecting also drop it.
9. **`listen stop` in auto mode.** The document lists `stop` as a generic state without
   saying which mode produces it. The code only sends it from the manual stop-listening
   event, so an auto-mode server must run its own end-of-speech detection.
10. **Version 2/3 endianness.** The struct comments in `docs/websocket.md` and
    `protocol.h` do not mention byte order; `SendAudio` uses `htons`/`htonl`, so every
    multi-byte field is big-endian.

## 9. Existing voice-agent API (integration surface)

Inspected `app/orchestrator.py`, `app/runtime.py`, `app/audio/output.py`,
`app/web/sessions.py`, `app/web/server.py`, `app/config.py`, `app/cancellation.py`,
`app/state.py`, and the `app/{asr,llm,tts}/base.py` interfaces.

The agent already exposes a **long-lived in-process streaming interface** — exactly what
Phase 4 asks for. No HTTP-per-chunk anywhere.

| Capability | Existing surface |
| --- | --- |
| audio input | `VoiceOrchestrator.feed_audio(np.ndarray)` — float32, mono, `config.audio.sample_rate` (16000), exactly `config.audio.block_size` (512) samples per call. Bounded internal queue (64), drops oldest when full. |
| VAD events | Internal (`SileroVADSegmenter`); surfaced indirectly as `state` events and a `metric`/`utterance` event. |
| ASR result | `transcript` event (final only; the backends are non-streaming). |
| LLM output | `assistant_delta` (token stream) and `assistant_done` (full text) events. |
| LLM tool calls | **Not supported.** `LLMService.generate_stream(messages, cancellation) -> AsyncIterator[str]` yields text only; `OllamaLLMService` does not send `tools` and ignores `message.tool_calls`. |
| TTS audio | Pushed to an injected `AudioOutput` (`app/audio/output.py`): `start`, `begin_turn(turn_id)`, `enqueue(turn_id, samples)`, `wait_first_played(turn_id)`, `wait_drained(turn_id)`, `clear()`, `close()`. Samples are float32 at `config.audio.output_sample_rate`. |
| cancellation | `orchestrator.interrupt(source)` plus the per-turn `TurnCancellation` (async event and threading event for model threads). |
| turn completion | `assistant_done` event and `wait_drained(turn_id)`. |
| model sharing | `ModelRuntime` owns the weights and per-stage concurrency gates; many sessions share one instance. |

`app/web/sessions.py` is the existing precedent for "one client = one orchestrator
sharing one runtime", and `BrowserAudioOutput` is the precedent for an `AudioOutput`
implementation that streams to a socket. The Xiaozhi adapter follows the same shape.

### 9.1 Consequences for the adapter

- **Reuse, do not reimplement.** `DeviceSession` owns a `VoiceOrchestrator` with
  `use_local_microphone=False` and a Xiaozhi-specific `AudioOutput`. VAD/ASR/LLM/TTS stay
  untouched.
- **`turn_id` already exists.** The orchestrator's per-turn id is passed to every
  `AudioOutput` call and stale turns are already filtered at the sink
  (`if turn_id != self._active_turn: return`). The adapter piggybacks on it rather than
  inventing a parallel generation counter.
- **Re-blocking is required in both directions**: 960-sample uplink frames to 512-sample
  agent frames; variable-length TTS chunks to 1440-sample Opus frames.
- **Tool calling has to be added to the agent.** Device MCP tools are useless unless the
  LLM layer can request them. The smallest change that keeps the boundary clean is a
  provider-neutral tool interface in the agent (a `ToolProvider` the orchestrator can be
  given, plus `tools` support in `OllamaLLMService`), with the *only* Xiaozhi-aware
  implementation living in the adapter's `mcp/` package. The adapter never imports ASR,
  LLM or TTS classes.
- **Interface deviation from the Phase 4 sketch.** The sketch lists `update_tools()` and
  `submit_tool_result()`. In-process, a single awaitable
  `call_tool(name, arguments, cancellation)` on a `ToolProvider` gives request/result
  correlation for free and removes a whole class of dangling-future bugs. The
  `tool_call_id` to JSON-RPC `id` mapping still exists — it lives inside the adapter's
  MCP client, which is the only component that needs it.

## 10. MVP scope

**In scope**

- WebSocket transport at `/xiaozhi/v1/`, protocol version 1.
- hello negotiation, session id, per-connection isolation.
- `listen` start/stop/detect, auto and manual modes.
- Uplink Opus to PCM into the existing VAD/ASR.
- `stt`, `tts start` / `sentence_start` / `stop`, paced downlink Opus.
- `abort` / barge-in with stale-turn suppression.
- Device MCP: `initialize`, `tools/list` (with pagination), `tools/call`, notifications,
  timeouts, pending-request cleanup.

**Deferred by design** (the session layer must not need rewriting to add them)

- MQTT + UDP transport, and the 16-byte MQTT-gateway audio header.
- Binary protocol versions 2 and 3.
- Full-duplex `realtime` mode with server-side AEC. A `realtime` hello is accepted and
  treated as auto; the server-side AEC path is not implemented.
- Vision (`initialize.params.capabilities.vision`), OTA/provisioning, device management
  UI, cloud MCP aggregation, music subsystem, `notify`, glyph push, and the
  `alert`/`system`/`custom` messages.
- `withUserTools: true` and any privileged device tool.
