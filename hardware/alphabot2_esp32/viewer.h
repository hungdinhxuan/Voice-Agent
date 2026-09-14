#pragma once

// Người xem: robot tự phục vụ một trang web, điện thoại ai mở thì thành loa và
// màn hình cho nó.
//
// Robot không có loa. Server vẫn gửi TTS xuống như với mọi thiết bị Xiaozhi
// khác, và thay vì vứt đi thì chuyển tiếp nguyên gói Opus sang trình duyệt —
// WebCodecs giải mã được, nên ESP32 không phải đụng tới codec nào.
//
// HTTP ở cổng 80, WebSocket ở 81. Hai cổng vì WebServer của core ESP32 không
// nâng cấp kết nối, và tách ra thì phần chuyển tiếp audio không bị handler HTTP
// làm nghẽn.

#include "viewer_page.h"

WebServer viewerHttp(80);
WebSocketsServer viewerWs(81);
bool viewerReady = false;

const char* modeLabel();          // định nghĩa ở file chính
// Định nghĩa ở remote.h, nạp sau file này: chỉ cần khai báo là đủ.
uint16_t downlinkSampleRate();

static void viewerSendConfig(uint8_t client) {
    char json[96];
    snprintf(json, sizeof(json), "{\"t\":\"cfg\",\"rate\":%u,\"mode\":\"%s\"}",
             downlinkSampleRate(), modeLabel());
    viewerWs.sendTXT(client, json);
}

static void viewerEvent(uint8_t client, WStype_t type, uint8_t* payload, size_t len) {
    (void)payload; (void)len;
    if (type == WStype_CONNECTED) viewerSendConfig(client);
}

void viewerBegin(const char* hostname) {
    viewerHttp.on("/", HTTP_GET, []() {
        viewerHttp.sendHeader("Cache-Control", "no-store");
        viewerHttp.send_P(200, "text/html; charset=utf-8", VIEWER_PAGE);
    });
    // Trang tự nối lại, nên mọi đường dẫn khác trả 404 gọn thay vì trang lỗi dài.
    viewerHttp.onNotFound([]() { viewerHttp.send(404, "text/plain", "not found"); });
    viewerHttp.begin();
    viewerWs.begin();
    viewerWs.onEvent(viewerEvent);
    if (MDNS.begin(hostname)) {
        MDNS.addService("http", "tcp", 80);
        Serial.printf("[VIEWER] http://%s.local/ hoac http://%s/\n",
                      hostname, WiFi.localIP().toString().c_str());
    } else {
        Serial.printf("[VIEWER] http://%s/\n", WiFi.localIP().toString().c_str());
    }
    viewerReady = true;
}

void viewerTick() {
    if (!viewerReady) return;
    viewerHttp.handleClient();
    viewerWs.loop();
}

// Chuỗi người dùng nói ra có dấu nháy và dấu chéo ngược thì phá vỡ JSON. Thoát
// tối thiểu, và cắt ngắn thay vì cấp phát động trong vòng lặp audio.
static void viewerSendText(const char* kind, const char* text) {
    if (!viewerReady || viewerWs.connectedClients() == 0) return;
    char json[400];
    size_t at = snprintf(json, sizeof(json), "{\"t\":\"%s\",\"text\":\"", kind);
    for (const char* p = text; *p && at < sizeof(json) - 8; p++) {
        if (*p == '"' || *p == '\\') { json[at++] = '\\'; json[at++] = *p; }
        else if ((unsigned char)*p < 0x20) { json[at++] = ' '; }
        else { json[at++] = *p; }
    }
    at += snprintf(json + at, sizeof(json) - at, "\"}");
    viewerWs.broadcastTXT(json, at);
}

void viewerHeard(const char* text)  { viewerSendText("you", text); }
void viewerSaid(const char* text)   { viewerSendText("bot", text); }
void viewerAction(const char* text) { viewerSendText("act", text); }

void viewerMode(const char* mode) {
    if (!viewerReady) return;
    char json[64];
    int n = snprintf(json, sizeof(json), "{\"t\":\"mode\",\"mode\":\"%s\"}", mode);
    viewerWs.broadcastTXT(json, n);
}

// Gói Opus đi thẳng, không đụng tới nội dung. Bỏ qua khi không có ai xem: phát
// vào hư không chỉ tốn thời gian của vòng lặp đang phải phục vụ mic.
void viewerAudio(const uint8_t* payload, size_t len) {
    if (!viewerReady || len == 0 || viewerWs.connectedClients() == 0) return;
    viewerWs.broadcastBIN(const_cast<uint8_t*>(payload), len);
}
