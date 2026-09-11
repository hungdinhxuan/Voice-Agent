# Điều khiển AlphaBot2 bằng giọng nói, qua trình duyệt làm cầu Bluetooth

Dành cho robot **không có WiFi**. Trình duyệt đóng vai thiết bị Xiaozhi: nó giữ
WebSocket ra server và giữ BLE xuống robot, nên robot chỉ cần Bluetooth.

```text
AlphaBot2 ──BLE UART──► trình duyệt ──WSS──► server voice agent
 (Arduino)               (Chrome/Edge)        (VAD → ASR → LLM → TTS)
                              │
                              └── đóng vai MCP server: LLM gọi tools/call
                                  thì trình duyệt dịch thành dòng lệnh BLE
```

Server không hề biết có Bluetooth. Với nó, trình duyệt **là** con robot — đúng như
thiết kế MCP của Xiaozhi: thiết bị là MCP server, backend là client.

## 1. Ràng buộc phải biết trước

**Web Bluetooth chỉ nói được BLE (GATT), không nói được Bluetooth classic SPP.**

| Module | Loại | Trình duyệt nối được? |
| --- | --- | --- |
| HC-05, HC-06 | classic SPP | **Không** |
| HM-10, AT-09, JDY-08, MLT-BT05 | BLE | Có |
| ESP32, nRF52 | BLE | Có |

Nếu bạn đang có HC-05/HC-06 thì không có cách nào cứu từ phía trình duyệt — phải đổi
sang module BLE. Đây là giới hạn của API, không phải của dự án này.

Thêm hai điều kiện của Web Bluetooth:

- Trang phải chạy trong **secure context**: `https://` hoặc `http://localhost`. Tunnel
  Cloudflare đã là HTTPS nên dùng được; `http://<IP LAN>:8080` thì **không**.
- Việc chọn thiết bị phải do người dùng bấm nút, không tự động được.

## 2. Phần cứng

Nối module BLE vào Arduino:

| Module BLE | Arduino |
| --- | --- |
| VCC | 5 V (một số module chỉ chịu 3.3 V — kiểm tra datasheet) |
| GND | GND |
| TX | `BT_RX` trong sketch (mặc định D2) |
| RX | `BT_TX` trong sketch (mặc định D3) |

> Hai chân D2/D3 trong sketch **chỉ là mặc định mình đặt ra**, chưa đối chiếu với bo
> AlphaBot2 của bạn. AlphaBot2 đã dùng nhiều chân cho cảm biến hồng ngoại, joystick,
> buzzer và siêu âm. Mở schematic Waveshare, chọn hai chân còn trống rồi sửa lại
> `BT_RX` / `BT_TX`.
>
> Tương tự, các chân motor trong sketch lấy theo demo AlphaBot2-Ar của Waveshare
> (TB6612FNG: PWMA D6, AIN1 A1, AIN2 A0, BIN1 A2, BIN2 A3, PWMB D5). Hãy xác nhận
> trước khi cấp nguồn động cơ.

## 3. Nạp sketch

`hardware/alphabot2_ble/alphabot2_ble.ino`. Giao thức là từng dòng ASCII:

| Lệnh | Ý nghĩa |
| --- | --- |
| `F<cm>` | đi tới, vd `F50` |
| `B<cm>` | lùi, vd `B30` |
| `L<deg>` | quay trái tại chỗ, vd `L90` |
| `R<deg>` | quay phải tại chỗ, vd `R180` |
| `S` | dừng ngay |
| `P` | ping, trả `OK` |

Sketch trả lại `ok F50` hoặc `err <lý do>`.

Hai điểm an toàn đã có sẵn: chuyển động là **không chặn** nên `S` cắt được giữa chừng,
và mọi lệnh bị chặn trên ở `MAX_RUN_MS` (4 giây) để robot không chạy mãi nếu trình
duyệt mất kết nối.

## 4. Hiệu chỉnh quãng đường

AlphaBot2 bản cơ bản **không có encoder**, nên cm và độ được quy ra thời gian chạy.
Chưa hiệu chỉnh thì "đi 50 cm" chỉ là con số trong lời nói, không phải ngoài đời.

1. Nạp sketch, nối BLE, mở console.
2. Bấm nút `F20` vài lần, đo quãng đường thật bằng thước.
3. Sửa `MS_PER_CM`:
   `MS_PER_CM_mới = MS_PER_CM_cũ × (quãng đường mong muốn ÷ quãng đường đo được)`
4. Làm tương tự với `L90` và `MS_PER_DEGREE`.
5. Giữ nguyên `SPEED` — đổi tốc độ là phải hiệu chỉnh lại từ đầu.

Sàn nhà, mức pin và tải đều ảnh hưởng. Đây là ước lượng theo thời gian, đừng kỳ vọng
độ chính xác của encoder.

## 5. Chạy thử

1. Bật server có `xiaozhi.enabled: true`, mở **https://\<domain\>/xiaozhi** bằng
   Chrome hoặc Edge.
2. Bấm **Kết nối robot (BLE)**, chọn module trong hộp thoại. Để `Profile UART` ở
   *tự dò* — console thử Nordic UART rồi HM-10. Module lạ thì chọn *UUID tự nhập* và
   điền `service,write`.
3. Bấm thử `F20`, `L90`, `STOP` để chắc dây và hiệu chỉnh đã đúng. Mỗi lần bấm hiện
   một dòng `ble →robot` trong luồng message.
4. Bấm **Kết nối** (WebSocket), rồi **listen start**, rồi **Bật microphone**.
5. Nói: *"Đi tới trước năm mươi phân"*, *"Rẽ trái chín mươi độ"*,
   *"Đi thẳng hai mươi phân rồi rẽ trái chín mươi độ"*.

Ở cột luồng message bạn sẽ thấy đủ chuỗi: `stt` → `mcp · tools/call` →
`ble →robot` → `mcp · reply` → `tts` — tức từ tiếng nói tới bánh xe.

## 6. Khi chưa nối BLE

Tool `self.chassis.*` **luôn** được khai với server, kể cả khi robot chưa nối. Gọi lúc
đó sẽ trả `isError: true` kèm lý do, nên LLM nói lại cho người dùng thay vì im lặng
không làm gì. Kết nối lại BLE là dùng được ngay, không cần mở lại WebSocket.

## 7. Về tham số mặc định

Mỗi tool khai `default` cho tham số (20 cm, 90 độ). Việc này có chủ đích: firmware
Xiaozhi bỏ property có `default` ra khỏi `required`, nên khi bạn nói *"tiến lên đi"*
mà không kèm số, model không bị ép bịa ra một con số.

Đây là kinh nghiệm rút từ đo thật: với schema bắt buộc tham số, `qwen3.5:4b` **tự bịa**
`distance_cm: 50` cho câu *"Tiến lên phía trước đi"*. Với robot thật thì đó là hành vi
nguy hiểm.

## 8. Giới hạn

- Chưa test với AlphaBot2 thật. Sketch và chân cắm cần bạn đối chiếu và hiệu chỉnh.
- Console gửi lệnh một chiều và không đọc phản hồi `ok`/`err` từ BLE về; nó chỉ báo
  đã ghi xong. Muốn LLM biết robot bị kẹt thì phải subscribe characteristic notify —
  chưa làm.
- Một lệnh mỗi lần. Model gọi nhiều tool liên tiếp thì các lệnh nối đuôi nhau, robot
  không xếp hàng: lệnh sau ghi đè `stopAt` của lệnh trước.
- Trình duyệt phải mở và ở gần robot trong tầm BLE (~10 m).
