# Local Vietnamese Voice Agent

Ứng dụng hội thoại giọng nói tiếng Việt chạy hoàn toàn trên một máy:

```text
Microphone (16 kHz mono)
  -> Silero VAD (ONNX/CPU)
  -> Qwen3-ASR-0.6B (GPU)
  -> Qwen3.5-4B Q4_K_M qua Ollama, streaming text (GPU)
  -> sentence chunker
  -> VieNeu-TTS v3 Turbo (ONNX/CPU)
  -> playback queue (48 kHz mono)
  -> Speaker
```

Sau khi tải đủ model, pipeline không gọi API hoặc dịch vụ inference bên ngoài. Bản đầu tiên dành cho một người dùng với headset microphone và headphones.

## Trạng thái tích hợp

- Đã tích hợp Silero VAD qua API Python chính thức và ONNX Runtime.
- Đã tích hợp `Qwen/Qwen3-ASR-0.6B-hf` qua Transformers native `apply_transcription_request`.
- Đã tích hợp `qwen3.5:4b` qua Ollama local API, streaming NDJSON và giữ model trong VRAM.
- Transformers vẫn là backend dự phòng qua `llm.backend: transformers`.
- Đã tích hợp VieNeu-TTS v3 Turbo qua SDK `vieneu`, backend ONNX/CPU và `infer_stream` native.
- Đã có utterance segmentation, conversation history, text chunking, playback queue, latency logs và chế độ half-duplex chống trợ lý nghe lại chính loa.
- Đã có test cho chunker, history, state, cancellation và config.
- Đã có web dashboard local hiển thị state, transcript, phản hồi streaming, latency và log.

Unit test không tải model. Smoke test ngày 09/09/2026 trên RTX 5070 Ti 16 GB đã xác nhận ASR, TTS, Ollama và backend Transformers cũ. Bạn vẫn cần kiểm tra giọng thật và thiết bị audio trong phòng sử dụng thực tế.

## Yêu cầu

- Windows 10/11 hoặc Linux x86-64.
- Python 3.12. `uv` tự cài đúng Python theo `.python-version` nếu cần.
- NVIDIA GPU. Khuyến nghị 16 GB VRAM.
- Driver NVIDIA tương thích CUDA 12.8.
- Headset microphone và headphones. Chưa có acoustic echo cancellation.
- Kết nối Internet cho lần tải model đầu tiên.
- Ollama cho backend LLM mặc định. API chỉ dùng `http://127.0.0.1:11434`.

Linux cần PortAudio:

```bash
sudo apt update
sudo apt install libportaudio2 portaudio19-dev
```

Windows dùng wheel `sounddevice`; thường không cần cài PortAudio riêng.

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

Tải Qwen3-ASR vào Hugging Face cache:

```bash
uv run hf download Qwen/Qwen3-ASR-0.6B-hf
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

Điền index hoặc tên thiết bị vào `audio.input_device` và `audio.output_device` trong `config.yaml`. Giá trị `null` dùng thiết bị mặc định.

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

Mở <http://127.0.0.1:8080>. Dashboard dùng WebSocket local để nhận transcript, token, state và latency. Microphone và speaker vẫn chạy trực tiếp trên máy chủ Python; trình duyệt không thu hoặc phát audio.

Mặc định ứng dụng chỉ thu khi trợ lý đã nói xong. Trong lúc `PROCESSING` hoặc `SPEAKING`, frame microphone bị bỏ qua; sau playback có thêm khoảng chống âm vang. Dùng nút **Ngắt phản hồi** nếu muốn dừng trợ lý ngay.

## Cấu hình chính

Chỉnh `config.yaml`:

- `vad.threshold`: tăng nếu tiếng ồn gây false positive; giảm nếu giọng nhỏ không được nhận.
- `vad.min_speech_ms`: thời lượng tối thiểu trước khi xác nhận speech start.
- `vad.min_silence_ms`: khoảng im lặng kết thúc utterance. Giá trị nhỏ giảm latency nhưng dễ cắt câu.
- `audio.echo_guard_ms`: thời gian chờ sau khi loa dừng trước khi mở thu lại; mặc định `600` ms.
- `audio.allow_barge_in`: mặc định `false`. Chỉ bật khi dùng headphones hoặc đã có acoustic echo cancellation.
- `llm.max_tokens`: giới hạn độ dài trả lời và thời gian giữ GPU.
- `llm.enable_thinking`: giữ `false` để tránh phát nội dung suy luận và giảm độ trễ hội thoại.
- `llm.backend`: mặc định `ollama`; đặt `transformers` để dùng backend cũ.
- `llm.context_length`: mặc định 4096, đủ cho hội thoại ngắn và giảm KV cache.
- `web.host` và `web.port`: mặc định `127.0.0.1:8080` để không mở dịch vụ ra mạng LAN.
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
MicrophoneInput
      |
SileroVADSegmenter ---- speech start ---- cancel current TurnCancellation
      |
speech end
      |
VoiceOrchestrator
      |---- Qwen3ASRService
      |---- ConversationHistory
      |---- Qwen35Service -------- token stream
      |                                  |
      |                         StreamingTextChunker
      |                                  |
      |                            asyncio text queue
      |                                  |
      |---- VieNeuTTSService ------ audio chunks
                                         |
                                  SpeakerOutput queue

VoiceOrchestrator ---- EventBroker ---- WebSocket ---- local web dashboard
```

Các interface ASR, LLM và TTS nằm trong `app/*/base.py`. Audio transport chỉ tiếp xúc với orchestrator. Cấu trúc này cho phép thay microphone/speaker bằng WebSocket + Opus sau milestone 1 mà không đổi model service.

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
- Chưa có acoustic echo cancellation. Chế độ half-duplex mặc định tránh speaker kích hoạt VAD khi dùng loa ngoài.
- ASR chạy theo utterance, chưa dùng streaming ASR.
- Backend Ollama dùng Q4_K_M. Chất lượng có thể thấp hơn checkpoint BF16 Transformers một ít.
- Chưa benchmark Ollama trên máy hiện tại vì Ollama chưa được cài trong môi trường phát triển.
- Web UI hiện là dashboard điều khiển pipeline audio của máy chủ. Chưa dùng microphone/audio playback của trình duyệt.
- Backend Transformers vẫn dùng torch fallback nếu chọn lại; smoke test cũ đo first text khoảng 1.5 giây.
- Một lệnh TTS hoặc ASR đang chạy trong worker thread không thể dừng kernel ngay lập tức. Cancellation bỏ kết quả và dọn queue; worker kết thúc phép inference đang chạy.
- Chưa tích hợp camera, robot movement, MCP, XiaoZhi, ESP32, wake word, RAG, web UI hoặc cloud service.

## Nguồn API chính thức

- Qwen3-ASR model card: <https://huggingface.co/Qwen/Qwen3-ASR-0.6B-hf>
- Qwen3.5-4B model card: <https://huggingface.co/Qwen/Qwen3.5-4B>
- Qwen3.5:4b trên Ollama: <https://ollama.com/library/qwen3.5:4b>
- Ollama local API: <https://docs.ollama.com/api/introduction>
- VieNeu-TTS: <https://github.com/pnnbao97/VieNeu-TTS>
- Silero VAD: <https://github.com/snakers4/silero-vad>

## Bước tối ưu tiếp theo

Đo Ollama first-token latency trên máy thật trước. Sau đó tinh chỉnh `min_silence_ms`, `context_length` và kích thước text chunk theo log dashboard.

Chỉ bắt đầu XiaoZhi/ESP32 sau khi pipeline headset chạy ổn định và barge-in đã được kiểm tra bằng hội thoại dài.
