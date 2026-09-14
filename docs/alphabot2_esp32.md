# AlphaBot2 + ESP32-S3: hai chế độ điều khiển

Robot có mic I2S nhưng **không có loa**. Sketch chạy một trong hai chế độ và tự
chuyển giữa chúng.

```text
REMOTE                                        LOCAL
──────                                        ─────
mic ──PCM 16k──► server (VAD→ASR→LLM→TTS)     mic ──► so ngưỡng ──► nhích tới
         ◄──MCP tools/call──                         đổi màu RGB
banh xe quay theo câu nói                     không cần mạng
         │
         └── TTS ──► trang web ESP32 tự host ──► điện thoại người xem
```

## 1. Chuyển chế độ

Tự động, không có nút bấm:

| Tình huống | Chế độ |
| --- | --- |
| Boot, WiFi nối được | REMOTE |
| Boot, WiFi hỏng | LOCAL |
| Đang REMOTE mà mất WiFi | rơi về LOCAL, dừng bánh xe ngay |
| Đang LOCAL, WiFi có lại | quay lại REMOTE (thử mỗi 20 giây) |

Mất mạng thì robot vẫn dùng được, chỉ kém thông minh đi. Đặt `ENABLE_REMOTE 0`
nếu muốn khoá hẳn ở hành vi gốc.

## 2. Sửa trước khi nạp

Trong `alphabot2_esp32.ino`:

```cpp
#define WIFI_SSID     "doi-ten-wifi"
#define WIFI_PASS     "doi-mat-khau"
#define SERVER_HOST   "192.168.0.204"   // IP LAN của máy chạy server
#define SERVER_PORT   8080
#define SERVER_TLS    0
```

**`SERVER_HOST` không được là `127.0.0.1`.** Server mặc định bind `127.0.0.1`
nên ESP32 không với tới. Hai cách:

- **LAN (khuyến nghị)** — đổi `web.host` thành `0.0.0.0` trong config, rồi trỏ
  `SERVER_HOST` vào IP LAN của máy. Nhanh nhất, không cần TLS. Đổi lại server
  mở ra toàn mạng nội bộ và **không có xác thực**.
- **Qua tunnel** — `SERVER_HOST "voiceagent.hungdx.com"`, `SERVER_PORT 443`,
  `SERVER_TLS 1`. Đi được từ bất cứ đâu nhưng thêm độ trễ, và uplink PCM ~256
  kbps đi vòng ra internet rồi quay lại.

## 3. Vì sao uplink là PCM chứ không phải Opus

Firmware Xiaozhi chính thức mã hoá Opus trên thiết bị. Sketch này gửi **PCM thô
16-bit little-endian 16 kHz**, và adapter chấp nhận `format: "pcm"` riêng cho
thiết bị tự làm.

Đổi lại: ~256 kbps thay vì ~24 kbps. Trong LAN thì không đáng kể. **Đừng chạy
đường này qua kết nối tính theo dung lượng.**

Muốn dùng Opus thì cài `arduino-libopus`, mã hoá trước khi `sendBIN`, và đổi
`format` về `"opus"` — adapter không cần sửa gì.

## 4. Trang người xem

ESP32 tự chạy HTTP ở cổng 80 và WebSocket ở 81. Mở **http://alphabot2.local/**
hoặc IP của robot bằng điện thoại trong cùng mạng.

Trang hiện câu bạn nói, câu robot trả lời, và các động tác nó thực hiện. Bấm
**Bật tiếng** thì điện thoại thành loa cho robot — ESP32 chuyển tiếp nguyên gói
Opus, trình duyệt giải mã bằng WebCodecs, ESP32 không đụng tới codec nào.

Phải bấm mới có tiếng: trình duyệt di động không cho phát âm thanh nếu chưa có
thao tác của người dùng.

## 5. Vì sao mic tắt trong lúc server nói

Robot không có loa, nhưng **điện thoại người xem thì có**, và nó có thể nằm ngay
cạnh mic. Nên khi nhận `tts start` thiết bị ngừng đẩy tiếng lên, và chỉ mở lại
sau `tts stop`. Không làm vậy thì robot nghe chính câu trả lời rồi tự kích hoạt
lượt hội thoại mới.

Đây cũng là lý do chế độ REMOTE không cần AEC.

## 6. Lệnh chuyển động

Giống hệt bản Arduino Uno (`hardware/alphabot2_ble`), cố ý giữ nguyên để tài
liệu và thói quen gỡ lỗi dùng chung được:

| Tool MCP | Lệnh nội bộ |
| --- | --- |
| `self.chassis.forward` | `F<cm>` |
| `self.chassis.backward` | `B<cm>` |
| `self.chassis.turn_left` | `L<deg>` |
| `self.chassis.turn_right` | `R<deg>` |
| `self.chassis.stop` | `S` |

**Lệnh được xếp hàng.** Câu như *"rẽ phải rồi đi thẳng hai mươi phân"* làm model
gọi hai tool cách nhau vài mili giây. Sketch giữ tối đa 6 động tác, chạy lần
lượt, nghỉ 150 ms giữa hai động tác. `stop` xoá cả hàng đợi.

Mất WebSocket thì bánh xe dừng ngay — robot không được chạy tiếp khi không còn
ai điều khiển.

## 7. Hiệu chỉnh quãng đường

Không có encoder, cm và độ quy ra thời gian chạy. Trong `motion.h`:

```cpp
const float MS_PER_CM     = 30.0;
const float MS_PER_DEGREE = 6.0;
const uint8_t MOVE_SPEED  = 110;
```

**Ba số này chưa được đo trên robot thật.** Hướng thì đúng, quãng đường thì
chưa. Đo bằng cách bảo robot đi 100 cm, lấy thước đo thật, rồi:

```
MS_PER_CM_mới = MS_PER_CM_cũ × (100 ÷ quãng đường đo được)
```

Đổi `MOVE_SPEED` là phải đo lại từ đầu.

## 8. Nạp

```bash
CLI="/c/Program Files/Arduino IDE/resources/app/lib/backend/resources/arduino-cli.exe"
FQBN="esp32:esp32:esp32s3:PartitionScheme=huge_app"
"$CLI" compile --fqbn "$FQBN" hardware/alphabot2_esp32
"$CLI" upload -p COMx --fqbn "$FQBN" hardware/alphabot2_esp32
```

Thư viện cần: `Adafruit NeoPixel`, `ArduinoJson`, `WebSockets` (Links2004).

**Dùng `PartitionScheme=huge_app`.** Với phân vùng mặc định sketch chiếm **89%**
flash — chạy được nhưng không còn chỗ để thêm gì. Đổi sang `huge_app` thì xuống
**37%**. Trong Arduino IDE: Tools → Partition Scheme → *Huge APP (3MB No OTA)*.

## 8b. Kiểm chứng giao thức mà không cần phần cứng

`hardware/alphabot2_esp32/sim_device.py` gửi **đúng những message mà firmware
gửi** — từng chuỗi JSON được chép từ `remote.h`, audio đi lên là PCM thô như
`pumpUplink()` đẩy. Chạy được nó nghĩa là giao thức khớp với adapter.

```bash
uv run --with soundfile --with websockets python \n    hardware/alphabot2_esp32/sim_device.py ws://127.0.0.1:8080 loi-noi.wav
```

File wav phải là mono 16 kHz PCM_16. Sửa giao thức trong `remote.h` thì sửa ở
đây trước, chạy thấy xanh rồi hãy sửa firmware theo.

Kết quả đo với câu *"Rẽ phải chín mươi độ rồi đi thẳng hai mươi phân"*:

```
stt  -> 'Rẽ phải chín mươi độ rồi đi thẳng hai mươi phân.'
lệnh xuống bánh xe : ['R90', 'F20']
trả lời            : 'Đã rẽ phải chín mươi độ và đi thẳng hai mươi phân.'
```

## 9. Giới hạn

- **Chưa chạy thử trên phần cứng thật.** Sketch compile được, và giao thức đã
  được kiểm chứng bằng `sim_device.py` với server thật (xem mục 8b) — bắt tay,
  PCM uplink, ASR, MCP tool list, chuỗi hai lệnh, TTS đều thông. Nhưng **code
  C++ chưa từng chạy**: đọc I2S, dựng JSON bằng ArduinoJson, HTTP/WS server,
  chuyển chế độ — tất cả mới chỉ được biên dịch. Coi như chưa kiểm chứng cho tới
  khi bạn nạp và thấy nó hoạt động.
- Lần đầu chạy hãy mở Serial Monitor ở 115200 — mọi bước bắt tay đều in ra đó.
- Chỉ một session mỗi robot. Nhiều người xem cùng lúc thì được.
- `MIC_GAIN` đang là 3.0, lấy theo chế độ local. ASR có thể cần giá trị khác;
  nếu nhận dạng kém, đây là số đầu tiên nên chỉnh.
