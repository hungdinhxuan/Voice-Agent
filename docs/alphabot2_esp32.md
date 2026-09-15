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

### WiFi: khai báo bằng hotspot của chính robot

Không cần sửa code để đổi mạng. Robot nào **không nối được WiFi** thì tự phát
một hotspot:

| | |
| --- | --- |
| Tên | `alphabot2-setup` |
| Mật khẩu | `12345678` (đổi ở `PORTAL_SSID` / `PORTAL_PASS` trong `provision.h`) |
| Trang | tự bật, hoặc mở `http://192.168.4.1/` |

Nối điện thoại vào hotspot đó, điện thoại tự mở trang cài đặt (portal bắt mọi
truy vấn DNS về mình, nên máy tưởng là mạng cần đăng nhập). Chọn mạng trong
danh sách quét được, gõ mật khẩu, bấm lưu — robot khởi động lại và nối vào.

Đọc kết quả **bằng chính cái hotspot**: nối được thì nó tắt hẳn, còn thấy nó
hiện lại sau một phút nghĩa là sai mật khẩu, vào lại và thử lần nữa. Đèn RGB
chuyển xanh dương khi portal đang bật.

Mật khẩu lưu trong NVS và **thắng `secrets.h`** — nạp lại firmware không lặng lẽ
kéo robot về mạng cũ. Muốn xoá thì gõ `W` rồi Enter trong Serial Monitor, robot
xoá NVS và khởi động lại.

### `secrets.h`: giá trị gieo mầm cho lần chạy đầu

Vẫn cần có file này để biên dịch (**không theo dõi trong git**). Điền mạng hay
dùng vào đây thì robot nối thẳng ngay lần boot đầu, khỏi qua portal:

```bash
cp hardware/alphabot2_esp32/secrets.example.h hardware/alphabot2_esp32/secrets.h
```

```cpp
#define WIFI_SSID     "ten-wifi-cua-ban"
#define WIFI_PASS     "mat-khau-cua-ban"
```

Để nguyên giá trị mẫu cũng chạy được: nối hụt, portal bật, khai báo qua điện thoại.

### Server

Địa chỉ server ở `alphabot2_esp32.ino`:

```cpp
#define SERVER_HOST   "voiceagent.hungdx.com"
#define SERVER_PORT   443
#define SERVER_TLS    1
```

**`SERVER_HOST` không được là `127.0.0.1`.** Server mặc định bind `127.0.0.1`
nên ESP32 không với tới. Hai cách:

- **LAN (khuyến nghị)** — đổi `web.host` thành `0.0.0.0` trong config, rồi trỏ
  `SERVER_HOST` vào IP LAN của máy. Nhanh nhất, không cần TLS. Đổi lại server
  mở ra toàn mạng nội bộ và **không có xác thực**.
- **Qua tunnel (đang dùng)** — `SERVER_HOST "voiceagent.hungdx.com"`,
  `SERVER_PORT 443`, `SERVER_TLS 1`. Đi được từ bất cứ đâu nhưng thêm độ trễ, và
  uplink Opus ~13 kbps đi vòng ra internet rồi quay lại.

  Lưu ý: `beginSSL` không kèm CA thì thư viện gọi `setInsecure()`. Đường truyền
  **được mã hoá nhưng ESP32 không xác minh danh tính server** — nó sẽ tin bất cứ
  ai trả lời ở địa chỉ đó. Muốn chặt hơn thì dùng `beginSslWithCA` kèm chứng chỉ
  gốc của Cloudflare.

Đường LAN hiện **không dùng được nếu không đặt token**: `app/config.py` từ chối
bind ra ngoài loopback khi thiếu `web.access_token` và `xiaozhi.access_token`
(mỗi cái tối thiểu 16 ký tự). Chốt này có chủ đích — đừng gỡ, hãy đặt token rồi
gửi kèm trong query nếu muốn chạy LAN.

## 3. Opus, và vì sao không phải PCM thô

Sketch mã hoá **Opus 16 kHz mono, khung 60 ms** ngay trên thiết bị — đúng như
firmware Xiaozhi chính thức (`audio_service.h`: `OPUS_FRAME_DURATION_MS 60`,
`ESP_AUDIO_SAMPLE_RATE_16K`, `ESP_OPUS_BITRATE_AUTO`).

Bản đầu gửi PCM thô cho đơn giản, và nó hỏng theo cách không nhìn ra ngay: 256
kbps làm bão hoà đường ghi TLS của ESP32, bộ đệm gửi không bao giờ rỗng lại, và
mọi gói MCP xếp hàng sau audio — **3.8 giây mỗi lệnh**, trong khi vòng MCP lúc
vừa bắt tay chỉ dưới 1 giây. Không phải phần cứng yếu, mà là gửi gấp 20 lần
lượng dữ liệu cần thiết.

Đo lại sau khi bật Opus: **13 kbps**.

Chiều xuống cũng là Opus, và **ESP32 tự giải mã thành PCM** cho trình duyệt —
trang người xem chạy `http://` trên LAN nên không có WebCodecs (mục 4).

Adapter vẫn chấp nhận `format: "pcm"` cho thiết bị tự làm nào không có codec;
xem `tests/test_xiaozhi_audio.py`.

### Thư viện Opus

Không có trong index của Arduino, phải clone tay:

```bash
cd ~/Documents/Arduino/libraries
git clone --depth 1 https://github.com/pschatzmann/arduino-libopus.git
```

libopus 1.3.1 của Xiph.Org, giấy phép BSD-3-Clause, do Phil Schatzmann đóng gói.

### Hai thứ bắt buộc, thiếu là hỏng

```cpp
SET_LOOP_TASK_STACK_SIZE(32 * 1024);
```

`opus_encode` dùng rất nhiều stack; task `loop()` mặc định chỉ 8 KB nên crash
ngay lần mã hoá đầu: `Guru Meditation Error ... Stack canary watchpoint`.

Và **phải mã hoá một khung im lặng trước khi WiFi khởi động**. libopus được build
với `NONTHREADSAFE_PSEUDOSTACK`: lần `ALLOC` đầu tiên nó `malloc` một khối **liền
mạch 60 KB** làm vùng nháp dùng chung. Để đến sau khi WiFi và TLS chạy thì heap
đã bị băm nhỏ — còn 170 KB trống nhưng không mảnh nào đủ 60 KB — malloc trả NULL,
mọi `ALLOC` trả NULL, và `assert(pcm_buf != NULL)` reset thiết bị giữa lúc bắt tay.

Nhìn từ server, cả hai lỗi này **trông y hệt lỗi mạng**: thiết bị nối vào, im
lặng, `initialize` hết giờ sau 10 giây, rồi lặp lại.

## 4. Trang người xem

ESP32 tự chạy HTTP ở cổng 80 và WebSocket ở 81. Mở **http://alphabot2.local/**
hoặc IP của robot bằng điện thoại trong cùng mạng.

Trang hiện câu bạn nói, câu robot trả lời, và các động tác nó thực hiện. Bấm
**Bật tiếng** thì điện thoại thành loa cho robot — ESP32 chuyển tiếp nguyên khung
audio, trình duyệt phát bằng Web Audio, ESP32 không đụng tới codec nào.

Phải bấm mới có tiếng: trình duyệt di động không cho phát âm thanh nếu chưa có
thao tác của người dùng.

**Vì sao ESP32 giải mã chứ không chuyển tiếp thẳng.** Trang này chạy ở `http://`
trên IP LAN, nên trình duyệt **không coi đó là secure context** — và
`AudioDecoder` (WebCodecs) chỉ tồn tại trong secure context. Không sửa được từ
phía trình duyệt: không phải HTTPS thì không có WebCodecs, hết.

Nên ESP32 giải mã Opus thành PCM rồi mới đẩy sang trình duyệt. Đường truyền ra
internet vẫn gọn, còn chặng LAN thì thô nhưng ngắn và rẻ.

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
FQBN="esp32:esp32:esp32s3:PartitionScheme=huge_app,CDCOnBoot=cdc"
"$CLI" compile --fqbn "$FQBN" hardware/alphabot2_esp32
"$CLI" upload -p COMx --fqbn "$FQBN" hardware/alphabot2_esp32
```

Thư viện cần: `Adafruit NeoPixel`, `ArduinoJson`, `WebSockets` (Links2004), và
`arduino-libopus` phải clone tay (mục 3).

**Dùng `PartitionScheme=huge_app`.** Với phân vùng mặc định sketch chiếm **89%**
flash — chạy được nhưng không còn chỗ để thêm gì. Đổi sang `huge_app` thì xuống
**37%**. Trong Arduino IDE: Tools → Partition Scheme → *Huge APP (3MB No OTA)*.

**Dùng `CDCOnBoot=cdc`.** ESP32-S3 mặc định tắt USB CDC, và khi đó `Serial.print`
đi ra chân UART chứ không ra cổng USB — Serial Monitor sẽ **im hoàn toàn** dù
firmware chạy bình thường. Trong Arduino IDE: Tools → USB CDC On Boot → *Enabled*.

Muốn xem thư viện WebSocket nói gì khi bắt tay, thêm:

```bash
--build-property "compiler.cpp.extra_flags=-DDEBUG_ESP_PORT=Serial"
```

## 8b. Kiểm chứng giao thức mà không cần phần cứng

`hardware/alphabot2_esp32/sim_device.py` gửi **đúng những message mà firmware
gửi** — từng chuỗi JSON được chép từ `remote.h`, audio đi lên là PCM thô như
`pumpUplink()` đẩy. Chạy được nó nghĩa là giao thức khớp với adapter.

```bash
uv run --with soundfile --with websockets python \
    hardware/alphabot2_esp32/sim_device.py ws://127.0.0.1:8080 loi-noi.wav
```

File wav phải là mono 16 kHz PCM_16. Sửa giao thức trong `remote.h` thì sửa ở
đây trước, chạy thấy xanh rồi hãy sửa firmware theo.

Kết quả đo với câu *"Rẽ phải chín mươi độ rồi đi thẳng hai mươi phân"*:

```
stt  -> 'Rẽ phải chín mươi độ rồi đi thẳng hai mươi phân.'
lệnh xuống bánh xe : ['R90', 'F20']
trả lời            : 'Đã rẽ phải chín mươi độ và đi thẳng hai mươi phân.'
```

## 8c. Nói vào mic của trình duyệt thay vì mic robot

Trang do robot phục vụ chạy ở `http://` trên IP LAN, nên **không xin được mic**:
`getUserMedia` cũng chỉ tồn tại trong secure context, y hệt `AudioDecoder`. Không
sửa được từ phía trình duyệt.

Đường đi được là mượn tool: console tại `https://<domain>/xiaozhi` có mic vì nó
chạy trên HTTPS, và nó mượn tool của robot để lệnh đi xuống đúng chỗ.

1. Mở `https://<domain>/xiaozhi`, điền `alphabot2` vào ô **Điều khiển robot**.
2. Bấm **Kết nối** → **listen start** → **Bật microphone**.
3. Nói vào mic máy đang mở trang. Tool call đi thẳng xuống robot ESP32.

Robot phải đang nối sẵn và đã xong bắt tay MCP, nếu không console dùng tool giả
lập của chính nó. `/api/xiaozhi` có trường `borrowed_from` cho biết phiên nào
đang mượn của ai.

Mic của robot vẫn chạy song song. Muốn im thì tắt `ENABLE_REMOTE`, hoặc bịt mic.

## 9. Giới hạn

- **Chưa chạy thử trên phần cứng thật.** Sketch compile được, và giao thức đã
  được kiểm chứng bằng `sim_device.py` với server thật (xem mục 8b) — bắt tay,
  PCM uplink, ASR, MCP tool list, chuỗi hai lệnh, TTS đều thông. Nhưng **code
  C++ chưa từng chạy**: đọc I2S, dựng JSON bằng ArduinoJson, HTTP/WS server,
  chuyển chế độ — tất cả mới chỉ được biên dịch. Coi như chưa kiểm chứng cho tới
  khi bạn nạp và thấy nó hoạt động.
- Lần đầu chạy hãy mở Serial Monitor ở 115200 — mọi bước bắt tay đều in ra đó.
- Thư viện WebSocket mặc định gửi kèm header `Origin: file://`, và chốt chặn
  Origin của adapter trả **403** cho nó. Firmware gọi `setExtraHeaders("")` để bỏ
  hẳn header đó — thiết bị Xiaozhi thật không gửi Origin bao giờ. Sửa ở firmware
  chứ không nới chốt chặn, vì chốt đó đang ngăn trình duyệt ở site khác mở trộm
  WebSocket.
- Chỉ một session mỗi robot. Nhiều người xem cùng lúc thì được.
- `MIC_GAIN` đang là 3.0, lấy theo chế độ local. ASR có thể cần giá trị khác;
  nếu nhận dạng kém, đây là số đầu tiên nên chỉnh.
