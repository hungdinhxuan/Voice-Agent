# Local Bilingual Voice Agent

Ứng dụng hội thoại giọng nói tiếng Việt/Anh chạy hoàn toàn trên một máy:

```text
Microphone (16 kHz mono) -> Silero VAD
  -> VI: Parakeet CTC Vietnamese -> Qwen3.5 -> VieNeu-TTS (48 kHz)
  -> EN: Parakeet TDT v3        -> Qwen3.5 -> Kokoro-82M (24 kHz)
  -> Browser speaker
```

Sau khi tải đủ model, pipeline không gọi API hoặc dịch vụ inference bên ngoài. Bản đầu tiên dành cho một người dùng với headset microphone và headphones.

## Trạng thái tích hợp

- Đã tích hợp Silero VAD qua API Python chính thức và ONNX Runtime.
- Đã tích hợp `nvidia/parakeet-ctc-0.6b-Vietnamese` qua NVIDIA NeMo 2.6.
- Đã tích hợp English ASR `nvidia/parakeet-tdt-0.6b-v3` và English TTS `hexgrad/Kokoro-82M`.
- Đã tích hợp `qwen3.5:4b` qua Ollama local API, streaming NDJSON và giữ model trong VRAM.
- Transformers vẫn là backend dự phòng qua `llm.backend: transformers`.
- Đã tích hợp VieNeu-TTS v3 Turbo qua SDK `vieneu`, backend ONNX/CPU và `infer_stream` native.
- Đã có utterance segmentation, conversation history, text chunking, playback queue, latency logs và barge-in bằng câu lệnh ngắt.
- Đã có test cho chunker, history, state, cancellation, config, runtime dùng chung, session web và binary audio protocol.
- Web có switch VI/EN theo từng session. VI preload khi khởi động; English model lazy-load lần đầu chọn EN rồi được dùng chung cho các client.
- Đã có tool calling ở tầng LLM (`app/tools.py`, backend Ollama) và adapter cho thiết bị Xiaozhi ESP32 kèm device MCP.

Unit test không tải model. Smoke test ngày 10/09/2026 trên RTX 5070 Ti 16 GB đã load đồng thời hai Parakeet cùng Ollama, switch WebSocket VI→EN thành công và Kokoro sinh PCM float32 24 kHz. Bạn vẫn cần kiểm tra giọng thật và thiết bị audio trong phòng sử dụng thực tế.

## Yêu cầu

- Windows 10/11 hoặc Linux x86-64.
- Python 3.12. `uv` tự cài đúng Python theo `.python-version` nếu cần.
- NVIDIA GPU. Khuyến nghị 16 GB VRAM.
- Driver NVIDIA tương thích CUDA 12.8.
- Headset microphone và headphones. Web yêu cầu browser bật echo cancellation/noise suppression nhưng hiệu quả phụ thuộc thiết bị và browser.
- Kết nối Internet cho lần tải model đầu tiên.
- Ollama cho backend LLM mặc định. API chỉ dùng `http://127.0.0.1:11434`.

Linux cần PortAudio:

```bash
sudo apt update
sudo apt install libportaudio2 portaudio19-dev
```

Windows dùng wheel `sounddevice`; thường không cần cài PortAudio riêng. NVIDIA ưu tiên NeMo trên Linux, nên project khóa bộ dependency inference tối thiểu đã được smoke-test trên Windows; các tính năng train và CTC alignment không nằm trong phạm vi app.

## Cài đặt

Cài `uv` theo tài liệu chính thức tại <https://docs.astral.sh/uv/getting-started/installation/>. Sau đó:

```bash
uv sync
```

Khoá `uv.lock` giữ toàn bộ phiên bản đã resolve. `pyproject.toml` dùng PyTorch 2.8.0 CUDA 12.8 và giới hạn các package model theo dòng phiên bản đã kiểm tra.

## Tải model

Cài Ollama theo <https://docs.ollama.com/windows> hoặc <https://ollama.com/download>, rồi tải bản Q4_K_M 3.4 GB:

```bash
ollama pull qwen3.5:4b
ollama serve
```

Ứng dụng preload model khi khởi động và đặt `keep_alive: -1`. Kiểm tra model chạy trên GPU:

```bash
ollama ps
```

Tải Parakeet vào Hugging Face cache:

```bash
uv run hf download nvidia/parakeet-ctc-0.6b-Vietnamese parakeet-ctc-0.6b-vi.nemo
uv run hf download nvidia/parakeet-tdt-0.6b-v3 parakeet-tdt-0.6b-v3.nemo
```

Chỉ tải model Transformers LLM nếu dùng backend dự phòng:

```bash
uv run hf download Qwen/Qwen3.5-4B
```

VieNeu tải model ONNX, tokenizer và preset voice trong lần chạy TTS đầu tiên:

```bash
uv run python main.py --test-tts
```

Lệnh này phát một câu kiểm tra. Sau khi ba bước trên hoàn tất, có thể chặn truy cập mạng:

Kokoro và voice `af_heart` được tải lần đầu khi chọn **EN** trên UI. Có thể tải trước toàn bộ repository để chuẩn bị offline:

```bash
uv run hf download hexgrad/Kokoro-82M
```

`uv sync` cũng cài sẵn English G2P và spaCy `en_core_web_sm`; không cần cài `espeak-ng` thủ công trên Windows.

PowerShell:

```powershell
$env:HF_HUB_OFFLINE="1"
$env:TRANSFORMERS_OFFLINE="1"
uv run python main.py
```

Bash:

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run python main.py
```

Nếu model nằm ngoài cache chuẩn, đặt đường dẫn cục bộ vào `asr.model` hoặc `llm.model` trong `config.yaml`.

## Chạy

Liệt kê thiết bị:

```bash
uv run python main.py --list-audio-devices
```

Điền index hoặc tên thiết bị vào `audio.input_device` và `audio.output_device` trong `config.yaml`. Giá trị `null` dùng thiết bị mặc định. Hai mục này chỉ áp dụng cho CLI; chế độ `--web` dùng thiết bị của browser.

Kiểm tra từng phần:

```bash
uv run python main.py --test-mic
uv run python main.py --test-tts "Xin chào, đây là câu kiểm tra."
uv run python main.py --test-asr path/to/sample.wav
uv run python main.py --test-llm
```

Chạy hội thoại:

```bash
uv run python main.py
```

Chạy web dashboard:

```bash
uv run python main.py --web
```

Mở <http://127.0.0.1:8080>, bấm **Bật microphone** và cấp quyền audio. Chế độ `--web` thu microphone bằng `getUserMedia`, gửi PCM float32 16 kHz qua WebSocket và phát TTS bằng Web Audio ngay trên trình duyệt. Mỗi tab/client là một session hội thoại độc lập; client này không thể ghi âm, xóa history hoặc ngắt playback của client khác. Model chỉ được load một lần và inference được điều phối bằng hàng đợi dùng chung. Chế độ CLI không `--web` vẫn dùng microphone/loa trực tiếp trên máy host.

Chọn **VI · Tiếng Việt** hoặc **EN · English** ở góc trên. Khi đổi ngôn ngữ, UI dừng microphone/turn hiện tại, hiển thị trạng thái tải model và tạo history mới với system prompt tương ứng. Nếu model mới load lỗi, session giữ nguyên ngôn ngữ cũ. Hãy bật lại microphone sau khi switch hoàn tất.

Dashboard hiển thị metadata của pipeline đang chọn. `GET /api/models` expose cả catalog `vi` và `en`; `GET /api/runtime` cho biết profile nào đã load; `GET /api/sessions` gồm ngôn ngữ từng client. Trạng thái tiến trình nằm tại `GET /health`. Mặc định server chỉ bind loopback.

Khi agent đang nói, browser vẫn thu để nhận lệnh ngắt. Câu khớp `audio.interrupt_phrases` như `dừng`, `dừng lại`, `ngừng`, `thôi` được nhận không phân biệt dấu, hoa/thường và chấp nhận tiền/hậu tố lịch sự ngắn. Nội dung hội thoại dài không khớp sẽ bị bỏ qua. Nút **Ngắt phản hồi** vẫn dừng ngay lập tức.

Trước khi gọi TTS, ứng dụng chuẩn hóa quote, Markdown, URL, email, ngày tháng, đơn vị, viết tắt, emoji và ký hiệu không có ích cho phát âm; `...` được đổi thành dấu chấm. Transcript và câu trả lời hiển thị trên UI vẫn giữ nguyên nội dung model.

TTS đi từ server tới browser bằng frame nhị phân `binary-pcm-v1`: header little-endian 24 byte (`VAO1`, `turn_id`, `sample_rate`, `sequence`, `timestamp`) rồi tới PCM float32. JSON chỉ còn dùng cho control/event. Client cũ vẫn có thể đọc JSON audio trong giai đoạn chuyển đổi, nhưng server mới chỉ phát frame nhị phân.

`web.require_same_origin` quyết định server có chỉ chấp nhận WebSocket và API từ Origin trùng host/port đang phục vụ hay không. WebSocket không bị CORS chặn, nên khi tắt kiểm tra này thì một trang web bất kỳ đang mở trong cùng browser có thể tạo session và đọc transcript; mặc định của code là `true`, còn `config.yaml` trong repository đặt `false` cho máy phát triển. Nếu `web.allowed_origins` có giá trị thì danh sách đó luôn được áp dụng, bất kể cờ này. Request không có header `Origin` (curl, script cục bộ) vẫn được chấp nhận. Access token nằm trong query string được che trong access log của uvicorn.

Nếu mở ra LAN, cấu hình bắt buộc `web.access_token` tối thiểu 16 ký tự, TLS certificate/key và `web.allowed_origins`. Truy cập UI bằng `https://host:port/?token=...`; UI tự chuyển token sang WebSocket và link API. Không commit token thật vào repository.

## Cấu hình chính

Chỉnh `config.yaml`:

- `vad.threshold`: tăng nếu tiếng ồn gây false positive; giảm nếu giọng nhỏ không được nhận.
- `vad.min_speech_ms`: thời lượng tối thiểu trước khi xác nhận speech start.
- `vad.min_silence_ms`: khoảng im lặng kết thúc utterance. Giá trị nhỏ giảm latency nhưng dễ cắt câu.
- `asr.backend`: mặc định `parakeet`; đặt `qwen3` cùng model Qwen tương ứng để rollback.
- `asr.checkpoint_file`: tên checkpoint `.nemo` trong repository Parakeet.
- `audio.echo_guard_ms`: thời gian chờ sau khi loa dừng trước khi mở thu lại; mặc định `600` ms.
- `audio.allow_barge_in`: mặc định `true`. Khi agent đang nói, câu ngắt ngắn khớp `audio.interrupt_phrases` mới hủy LLM/TTS; câu khác bị bỏ qua. Nên dùng headphones nếu browser AEC không hiệu quả.
- `audio.interrupt_phrases`: danh sách câu lệnh giọng nói dùng để ngắt phản hồi đang phát.
- `llm.max_tokens`: giới hạn độ dài trả lời và thời gian giữ GPU.
- `llm.enable_thinking`: giữ `false` để tránh phát nội dung suy luận và giảm độ trễ hội thoại.
- `llm.backend`: mặc định `ollama`; đặt `transformers` để dùng backend cũ.
- `llm.context_length`: mặc định 4096, đủ cho hội thoại ngắn và giảm KV cache.
- `web.host` và `web.port`: mặc định `127.0.0.1:8080` để không mở dịch vụ ra mạng LAN.
- `web.default_language`: `vi` hoặc `en`; profile mặc định được preload khi server khởi động.
- `web.playback_prebuffer_ms`: buffer phát ban đầu, mặc định 120 ms để giảm hụt tiếng khi TTS có jitter.
- `web.max_sessions`: số client WebSocket đồng thời tối đa, mặc định 4. Client vượt quá bị đóng với mã 1013.
- `web.require_same_origin`: mặc định `true` trong code, `false` trong `config.yaml` của repository. Đặt `true` khi không còn phát triển cục bộ.
- `web.access_token`, `web.tls_certfile`, `web.tls_keyfile`, `web.allowed_origins`: bắt buộc khi `web.host` không phải loopback.
- `runtime.max_concurrent_asr/llm/tts`: số inference đồng thời trên model dùng chung; mặc định 1 để tránh vượt VRAM và giữ latency ổn định.
- `english.asr`: English Parakeet TDT v3; `checkpoint_file: null` dùng NeMo `from_pretrained`.
- `english.tts`: Kokoro-82M, voice mặc định `af_heart`, American English `lang_code: a`, output 24 kHz.
- `english.conversation` và `english.interrupt_phrases`: prompt tiếng Anh và các câu ngắt như `stop`, `please stop`.
- `tts.voice`: để `null` dùng preset đầu tiên, hoặc dùng nhãn từ `Vieneu.list_preset_voices()`.
- `tts_chunker`: cân bằng câu tự nhiên với độ trễ TTS đầu tiên.

Input cố định ở 16 kHz, mono và block 512 samples vì đây là frame hợp lệ của Silero VAD. Output mặc định 48 kHz theo VieNeu-TTS v3 Turbo.

## Log độ trễ

Mỗi turn ghi:

```text
[VAD] speech ended: 2.18 s
[ASR] latency: 143 ms
[LLM] first token: 96 ms
[LLM] generation: 412 ms
[TTS] first audio: 184 ms
[TOTAL] speech-end to first played audio: 487 ms
```

`first audio` là lúc VieNeu trả chunk đầu tiên. `first played audio` là lúc speaker worker bắt đầu ghi chunk đầu tiên vào PortAudio.

## Kiến trúc

```text
Browser A -- PCM/WebSocket -- WebVoiceSession A --┐
Browser B -- PCM/WebSocket -- WebVoiceSession B --┼-- ModelRuntime
Browser N -- PCM/WebSocket -- WebVoiceSession N --┘   ├─ ASR gate -> Parakeet
       mỗi session: VAD, history, cancellation,        ├─ LLM gate -> Ollama
       EventBroker và BrowserAudioOutput riêng         └─ TTS gate -> VieNeu

CLI microphone/speaker -- VoiceOrchestrator -- ModelRuntime riêng

ESP32 -- Opus/WebSocket -- DeviceSession ----------┘  (app/xiaozhi_adapter)
```

Các interface ASR, LLM và TTS nằm trong `app/*/base.py`. Session sở hữu trạng thái hội thoại; runtime sở hữu model và lịch inference. Web input dùng PCM float32 16 kHz, web output dùng binary PCM float32 48 kHz có sequence number; CLI giữ PortAudio cho vận hành trực tiếp trên host.

## Thiết bị Xiaozhi ESP32

`app/xiaozhi_adapter` cho phép thiết bị [`78/xiaozhi-esp32`](https://github.com/78/xiaozhi-esp32) chính thức dùng pipeline VAD/ASR/LLM/TTS của project này. Adapter chỉ là lớp protocol và transport: Xiaozhi sở hữu giao thức thiết bị, project này sở hữu phần thông minh.

Bật trong `config.yaml` rồi chạy `main.py --web` như bình thường — adapter dùng chung port với web UI:

```yaml
xiaozhi:
  enabled: true
```

Trỏ thiết bị vào `ws://<IP LAN>:8080/xiaozhi/v1/` với `version = 1`. Xem `GET /api/xiaozhi` để biết thiết bị đang kết nối, audio params đã thương lượng và trạng thái MCP.

Mở `/xiaozhi` để có console giả lập thiết bị ngay trong trình duyệt: nó nói đúng nửa client của giao thức, hiện từng frame thật trên socket kèm giải thích, tự đóng vai MCP server, và với Chrome/Edge thì mã hóa Opus từ microphone để chạy trọn một lượt hội thoại. Dùng để hiểu giao thức và test khi chưa có phần cứng.

Hỗ trợ: protocol version 1, hội thoại half-duplex, uplink Opus 16 kHz/60 ms, downlink Opus 24 kHz/60 ms có pacing, barge-in, và device MCP (thiết bị là MCP server, adapter là client) để LLM gọi tool trên thiết bị.

Chi tiết cấu hình, hợp đồng API, hành vi MCP và danh sách tính năng **chưa** hỗ trợ: [`docs/xiaozhi_adapter.md`](docs/xiaozhi_adapter.md). Giao thức được xác định từ source firmware, không từ tài liệu: [`docs/xiaozhi_protocol_research.md`](docs/xiaozhi_protocol_research.md).

Chưa test với ESP32 thật. Toàn bộ test tự động chạy qua một fake device.

## Test

```bash
uv run pytest
```

Unit test không tải model và không chạy inference GPU.

## Xử lý lỗi thường gặp

### Không mở được microphone hoặc speaker

Chạy `--list-audio-devices`, kiểm tra quyền microphone của hệ điều hành, rồi đặt đúng device index. Trên Linux, kiểm tra PipeWire/PulseAudio và gói PortAudio. Tránh dùng sample rate tùy ý; pipeline input cần 16 kHz.

### `PortAudioError: Unanticipated host error`

Đóng ứng dụng khác đang giữ thiết bị ở exclusive mode. Trên Windows, tắt exclusive mode trong Sound Control Panel nếu cần. Chọn thiết bị host API khác nếu một thiết bị xuất hiện nhiều lần.

### CUDA out of memory

Đóng ứng dụng GPU khác. Giảm `llm.max_tokens`. Bản này dùng BF16 và chưa tích hợp quantization vì backend quantization ổn định trên cả Windows và Linux cần được kiểm tra riêng. Quantization 4-bit là bước tối ưu tiếp theo nếu tổng VRAM thực tế vượt giới hạn máy.

### Ollama không kết nối

Chạy `ollama serve`, sau đó `ollama pull qwen3.5:4b`. Dùng `uv run python main.py --test-llm` để kiểm tra riêng backend.

### Model không tải khi offline

Chạy ba lệnh tải model khi còn Internet. Sau đó thử từng diagnostic với `HF_HUB_OFFLINE=1`. Hugging Face báo file nào còn thiếu trong cache.

## Giới hạn hiện tại

- VieNeu stream frame audio bên trong mỗi text chunk. Khoảng nghỉ vẫn có thể xuất hiện nếu TTS tạo chậm hơn playback.
- Web yêu cầu AEC/noise suppression/auto gain của browser và expose capability thực tế qua `GET /api/sessions`, nhưng chưa có server-side AEC hoặc kiểm chứng cho mọi thiết bị.
- Parakeet CTC hiện chạy theo utterance. Barge-in vì vậy chỉ có hiệu lực sau khi VAD kết thúc câu ngắt, chưa phải partial streaming ASR.
- English Parakeet TDT hiện cũng dùng utterance-level `transcribe`; chưa bật chunked streaming.
- Lần switch EN đầu tiên có thể mất vài phút để tải model. Sau khi cache đầy, smoke test cùng máy mất khoảng 44 giây để load English ASR + TTS; các lần switch sau trong cùng process gần như tức thời.
- Sau khi VI và EN cùng được dùng, cả hai ASR giữ trong bộ nhớ để các session chạy độc lập; cần theo dõi VRAM nếu thay LLM/model lớn hơn.
- `nvidia/nemotron-3.5-asr-streaming-0.6b` có native cache-aware streaming và hỗ trợ `vi-VN`, nhưng chưa được tích hợp/smoke-test trong project.
- Mỗi web session có một Silero VAD instance riêng để giữ state độc lập. ASR/LLM/TTS mới là model runtime dùng chung.
- WebSocket truyền PCM thô, chưa dùng Opus; bandwidth cao hơn phương án dành cho ESP32/WAN.
- Backend Ollama dùng Q4_K_M. Chất lượng có thể thấp hơn checkpoint BF16 Transformers một ít.
- Ollama `qwen3.5:4b` và Parakeet CTC 0.6B chạy đồng thời trong giới hạn VRAM 16 GB trên máy smoke test.
- Backend Transformers vẫn dùng torch fallback nếu chọn lại; smoke test cũ đo first text khoảng 1.5 giây.
- Một lệnh TTS hoặc ASR đang chạy trong worker thread không thể dừng kernel ngay lập tức. Cancellation bỏ kết quả và dọn queue; worker kết thúc phép inference đang chạy.
- Nếu browser không gửi ack playback (tab bị ẩn, AudioContext bị treo), server chờ tối đa thời lượng audio đã gửi cộng 3 giây rồi ghi log và kết thúc lượt. Phía người nghe có thể mất phần đuôi câu trả lời.
- Frame WebSocket hỏng chỉ sinh event `protocol_error` và bị bỏ qua; session không bị đóng nhưng cũng không có cơ chế yêu cầu client gửi lại.
- Silero VAD vẫn chạy đồng bộ trên event loop, mỗi session một instance; nhiều client đồng thời sẽ làm tăng jitter của toàn bộ pipeline.
- History cắt theo số lượt (`conversation.max_history_turns`), chưa theo ngân sách token của `llm.context_length`.
- Session mất history khi WebSocket ngắt; chưa có resume/persistence.
- Chưa tích hợp camera, robot movement, MCP, XiaoZhi/ESP32, wake word, RAG hoặc cloud service.

## Nguồn API chính thức

- NVIDIA Parakeet Vietnamese model card: <https://huggingface.co/nvidia/parakeet-ctc-0.6b-Vietnamese>
- NVIDIA Parakeet TDT v3 model card: <https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3>
- NVIDIA NeMo ASR: <https://docs.nvidia.com/nemo-framework/user-guide/latest/nemotoolkit/asr/intro.html>
- NVIDIA Nemotron streaming ASR: <https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b>
- Qwen3.5-4B model card: <https://huggingface.co/Qwen/Qwen3.5-4B>
- Qwen3.5:4b trên Ollama: <https://ollama.com/library/qwen3.5:4b>
- Ollama local API: <https://docs.ollama.com/api/introduction>
- VieNeu-TTS: <https://github.com/pnnbao97/VieNeu-TTS>
- Kokoro-82M: <https://huggingface.co/hexgrad/Kokoro-82M>
- Kokoro Python runtime: <https://github.com/hexgrad/kokoro>
- Silero VAD: <https://github.com/snakers4/silero-vad>

## Bước tối ưu tiếp theo

Đo p50/p95 của ASR queue, Ollama first-token và audio underrun với nhiều browser thật. Sau đó tinh chỉnh concurrency, `min_silence_ms`, `context_length`, playback prebuffer và kích thước text chunk.

Ưu tiên tích hợp native streaming ASR rồi kiểm tra barge-in bằng hội thoại dài/loa ngoài. Chỉ bắt đầu XiaoZhi/ESP32 sau khi protocol Opus, auth và session lifecycle được chốt.
