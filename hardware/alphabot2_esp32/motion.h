#pragma once

// Chuyển động theo lệnh, dùng chung cho cả hai chế độ.
//
// Cùng bộ lệnh với bản Arduino Uno (hardware/alphabot2_ble): F/B/L/R theo cm và
// độ, S dừng. Giữ nguyên giao thức để console, tài liệu và thói quen gỡ lỗi
// không phải học lại lần nữa.
//
// Không chặn. handleSoundDetected() của chế độ local dùng delay() và điều đó
// chấp nhận được vì lúc đó không có socket nào cần phục vụ; ở chế độ remote thì
// delay() sẽ làm nghẽn WebSocket và mic, nên đường này chạy theo mốc thời gian.

// AlphaBot2 bản cơ bản không có encoder: cm và độ đều quy ra thời gian chạy.
// Hai số này phải đo lại nếu đổi MOVE_SPEED, mặt sàn hay mức pin.
const float MS_PER_CM     = 30.0;
const float MS_PER_DEGREE = 6.0;
const uint8_t MOVE_SPEED  = 110;   // cao hơn FORWARD_SPEED của chế độ local

// Không để robot chạy mãi nếu mất kết nối giữa chừng.
const unsigned long MAX_RUN_MS = 4000;

// "Rẽ phải rồi đi thẳng hai mươi phân" làm model gọi hai tool, và chúng tới nơi
// cách nhau vài mili giây. Không xếp hàng thì lệnh sau ghi đè mốc dừng của lệnh
// trước, nên động tác đầu gần như không chạy.
const uint8_t QUEUE_MAX = 6;
const unsigned long SETTLE_MS = 150;   // đứng yên giữa hai động tác cho đỡ trôi

struct Move { char verb; long value; };
Move moveQueue[QUEUE_MAX];
uint8_t moveQueueLen = 0;

unsigned long moveStopAt = 0;    // 0 nghĩa là đang đứng yên
unsigned long moveResumeAt = 0;  // chưa tới mốc này thì chưa lấy lệnh kế

bool motionBusy() { return moveStopAt != 0 || moveQueueLen > 0; }

// Dừng nghĩa là dừng hẳn: bỏ luôn những động tác còn chờ, nếu không robot sẽ tự
// chạy tiếp ngay sau khi người dùng bảo nó dừng.
void motionHalt() {
    robotStop();
    moveStopAt = 0;
    moveResumeAt = 0;
    moveQueueLen = 0;
}

bool motionEnqueue(char verb, long value) {
    if (moveQueueLen >= QUEUE_MAX) return false;
    moveQueue[moveQueueLen].verb = verb;
    moveQueue[moveQueueLen].value = value;
    moveQueueLen++;
    return true;
}

// Lấy động tác đầu hàng ra chạy. Hàng ngắn nên dồn mảng cho đơn giản.
void motionStartNext() {
    if (!moveQueueLen) return;
    const char verb = moveQueue[0].verb;
    const long value = moveQueue[0].value;
    for (uint8_t i = 1; i < moveQueueLen; i++) moveQueue[i - 1] = moveQueue[i];
    moveQueueLen--;

    unsigned long duration;
    switch (verb) {
        case 'F': duration = value * MS_PER_CM;     motorSet( MOVE_SPEED,  MOVE_SPEED); break;
        case 'B': duration = value * MS_PER_CM;     motorSet(-MOVE_SPEED, -MOVE_SPEED); break;
        case 'L': duration = value * MS_PER_DEGREE; motorSet(-MOVE_SPEED,  MOVE_SPEED); break;
        default:  duration = value * MS_PER_DEGREE; motorSet( MOVE_SPEED, -MOVE_SPEED); break;
    }
    if (duration > MAX_RUN_MS) duration = MAX_RUN_MS;
    moveStopAt = millis() + duration;
}

// Gọi mỗi vòng loop ở chế độ remote. Trả về true nếu vừa bắt đầu một động tác.
void motionTick() {
    if (moveStopAt && millis() >= moveStopAt) {
        robotStop();
        moveStopAt = 0;
        moveResumeAt = millis() + SETTLE_MS;
    }
    if (!moveStopAt && moveQueueLen && millis() >= moveResumeAt) motionStartNext();
}

// Nhận một lệnh. `started` cho biết nó chạy ngay hay phải xếp hàng, để bên gọi
// nói lại cho LLM biết robot đang làm hay đang chờ.
bool motionCommand(char verb, long value, bool* started) {
    if (started) *started = false;
    if (verb == 'S') { motionHalt(); if (started) *started = true; return true; }
    if (verb != 'F' && verb != 'B' && verb != 'L' && verb != 'R') return false;
    if (value <= 0) return false;
    if (!motionEnqueue(verb, value)) return false;
    if (!moveStopAt && millis() >= moveResumeAt) {
        motionStartNext();
        if (started) *started = true;
    }
    return true;
}

// --- điều khiển tay qua USB -------------------------------------------------
//
// Cùng bộ lệnh mà MCP đẩy xuống, nhưng gõ được từ Serial Monitor. Để tách bạch
// "không có điện động cơ" với "lệnh không tới nơi": gõ F20 mà bánh không quay
// thì lỗi nằm ở nguồn hoặc dây, không phải ở đường tiếng nói.
//
//   F<cm>  B<cm>  L<độ>  R<độ>  S        ví dụ F20
//   ?      in trạng thái

void motionSerialTick() {
    static char buf[24];
    static uint8_t len = 0;
    while (Serial.available()) {
        const char c = Serial.read();
        if (c == '\r') continue;
        if (c != '\n') {
            if (len < sizeof(buf) - 1) buf[len++] = c;
            continue;
        }
        buf[len] = 0;
        len = 0;
        if (!buf[0]) continue;

        if (buf[0] == '?') {
            Serial.printf("[MOTION] dang chay=%s, con cho=%u, stopAt=%lu, now=%lu\n",
                          moveStopAt ? "co" : "khong", moveQueueLen, moveStopAt, millis());
            continue;
        }

        bool started = false;
        const long value = (buf[0] == 'S') ? 1 : atol(buf + 1);
        if (motionCommand(buf[0], value, &started)) {
            Serial.printf("[MOTION] %s %s\n", started ? "chay ngay" : "xep hang", buf);
        } else {
            Serial.printf("[MOTION] lenh la hoac thieu tham so: %s\n", buf);
        }
    }
}
