# Local Vietnamese Voice Agent

Ứng dụng hội thoại giọng nói tiếng Việt chạy hoàn toàn trên một máy:

```text
Microphone (16 kHz mono)
  -> Silero VAD (ONNX/CPU)
  -> NVIDIA Parakeet CTC 0.6B Vietnamese (GPU)
  -> Qwen3.5-4B Q4_K_M qua Ollama, streaming text (GPU)
  -> sentence chunker
  -> VieNeu-TTS v3 Turbo (ONNX/CPU)
  -> playback queue (48 kHz mono)
  -> Speaker
```

Sau khi tải đủ model, pipeline không gọi API hoặc dịch vụ inference bên ngoài. Bản đầu tiên dành cho một người dùng với headset microphone và headphones.

## Trạng thái tích hợp

- Đã tích hợp Silero VAD qua API Python chính thức và ONNX Runtime.
- Đã tích hợp `nvidia/parakeet-ctc-0.6b-Vietnamese` qua NVIDIA NeMo 2.6.
- Đã tích hợp `qwen3.5:4b` qua Ollama local API, streaming NDJSON và giữ model trong VRAM.
- Transformers vẫn là backend dự phòng qua `llm.backend: transformers`.
- Đã tích hợp VieNeu-TTS v3 Turbo qua SDK `vieneu`, backend ONNX/CPU và `infer_stream` native.
- Đã có utterance segmentation, conversation history, text chunking, playback queue, latency logs và barge-in bằng câu lệnh ngắt.
- Đã có test cho chunker, history, state, cancellation, config, runtime dùng chung, session web và binary audio protocol.
- Đã có web voice client dùng microphone/loa của trình duyệt. Mỗi kết nối có VAD, history và playback độc lập; ASR/LLM/TTS dùng chung model runtime có giới hạn concurrency.

Unit test không tải model. Smoke test ngày 10/09/2026 trên RTX 5070 Ti 16 GB cho thấy Parakeet nhận đúng câu tiếng Việt mẫu dài 2,4 giây trong khoảng 0,39–0,55 giây. Full pipeline cùng TTS và Ollama cũng đã khởi động thành công. Bạn vẫn cần kiểm tra giọng thật và thiết bị audio trong phòng sử dụng thực tế.

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

Dashboard hiển thị metadata của toàn bộ pipeline: ASR, LLM, TTS và VAD. API JSON được expose tại `GET /api/models`, `GET /api/runtime`, `GET /api/sessions`; trạng thái tiến trình nằm tại `GET /health`. Mặc định server chỉ bind loopback.

Khi agent đang nói, browser vẫn thu để nhận lệnh ngắt. Câu khớp `audio.interrupt_phrases` như `dừng`, `dừng lại`, `ngừng`, `thôi` được nhận không phân biệt dấu, hoa/thường và chấp nhận tiền/hậu tố lịch sự ngắn. Nội dung hội thoại dài không khớp sẽ bị bỏ qua. Nút **Ngắt phản hồi** vẫn dừng ngay lập tức.

Trước khi gọi TTS, ứng dụng chuẩn hóa quote, Markdown, URL, email, ngày tháng, đơn vị, viết tắt, emoji và ký hiệu không có ích cho phát âm; `...` được đổi thành dấu chấm. Transcript và câu trả lời hiển thị trên UI vẫn giữ nguyên nội dung model.

TTS đi từ server tới browser bằng frame nhị phân `binary-pcm-v1`: header little-endian 24 byte (`VAO1`, `turn_id`, `sample_rate`, `sequence`, `timestamp`) rồi tới PCM float32. JSON chỉ còn dùng cho control/event. Client cũ vẫn có thể đọc JSON audio trong giai đoạn chuyển đổi, nhưng server mới chỉ phát frame nhị phân.

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
- `web.playback_prebuffer_ms`: buffer phát ban đầu, mặc định 120 ms để giảm hụt tiếng khi TTS có jitter.
- `web.access_token`, `web.tls_certfile`, `web.tls_keyfile`, `web.allowed_origins`: bắt buộc khi `web.host` không phải loopback.
- `runtime.max_concurrent_asr/llm/tts`: số inference đồng thời trên model dùng chung; mặc định 1 để tránh vượt VRAM và giữ latency ổn định.
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
```

Các interface ASR, LLM và TTS nằm trong `app/*/base.py`. Session sở hữu trạng thái hội thoại; runtime sở hữu model và lịch inference. Web input dùng PCM float32 16 kHz, web output dùng binary PCM float32 48 kHz có sequence number; CLI giữ PortAudio cho vận hành trực tiếp trên host.

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
- `nvidia/nemotron-3.5-asr-streaming-0.6b` có native cache-aware streaming và hỗ trợ `vi-VN`, nhưng chưa được tích hợp/smoke-test trong project.
- Mỗi web session có một Silero VAD instance riêng để giữ state độc lập. ASR/LLM/TTS mới là model runtime dùng chung.
- WebSocket truyền PCM thô, chưa dùng Opus; bandwidth cao hơn phương án dành cho ESP32/WAN.
- Backend Ollama dùng Q4_K_M. Chất lượng có thể thấp hơn checkpoint BF16 Transformers một ít.
- Ollama `qwen3.5:4b` và Parakeet CTC 0.6B chạy đồng thời trong giới hạn VRAM 16 GB trên máy smoke test.
- Backend Transformers vẫn dùng torch fallback nếu chọn lại; smoke test cũ đo first text khoảng 1.5 giây.
- Một lệnh TTS hoặc ASR đang chạy trong worker thread không thể dừng kernel ngay lập tức. Cancellation bỏ kết quả và dọn queue; worker kết thúc phép inference đang chạy.
- Session mất history khi WebSocket ngắt; chưa có resume/persistence.
- Chưa tích hợp camera, robot movement, MCP, XiaoZhi/ESP32, wake word, RAG hoặc cloud service.

## Nguồn API chính thức

- NVIDIA Parakeet Vietnamese model card: <https://huggingface.co/nvidia/parakeet-ctc-0.6b-Vietnamese>
- NVIDIA NeMo ASR: <https://docs.nvidia.com/nemo-framework/user-guide/latest/nemotoolkit/asr/intro.html>
- NVIDIA Nemotron streaming ASR: <https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b>
- Qwen3.5-4B model card: <https://huggingface.co/Qwen/Qwen3.5-4B>
- Qwen3.5:4b trên Ollama: <https://ollama.com/library/qwen3.5:4b>
- Ollama local API: <https://docs.ollama.com/api/introduction>
- VieNeu-TTS: <https://github.com/pnnbao97/VieNeu-TTS>
- Silero VAD: <https://github.com/snakers4/silero-vad>

## Bước tối ưu tiếp theo

Đo p50/p95 của ASR queue, Ollama first-token và audio underrun với nhiều browser thật. Sau đó tinh chỉnh concurrency, `min_silence_ms`, `context_length`, playback prebuffer và kích thước text chunk.

Ưu tiên tích hợp native streaming ASR rồi kiểm tra barge-in bằng hội thoại dài/loa ngoài. Chỉ bắt đầu XiaoZhi/ESP32 sau khi protocol Opus, auth và session lifecycle được chốt.
