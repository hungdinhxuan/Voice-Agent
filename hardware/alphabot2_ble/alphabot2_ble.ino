// AlphaBot2 + BLE UART -> nhận lệnh từ Xiaozhi Device Console trong trình duyệt.
//
// Giao thức: mỗi lệnh là một dòng ASCII kết thúc bằng '\n'.
//   F<cm>   đi tới       vd F50
//   B<cm>   lùi          vd B30
//   L<deg>  quay trái    vd L90
//   R<deg>  quay phải    vd R180
//   S       dừng ngay
//   P       ping, trả "OK"
// Trả về: "ok F50", "err <lý do>". Console hiện nguyên văn.
//
// ĐỌC TRƯỚC KHI NẠP
//   1. Chân motor bên dưới lấy theo demo AlphaBot2-Ar của Waveshare. HÃY ĐỐI CHIẾU
//      với schematic bo của bạn trước khi cấp nguồn động cơ.
//   2. BT_RX/BT_TX đang để D10/D11. Hãy xác nhận hai chân này còn trống trên bo
//      của bạn, AlphaBot2 đã dùng nhiều chân cho cảm biến.
//   3. AlphaBot2 bản cơ bản KHÔNG có encoder, nên cm và độ được quy ra thời gian
//      chạy. Phải hiệu chỉnh MS_PER_CM và MS_PER_DEGREE thì số mới đúng thực tế.
//      Xem phần hiệu chỉnh trong docs/alphabot2_bridge.md.

#include <SoftwareSerial.h>

// --- chân motor (TB6612FNG trên AlphaBot2-Ar) -------------------------------
const uint8_t PWMA = 6;   // tốc độ motor trái
const uint8_t AIN1 = A1;
const uint8_t AIN2 = A0;
const uint8_t BIN1 = A2;
const uint8_t BIN2 = A3;
const uint8_t PWMB = 5;   // tốc độ motor phải

// --- module BLE (HM-10 / AT-09 / JDY-08) ------------------------------------
const uint8_t BT_RX = 10;  // nối tới TX của module
const uint8_t BT_TX = 11;  // nối tới RX của module
SoftwareSerial bt(BT_RX, BT_TX);

// Đo thực tế trên AlphaBot2: motor phải đấu ngược cực so với motor trái, nên
// cùng một giá trị dương lại làm hai bánh quay ngược chiều nhau. Đảo ở đây thay
// vì đảo dây, và để ai có bo đấu khác chỉ cần sửa hai dòng này.
const bool INVERT_LEFT  = false;
const bool INVERT_RIGHT = true;

// --- hiệu chỉnh: đo rồi sửa hai số này --------------------------------------
const float MS_PER_CM = 22.0;      // thời gian chạy thẳng 1 cm, ở SPEED bên dưới
const float MS_PER_DEGREE = 4.6;   // thời gian quay tại chỗ 1 độ
const uint8_t SPEED = 120;         // 0..255, phải giống lúc hiệu chỉnh

// Robot không được chạy mãi nếu console mất kết nối giữa chừng.
const unsigned long MAX_RUN_MS = 4000;

unsigned long stopAt = 0;          // 0 nghĩa là đang đứng yên

void handle(const char* command);
void report();

// Đọc từng dòng, mỗi cổng một bộ đệm riêng để hai nguồn không trộn vào nhau.
struct LineReader {
  char buf[24];
  uint8_t len = 0;

  void pump(Stream& port, unsigned long& counter) {
    while (port.available()) {
      char c = port.read();
      if (c == '\r') continue;
      if (c == '\n') { buf[len] = '\0'; counter++; handle(buf); len = 0; continue; }
      if (len < sizeof(buf) - 1) buf[len++] = c;
    }
  }
};

LineReader fromBle;
LineReader fromUsb;

unsigned long bleLines = 0;        // đếm dòng nhận từ BLE, để phát hiện nhiễu
unsigned long usbLines = 0;

void setup() {
  // Mảng tường minh: avr-libstdc++ không có <initializer_list> cho range-for.
  const uint8_t pins[] = {PWMA, AIN1, AIN2, BIN1, BIN2, PWMB};
  for (uint8_t i = 0; i < sizeof(pins); i++) pinMode(pins[i], OUTPUT);
  halt();
  // Chưa cắm module BLE thì chân RX thả nổi, SoftwareSerial sẽ đọc ra nhiễu và
  // có thể dựng thành một "lệnh" giả làm robot chạy tiếp. Pull-up giữ mức idle.
  pinMode(BT_RX, INPUT_PULLUP);
  Serial.begin(115200);            // debug, và nhận lệnh khi bench test
  bt.begin(9600);                  // HM-10 mặc định 9600
  reply("ready");
}

void loop() {
  // Dừng đúng hạn trước, để lệnh S luôn cắt được chuyển động đang chạy.
  if (stopAt && millis() >= stopAt) halt();

  fromBle.pump(bt, bleLines);
  // Nhận cùng bộ lệnh qua USB, để thử được khi chưa gắn module BLE.
  fromUsb.pump(Serial, usbLines);
}

void handle(const char* command) {
  if (!command[0]) return;
  const char verb = command[0];

  if (verb == 'S') { halt(); reply("ok S"); return; }
  if (verb == 'P') { reply("OK"); return; }
  if (verb == 'T') { report(); return; }

  // Kiểm tra lệnh trước tham số: atol("") cũng bằng 0, nên nếu đảo thứ tự thì
  // một lệnh lạ sẽ bị báo nhầm thành thiếu tham số.
  if (verb != 'F' && verb != 'B' && verb != 'L' && verb != 'R') {
    reply("err unknown command");
    return;
  }

  const long value = atol(command + 1);
  if (value <= 0) { reply("err missing value"); return; }

  unsigned long duration;
  switch (verb) {
    case 'F': duration = value * MS_PER_CM;     drive(SPEED, SPEED);   break;
    case 'B': duration = value * MS_PER_CM;     drive(-SPEED, -SPEED); break;
    case 'L': duration = value * MS_PER_DEGREE; drive(-SPEED, SPEED);  break;
    default:  duration = value * MS_PER_DEGREE; drive(SPEED, -SPEED);  break;
  }

  if (duration > MAX_RUN_MS) duration = MAX_RUN_MS;
  stopAt = millis() + duration;

  char ack[24];
  snprintf(ack, sizeof(ack), "ok %s", command);
  reply(ack);
}

// left và right: âm là lùi, dương là tiến, |giá trị| là PWM.
void drive(int left, int right) {
  if (INVERT_LEFT) left = -left;
  if (INVERT_RIGHT) right = -right;
  digitalWrite(AIN1, left < 0);
  digitalWrite(AIN2, left > 0);
  digitalWrite(BIN1, right > 0);
  digitalWrite(BIN2, right < 0);
  analogWrite(PWMA, abs(left));
  analogWrite(PWMB, abs(right));
}

void halt() {
  drive(0, 0);
  stopAt = 0;
}

// Trạng thái để đo từ máy tính, không cần nhìn robot.
void report() {
  const unsigned long now = millis();
  const long left = stopAt ? (long)(stopAt - now) : 0;
  char text[56];
  snprintf(text, sizeof(text), "t=%lu stop=%lu left=%ld ble=%lu usb=%lu",
           now, stopAt, left, bleLines, usbLines);
  reply(text);
}

void reply(const char* text) {
  bt.println(text);
  Serial.println(text);
}
