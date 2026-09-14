// Bring-up tương tác cho AlphaBot2: gõ lệnh trong Serial Monitor, xem bánh xe phản ứng.
// Dùng khi động cơ không quay và cần biết thiếu gì: sai chân, yếu lực, hay thiếu STBY.
//
// Serial Monitor: 115200 baud, line ending "New Line" (hoặc "Both NL & CR").
// Gõ ? để xem menu.

const uint8_t PWMA = 6, AIN1 = A1, AIN2 = A0;
const uint8_t BIN1 = A2, BIN2 = A3, PWMB = 5;

// Chân có thể là STBY của TB6612 hoặc enable nào đó. Bỏ D0/D1 vì Serial dùng.
const uint8_t CANDIDATES[] = {2, 3, 4, 7, 8, 9, 10, 11, 12, 13, A4, A5};
const uint8_t N = sizeof(CANDIDATES);

// Đo thực tế trên AlphaBot2: motor phải đấu ngược cực so với motor trái, nên
// cùng một giá trị dương lại làm hai bánh quay ngược chiều nhau. Đảo ở đây thay
// vì đảo dây, và để ai có bo đấu khác chỉ cần sửa hai dòng này.
const bool INVERT_LEFT  = false;
const bool INVERT_RIGHT = true;

uint8_t speed = 120;               // đổi bằng lệnh v
const unsigned long RUN_MS = 1500; // mỗi lần thử chạy bao lâu

char buf[24];
uint8_t len = 0;

void setup() {
  Serial.begin(115200);
  const uint8_t pins[] = {PWMA, AIN1, AIN2, BIN1, BIN2, PWMB};
  for (uint8_t i = 0; i < sizeof(pins); i++) pinMode(pins[i], OUTPUT);
  for (uint8_t i = 0; i < N; i++) { pinMode(CANDIDATES[i], OUTPUT); digitalWrite(CANDIDATES[i], LOW); }
  off();
  delay(300);
  menu();
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (len) { buf[len] = '\0'; handle(buf); len = 0; }
      continue;
    }
    if (len < sizeof(buf) - 1) buf[len++] = c;
  }
}

void menu() {
  Serial.println();
  Serial.println(F("=== AlphaBot2 self-test ==="));
  Serial.println(F("  f / b    hai banh tien / lui"));
  Serial.println(F("  l / r    quay trai / phai tai cho"));
  Serial.println(F("  1 / 2    rieng banh TRAI tien / lui"));
  Serial.println(F("  3 / 4    rieng banh PHAI tien / lui"));
  Serial.println(F("  v<0-255> dat toc do, vd v255"));
  Serial.println(F("  y<chan>  bat/tat mot chan ung vien, vd y9"));
  Serial.println(F("  y        xem trang thai cac chan ung vien"));
  Serial.println(F("  Y / n    bat TAT CA / tat TAT CA chan ung vien"));
  Serial.println(F("  x        tat het ngay"));
  Serial.println(F("  ?        hien lai menu"));
  status();
}

void status() {
  Serial.print(F("toc do = ")); Serial.print(speed);
  Serial.print(F(" | chan dang HIGH: "));
  bool any = false;
  for (uint8_t i = 0; i < N; i++) {
    if (digitalRead(CANDIDATES[i])) {
      Serial.print(CANDIDATES[i]); Serial.print(' ');
      any = true;
    }
  }
  if (!any) Serial.print(F("(khong co)"));
  Serial.println();
}

void handle(const char* cmd) {
  const char verb = cmd[0];

  if (verb == '?') { menu(); return; }
  if (verb == 'x') { off(); Serial.println(F("da tat het")); return; }
  if (verb == 'Y') { setAll(true);  Serial.println(F("bat TAT CA chan ung vien")); status(); return; }
  if (verb == 'n') { setAll(false); Serial.println(F("tat TAT CA chan ung vien")); status(); return; }

  if (verb == 'v') {
    const long value = atol(cmd + 1);
    if (value < 0 || value > 255) { Serial.println(F("toc do phai trong 0..255")); return; }
    speed = value;
    status();
    return;
  }

  if (verb == 'y') {
    if (!cmd[1]) { status(); return; }
    const long pin = atol(cmd + 1);
    for (uint8_t i = 0; i < N; i++) {
      if (CANDIDATES[i] == pin) {
        const bool next = !digitalRead(pin);
        digitalWrite(pin, next);
        Serial.print(F("chan ")); Serial.print(pin);
        Serial.println(next ? F(" -> HIGH") : F(" -> LOW"));
        status();
        return;
      }
    }
    Serial.println(F("chan khong nam trong danh sach ung vien"));
    return;
  }

  int left = 0, right = 0;
  switch (verb) {
    case 'f': left =  speed; right =  speed; break;
    case 'b': left = -speed; right = -speed; break;
    case 'l': left = -speed; right =  speed; break;
    case 'r': left =  speed; right = -speed; break;
    case '1': left =  speed; break;
    case '2': left = -speed; break;
    case '3': right =  speed; break;
    case '4': right = -speed; break;
    default: Serial.println(F("lenh la, go ? de xem menu")); return;
  }

  Serial.print(F(">>> chay ")); Serial.print(verb);
  Serial.print(F(" o toc do ")); Serial.print(speed);
  Serial.print(F(" trong ")); Serial.print(RUN_MS); Serial.println(F(" ms"));
  drive(left, right);
  delay(RUN_MS);
  off();
  Serial.println(F("<<< da dung"));
}

void setAll(bool on) {
  for (uint8_t i = 0; i < N; i++) digitalWrite(CANDIDATES[i], on ? HIGH : LOW);
}

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

void off() {
  analogWrite(PWMA, 0); analogWrite(PWMB, 0);
  digitalWrite(AIN1, LOW); digitalWrite(AIN2, LOW);
  digitalWrite(BIN1, LOW); digitalWrite(BIN2, LOW);
}
