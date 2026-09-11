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
//   2. BT_RX/BT_TX mặc định D2/D3 chỉ là phỏng đoán. AlphaBot2 đã dùng nhiều chân
//      cho cảm biến, hãy chọn hai chân còn trống trên bo của bạn.
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
const uint8_t BT_RX = 2;  // nối tới TX của module
const uint8_t BT_TX = 3;  // nối tới RX của module
SoftwareSerial bt(BT_RX, BT_TX);

// --- hiệu chỉnh: đo rồi sửa hai số này --------------------------------------
const float MS_PER_CM = 22.0;      // thời gian chạy thẳng 1 cm, ở SPEED bên dưới
const float MS_PER_DEGREE = 4.6;   // thời gian quay tại chỗ 1 độ
const uint8_t SPEED = 120;         // 0..255, phải giống lúc hiệu chỉnh

// Robot không được chạy mãi nếu console mất kết nối giữa chừng.
const unsigned long MAX_RUN_MS = 4000;

unsigned long stopAt = 0;          // 0 nghĩa là đang đứng yên
char line[24];
uint8_t lineLen = 0;

void setup() {
  for (uint8_t pin : {PWMA, AIN1, AIN2, BIN1, BIN2, PWMB}) pinMode(pin, OUTPUT);
  halt();
  Serial.begin(115200);            // để debug qua USB
  bt.begin(9600);                  // HM-10 mặc định 9600
  reply("ready");
}

void loop() {
  // Dừng đúng hạn trước, để lệnh S luôn cắt được chuyển động đang chạy.
  if (stopAt && millis() >= stopAt) halt();

  while (bt.available()) {
    char c = bt.read();
    if (c == '\r') continue;
    if (c == '\n') { line[lineLen] = '\0'; handle(line); lineLen = 0; continue; }
    if (lineLen < sizeof(line) - 1) line[lineLen++] = c;
  }
}

void handle(const char* command) {
  if (!command[0]) return;
  const char verb = command[0];
  const long value = atol(command + 1);

  if (verb == 'S') { halt(); reply("ok S"); return; }
  if (verb == 'P') { reply("OK"); return; }

  if (value <= 0) { reply("err thiếu tham số"); return; }

  unsigned long duration;
  switch (verb) {
    case 'F': duration = value * MS_PER_CM;     drive(SPEED, SPEED);   break;
    case 'B': duration = value * MS_PER_CM;     drive(-SPEED, -SPEED); break;
    case 'L': duration = value * MS_PER_DEGREE; drive(-SPEED, SPEED);  break;
    case 'R': duration = value * MS_PER_DEGREE; drive(SPEED, -SPEED);  break;
    default:  reply("err lệnh lạ"); return;
  }

  if (duration > MAX_RUN_MS) duration = MAX_RUN_MS;
  stopAt = millis() + duration;

  char ack[24];
  snprintf(ack, sizeof(ack), "ok %s", command);
  reply(ack);
}

// left và right: âm là lùi, dương là tiến, |giá trị| là PWM.
void drive(int left, int right) {
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

void reply(const char* text) {
  bt.println(text);
  Serial.println(text);
}
