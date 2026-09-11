# xiaozhi_adapter

Protocol and transport layer that lets official [`78/xiaozhi-esp32`](https://github.com/78/xiaozhi-esp32)
devices use this project's voice agent. It translates; it does not think.

Setup, configuration and behaviour: [`docs/xiaozhi_adapter.md`](../../docs/xiaozhi_adapter.md).
Protocol facts and the reasoning behind them: [`docs/xiaozhi_protocol_research.md`](../../docs/xiaozhi_protocol_research.md).
**Read the research notes before changing protocol behaviour** — most of the
non-obvious choices here exist because of a firmware detail recorded there.

## Module map

| Path | Responsibility |
| --- | --- |
| `server.py` | `/xiaozhi/v1/` endpoint, auth, handshake gate, session registry |
| `session/device_session.py` | One socket, one session: dispatch, turn lifecycle, task ownership |
| `session/audio_sink.py` | `AudioOutput` implementation: agent TTS PCM to paced device Opus |
| `protocol/messages.py` | Parsing and building the exact shapes the firmware emits |
| `protocol/binary.py` | Binary framing seam; version 1 only, 2/3 refused |
| `protocol/state.py` | Shadow of the firmware state machine, not a second authority |
| `audio/codec.py` | libopus encode/decode via PyAV |
| `audio/resampler.py` | Stateful soxr resampling and fixed-size re-blocking |
| `audio/pipeline.py` | Uplink and downlink chains |
| `audio/pacing.py` | Drift-free TTS frame scheduling |
| `mcp/client.py` | Device MCP client: JSON-RPC framing and request correlation |
| `mcp/tool_mapping.py` | Sanitised-name mapping and schema normalisation |
| `mcp/provider.py` | `app.tools.ToolProvider` backed by device MCP |
| `agent/client.py` | `AgentSession`: the generic voice-agent boundary |
| `agent/events.py` | Transport-neutral agent event type |
| `logging.py` | Per-session structured logging |
| `testing/fake_device.py` | `FakeXiaozhiDevice`, for tests and manual verification |

## Boundary rules

- Nothing here imports an ASR, LLM or TTS class. The only contact with the agent is
  `AgentSession`, the `AudioOutput` protocol, and `app.tools.ToolProvider`.
- `mcp/` never touches audio and `audio/` never touches JSON-RPC, even though both
  share the WebSocket.
- All format conversion lives in `audio/`, nowhere else.
- `DeviceSession` owns every task it starts and cancels them all in `close()`.
- The device owns its state machine. `protocol/state.py` only mirrors it, for one
  decision: the firmware discards downlink audio unless it is Speaking.

## Attribution

Design ideas and protocol behaviour were taken from
[`78/xiaozhi-esp32`](https://github.com/78/xiaozhi-esp32) (MIT) and
[`xinnan-tech/xiaozhi-esp32-server`](https://github.com/xinnan-tech/xiaozhi-esp32-server)
(MIT), at the commits recorded in the research notes. Specifically reused as *behaviour*,
not code: protocol negotiation, session lifecycle, audio packet parsing, audio pacing,
listen and abort handling, and MCP request/result correlation.

No upstream code is copied verbatim. The pacing algorithm and the MCP correlation design
are reimplemented from the documented behaviour. ASR, VAD, LLM, TTS, memory, database and
management-UI code from the reference backend is deliberately not used.
