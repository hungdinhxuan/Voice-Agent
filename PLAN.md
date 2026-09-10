# Implementation plan and status

Updated 2026-09-10. The original single-user local pipeline below is operational. The web hardening roadmap is now:

- [x] M0 — audit README limitations and define ownership: a session owns VAD/history/cancellation/playback; `ModelRuntime` owns shared model lifecycle and inference gates.
- [x] M1 — isolate every WebSocket client into an independent voice session while loading ASR/LLM/TTS only once.
- [x] M2 — move server-to-browser TTS from base64 JSON to versioned binary PCM, add sequence-gap telemetry and configurable playback prebuffer.
- [x] M3 — make stop phrases accent/case tolerant, reject long conversational false positives, request browser AEC/noise suppression and expose actual track capabilities.
- [x] M4 — normalize Markdown, URLs, email, dates, units, currency and common technical abbreviations before TTS without changing UI text.
- [x] M5 — expose `/api/runtime` and `/api/sessions`, track queue pressure/audio gaps, and require TLS + token + origin allowlist when binding outside loopback.
- [ ] M6 — integrate and smoke-test a native streaming Vietnamese ASR backend. Candidate: `nvidia/nemotron-3.5-asr-streaming-0.6b`; keep Parakeet CTC as rollback.
- [ ] M7 — add session resume/persistence, Opus transport and hardware-tested XiaoZhi/ESP32 support after M6 latency and long-conversation gates pass.

Acceptance gates for completed web work: two clients can speak independently; one client cannot mutate another's history/playback; model adapters load once; binary audio has a version and sequence; LAN mode fails closed without TLS/token/origins; model-free test suite passes.

Build a fully local, open-source, real-time Vietnamese voice conversation application that runs on a single PC.

The final goal is:

Microphone  
→ Silero VAD  
→ NVIDIA Parakeet CTC 0.6B Vietnamese  
→ Qwen3.5-4B  
→ VieNeu-TTS v3 Turbo  
→ Speaker

The application must work completely locally after model files have been downloaded. Do not use OpenAI API, Gemini API, cloud ASR, cloud TTS, or any paid/external inference service.

## Main objectives

Create a working Python repository where I can run:

```bash
uv sync
uv run python main.py

```

and then speak Vietnamese through my microphone and receive a spoken Vietnamese response in real time.

Prioritize:

1. Low latency
2. Vietnamese quality
3. Clean modular architecture
4. Easy debugging
5. Ability to replace individual ASR/LLM/TTS components later
6. Future integration with XiaoZhi ESP32 via WebSocket/Opus
7. Fully local execution

Do not over-engineer the first version.

---

# Required stack

Use:

- Python 3.11 or 3.12
- `uv` for dependency/environment management
- Silero VAD for voice activity detection
- NVIDIA Parakeet CTC 0.6B Vietnamese for speech recognition
- Qwen3.5-4B for the conversational LLM
- VieNeu-TTS v3 Turbo for Vietnamese TTS
- ONNX CPU backend for VieNeu-TTS if practical
- GPU for Parakeet ASR and Qwen LLM
- `sounddevice`, PortAudio, or another reliable local audio backend
- asyncio wherever appropriate

Pipecat may be used if it meaningfully simplifies interruption handling and real-time frame processing, but do not force Pipecat if a smaller custom asyncio implementation is more robust.

---

# Hardware assumptions

Target machine:

- NVIDIA GPU
- Preferably 16 GB or more VRAM
- Modern multicore CPU
- Windows or Linux support preferred

The architecture should allow:

```text
GPU:
- NVIDIA Parakeet CTC 0.6B Vietnamese
- Qwen3.5-4B

CPU:
- Silero VAD
- audio I/O
- VieNeu-TTS ONNX
- orchestration

```

Use quantization for the LLM if necessary to reduce VRAM.

Do not sacrifice maintainability for premature optimization.

---

# Version 1 scope

Implement only:

```text
Microphone
    ↓
Silero VAD
    ↓
utterance segmentation
    ↓
NVIDIA Parakeet CTC 0.6B Vietnamese
    ↓
conversation history
    ↓
Qwen3.5-4B
    ↓
streaming text chunker
    ↓
VieNeu-TTS
    ↓
audio playback

```

Do NOT implement yet:

- camera
- robot movement
- MCP
- XiaoZhi
- ESP32
- wake word
- RAG
- vector database
- user accounts
- web UI
- cloud services

These should be easy to add later, but are outside the first working milestone.

---

# Audio input

Capture microphone audio continuously.

Use a format compatible with Silero VAD and Parakeet CTC, preferably:

```text
16 kHz
mono
PCM float32 or int16

```

The VAD should detect:

- speech start
- speech continuation
- speech end

Use configurable values such as:

```yaml
vad:
  threshold: 0.5
  min_speech_ms: 250
  min_silence_ms: 400
  speech_pad_ms: 150

```

Do not call the ASR continuously for silence.

Buffer only the current utterance.

When enough silence is detected:

```text
speech buffer
→ ASR

```

Log:

```text
[VAD] speech started
[VAD] speech ended: 2.43 sec

```

---

# ASR

Use:

```text
nvidia/parakeet-ctc-0.6b-Vietnamese

```

The application must recognize Vietnamese.

Create an interface similar to:

```python
class ASRService:
    async def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        ...

```

The ASR implementation must be isolated from the rest of the application.

For the first version, utterance-level ASR is sufficient.

Do not require streaming ASR initially.

Later it should be possible to replace it with streaming ASR without redesigning the entire application.

Log:

```text
[ASR] Tôi muốn hỏi bạn một câu.

```

If ASR returns empty or nonsensical text, ignore the turn safely.

---

# LLM

Use:

```text
Qwen3.5-4B

```

Run locally.

Prefer a local inference backend such as:

- transformers
- vLLM
- llama.cpp
- another suitable open-source runtime

Choose the implementation that gives the best balance of simplicity and low latency for a single local user.

The LLM layer must be abstracted:

```python
class LLMService:
    async def generate_stream(
        self,
        messages: list[dict]
    ):
        yield token

```

Maintain conversation history:

```python
[
    {"role": "system", ...},
    {"role": "user", ...},
    {"role": "assistant", ...}
]

```

Use a Vietnamese-friendly system prompt such as:

```text
Bạn là trợ lý hội thoại cho một robot.

Hãy trả lời bằng tiếng Việt tự nhiên, ngắn gọn và phù hợp với hội thoại bằng giọng nói.

Không sử dụng Markdown trừ khi thật sự cần thiết.

Không trả lời quá dài nếu người dùng không yêu cầu.

Ưu tiên câu văn dễ đọc thành tiếng.

```

The LLM must support streaming generation.

---

# LLM → TTS chunking

Do NOT wait for the full LLM response before starting TTS.

Create a text chunker.

Example:

LLM output:

```text
Được rồi. Tôi sẽ kiểm tra giúp bạn. Có thể mất một chút thời gian.

```

The TTS pipeline should receive:

```text
"Được rồi."

```

then:

```text
"Tôi sẽ kiểm tra giúp bạn."

```

then:

```text
"Có thể mất một chút thời gian."

```

Prefer chunk boundaries at:

```text
.
?
!
;
:

```

Allow comma boundaries when the current text segment becomes long enough.

Avoid sending extremely short fragments such as:

```text
"và"
"nhưng"
"thì"

```

Implement configurable thresholds such as:

```yaml
tts_chunker:
  min_chars: 15
  preferred_chars: 60
  max_chars: 120

```

---

# TTS

Use:

```text
VieNeu-TTS v3 Turbo

```

Prefer the ONNX CPU backend if available and stable.

Expose:

```python
class TTSService:
    async def synthesize_stream(self, text: str):
        yield audio_chunk

```

Audio should begin playback as early as practical.

Do not wait for the entire LLM answer to finish.

Use a queue:

```text
LLM
 ↓
text chunks
 ↓
TTS queue
 ↓
audio queue
 ↓
speaker

```

The TTS worker and LLM worker should run concurrently.

---

# Playback

Implement reliable audio playback.

Avoid gaps between TTS chunks where possible.

Use a dedicated playback queue.

Architecture:

```text
VieNeu
  ↓
AudioChunk
  ↓
async queue
  ↓
speaker worker

```

The speaker worker should be independent of LLM generation.

---

# Interruption / barge-in

This is an important requirement.

While the assistant is speaking, continue monitoring the microphone.

If the VAD detects new user speech:

```text
user starts speaking
        ↓
cancel current LLM generation
        ↓
clear pending TTS text
        ↓
clear pending audio playback
        ↓
start recording new user utterance

```

The user should be able to interrupt the assistant naturally.

Implement cancellation using asyncio tasks/events rather than killing the whole process.

For version 1, assume the user uses:

- headset microphone
- headphones

Therefore acoustic echo cancellation is NOT required yet.

Add a clear TODO/interface for adding WebRTC AEC later.

---

# State machine

Use an explicit conversation state such as:

```python
IDLE
LISTENING
PROCESSING
SPEAKING
INTERRUPTED

```

Avoid hidden state scattered across multiple modules.

Example:

```text
IDLE
 ↓ speech
LISTENING
 ↓ silence
PROCESSING
 ↓ first TTS audio
SPEAKING
 ↓ finished
IDLE

```

For interruption:

```text
SPEAKING
 ↓ user speech
INTERRUPTED
 ↓
LISTENING

```

---

# Repository structure

Use approximately:

```text
local-voice-agent/
├── pyproject.toml
├── README.md
├── main.py
├── config.yaml
│
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── orchestrator.py
│   ├── state.py
│   │
│   ├── audio/
│   │   ├── input.py
│   │   ├── output.py
│   │   └── queues.py
│   │
│   ├── vad/
│   │   └── silero.py
│   │
│   ├── asr/
│   │   ├── base.py
│   │   └── qwen3_asr.py
│   │
│   ├── llm/
│   │   ├── base.py
│   │   └── qwen35.py
│   │
│   ├── tts/
│   │   ├── base.py
│   │   └── vieneu.py
│   │
│   ├── conversation/
│   │   ├── history.py
│   │   └── chunker.py
│   │
│   └── utils/
│       ├── logging.py
│       └── timing.py
│
└── tests/

```

You may adjust the structure if there is a good engineering reason.

---

# Configuration

Put important settings in `config.yaml`.

Example:

```yaml
audio:
  sample_rate: 16000
  channels: 1
  input_device: null
  output_device: null

vad:
  threshold: 0.5
  min_speech_ms: 250
  min_silence_ms: 400

asr:
  model: nvidia/parakeet-ctc-0.6b-Vietnamese
  device: cuda

llm:
  model: Qwen/Qwen3.5-4B
  device: cuda
  temperature: 0.6
  max_tokens: 256

tts:
  backend: onnx
  device: cpu

conversation:
  max_history_turns: 20

```

Avoid hardcoded device IDs and absolute paths.

---

# Logging and latency measurement

Measure every stage.

For every turn print something like:

```text
[VAD] utterance: 2.18 s

[ASR]
text: "Bạn có nghe rõ tôi không?"
latency: 143 ms

[LLM]
first token: 96 ms
generation: 412 ms

[TTS]
first audio: 184 ms

[TOTAL]
speech-end → first assistant audio: 487 ms

```

Track at minimum:

- utterance duration
- ASR latency
- LLM time to first token
- LLM total generation time
- TTS time to first audio
- speech-end to first played audio

This instrumentation is important because the project will later be optimized for real-time robot interaction.

---

# Error handling

The application must survive:

- empty ASR output
- microphone disconnect
- temporary inference exception
- TTS exception
- cancelled LLM generation
- audio buffer underrun
- Ctrl+C

Ctrl+C should terminate cleanly and release:

- microphone
- speaker
- GPU resources if practical
- background asyncio tasks

Do not leave stuck PortAudio processes.

---

# First milestone

The first milestone is successful when:

1. I run `uv sync`.
2. I run `uv run python [main.py](http://main.py)`.
3. The models load locally.
4. The application listens to the microphone.
5. I say in Vietnamese:

```text
Xin chào, bạn tên là gì?

```

6. ASR prints the correct Vietnamese transcript.
7. Qwen generates a Vietnamese answer.
8. VieNeu speaks the answer through the speaker/headphones.
9. The assistant starts speaking without waiting unnecessarily for the full generated response.
10. I can interrupt it by speaking again.

Do not move to ESP32 integration until these requirements work reliably. The browser transport is now the reference session protocol; ESP32 still needs Opus framing and hardware validation.

---

# Testing

Add lightweight tests for:

- sentence/text chunker
- conversation history truncation
- state transitions
- cancellation
- config loading
- independent web sessions
- shared runtime lifecycle and concurrency gates
- binary audio framing and security helpers

Do not attempt to unit-test actual GPU model inference.

Also add a diagnostic command such as:

```bash
uv run python main.py --list-audio-devices

```

and ideally:

```bash
uv run python main.py --test-mic
uv run python main.py --test-tts
uv run python main.py --test-asr sample.wav

```

These are important for debugging hardware independently.

---

# README

Write a practical README covering:

- prerequisites
- NVIDIA/CUDA requirements
- `uv` installation
- model downloads
- environment setup
- starting the application
- selecting microphone/speaker
- common PortAudio problems
- GPU memory issues
- testing individual components
- architecture diagram
- current limitations

Do not claim a model or feature works unless it was actually integrated.

Clearly mark unfinished functionality as TODO.

---

# Future architecture

The browser transport is implemented with WebSocket + PCM. Preserve clean interfaces so later we can add:

```text
PC microphone
     ↓
PC speaker

```

with:

```text
XiaoZhi ESP32
      ↕
WebSocket + Opus
      ↕
local voice agent server

```

The future architecture will be:

```text
ESP32
  ↓
Opus/WebSocket
  ↓
VAD
  ↓
native streaming Vietnamese ASR
  ↓
Qwen3.5
  ↓
VieNeu
  ↓
Opus/WebSocket
  ↓
ESP32

```

Later we will also add:

- WebRTC AEC
- wake word
- MCP
- robot movement
- servo control
- camera
- visual reasoning
- navigation

Therefore ASR, LLM, TTS, audio transport, and orchestration must remain decoupled.

---

# Engineering instructions

Work iteratively.

First inspect the official repositories/model documentation for the currently supported APIs of:

- NVIDIA Parakeet and Nemotron streaming ASR
- Qwen3.5
- VieNeu-TTS v3 Turbo
- Silero VAD

Do not invent APIs from memory.

Pin compatible dependency versions.

Prefer official APIs over unofficial wrappers.

After implementation, actually run:

```bash
uv sync

```

and execute available unit tests/import tests.

Fix dependency/import/runtime errors rather than merely generating code that looks plausible.

If a model cannot be fully tested because weights are too large or unavailable in the current environment, validate everything around it and clearly document the exact command I should run locally.

Do not replace the requested model with a cloud API just because local setup is more difficult.

The priority order is:

1. Working local pipeline
2. Low latency
3. Reliability
4. Clean architecture
5. Advanced features

At the end, provide:

- final repository structure
- installation commands
- run commands
- known limitations
- what remains to test on the actual GPU/audio hardware
- recommended next optimization after the first working prototype
