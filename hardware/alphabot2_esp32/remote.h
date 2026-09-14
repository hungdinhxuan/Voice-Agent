#pragma once

// Chế độ remote: robot là một thiết bị Xiaozhi thật.
//
// Mic  -> WebSocket -> server (VAD, ASR, LLM, TTS)
// LLM  -> MCP tools/call -> bánh xe quay
// TTS  -> chuyển tiếp sang trang người xem (robot không có loa)
//
// Uplink là PCM thô 16 kHz little-endian, không phải Opus. Adapter chấp nhận
// `format: "pcm"` riêng cho thiết bị tự làm: nhét được cả bộ mã hoá Opus vào
// sketch này thì tốn công gấp bội, còn 256 kbps trong mạng LAN thì thoải mái.
// Đổi lại, đường này KHÔNG nên chạy qua kết nối tính theo dung lượng.
//
// Vai trò MCP ngược với trực giác: robot là MCP *server*, backend là *client*.
// Backend hỏi initialize rồi tools/list, và khi LLM muốn quay bánh thì nó gọi
// tools/call xuống đây.


WebSocketsClient serverWs;

bool remoteLinked = false;     // socket đang mở
bool remoteReady = false;      // đã nhận hello của server
bool uplinkOpen = false;       // đang được phép đẩy mic lên
uint16_t downRate = 24000;     // sample rate server dùng cho TTS
char sessionId[40] = "";

unsigned long lastLinkAttempt = 0;
const unsigned long LINK_RETRY_MS = 5000;

// 60 ms ở 16 kHz. Mic đọc ra stereo 32-bit nên phải đọc gấp đôi rồi lọc.
const size_t UPLINK_SAMPLES = 960;
static int32_t uplinkRaw[UPLINK_SAMPLES * 2];
static int16_t uplinkPcm[UPLINK_SAMPLES];

uint16_t downlinkSampleRate() { return downRate; }

// Khai báo tool đúng như console tại /xiaozhi khai, để adapter ánh xạ giống hệt.
// `default` có chủ đích: firmware Xiaozhi bỏ property có default ra khỏi
// `required`, nên câu "tiến lên đi" không ép model bịa ra một con số.
static const char TOOLS_JSON[] PROGMEM =
  "[{\"name\":\"self.chassis.forward\",\"description\":\"Drive the robot forward\","
  "\"inputSchema\":{\"type\":\"object\",\"properties\":{\"distance_cm\":{\"type\":\"integer\","
  "\"minimum\":1,\"maximum\":500,\"default\":20,\"description\":\"distance in centimetres\"}}}},"
  "{\"name\":\"self.chassis.backward\",\"description\":\"Drive the robot backward\","
  "\"inputSchema\":{\"type\":\"object\",\"properties\":{\"distance_cm\":{\"type\":\"integer\","
  "\"minimum\":1,\"maximum\":500,\"default\":20,\"description\":\"distance in centimetres\"}}}},"
  "{\"name\":\"self.chassis.turn_left\",\"description\":\"Turn the robot left in place\","
  "\"inputSchema\":{\"type\":\"object\",\"properties\":{\"degrees\":{\"type\":\"integer\","
  "\"minimum\":1,\"maximum\":360,\"default\":90,\"description\":\"angle in degrees\"}}}},"
  "{\"name\":\"self.chassis.turn_right\",\"description\":\"Turn the robot right in place\","
  "\"inputSchema\":{\"type\":\"object\",\"properties\":{\"degrees\":{\"type\":\"integer\","
  "\"minimum\":1,\"maximum\":360,\"default\":90,\"description\":\"angle in degrees\"}}}},"
  "{\"name\":\"self.chassis.stop\",\"description\":\"Stop the robot immediately\","
  "\"inputSchema\":{\"type\":\"object\",\"properties\":{}}},"
  "{\"name\":\"self.get_device_status\",\"description\":\"Get the current status of this device\","
  "\"inputSchema\":{\"type\":\"object\",\"properties\":{}}}]";

// ---------------------------------------------------------------- gửi đi

static void sendJson(const JsonDocument& doc) {
    if (!remoteLinked) return;
    String out;
    serializeJson(doc, out);
    serverWs.sendTXT(out);
}

static void sendHello() {
    JsonDocument doc;
    doc["type"] = "hello";
    doc["version"] = 1;
    doc["transport"] = "websocket";
    doc["features"]["mcp"] = true;
    JsonObject audio = doc["audio_params"].to<JsonObject>();
    audio["format"] = "pcm";
    audio["sample_rate"] = SAMPLE_RATE;
    audio["channels"] = 1;
    audio["frame_duration"] = 60;
    sendJson(doc);
}

static void sendListen(const char* state) {
    JsonDocument doc;
    doc["session_id"] = sessionId;
    doc["type"] = "listen";
    doc["state"] = state;
    doc["mode"] = "auto";
    sendJson(doc);
}

static void sendMcp(const JsonDocument& payload) {
    JsonDocument doc;
    doc["session_id"] = sessionId;
    doc["type"] = "mcp";
    doc["payload"] = payload;
    sendJson(doc);
}

// ---------------------------------------------------------------- MCP server

static void mcpResultText(long id, const char* text, bool isError) {
    JsonDocument payload;
    payload["jsonrpc"] = "2.0";
    payload["id"] = id;
    JsonObject result = payload["result"].to<JsonObject>();
    JsonObject item = result["content"].to<JsonArray>().add<JsonObject>();
    item["type"] = "text";
    item["text"] = text;
    result["isError"] = isError;
    sendMcp(payload);
}

// Một lệnh không gửi được phải quay về dạng isError chứ không phải lỗi JSON-RPC:
// LLM đọc được nội dung và nói lại cho người dùng, thay vì coi đây là hỏng giao thức.
static void mcpToolCall(long id, const char* name, JsonObjectConst args) {
    char verb = 0;
    long value = 0;
    if (!strcmp(name, "self.chassis.forward"))        { verb = 'F'; value = args["distance_cm"] | 20; }
    else if (!strcmp(name, "self.chassis.backward"))  { verb = 'B'; value = args["distance_cm"] | 20; }
    else if (!strcmp(name, "self.chassis.turn_left")) { verb = 'L'; value = args["degrees"] | 90; }
    else if (!strcmp(name, "self.chassis.turn_right")){ verb = 'R'; value = args["degrees"] | 90; }
    else if (!strcmp(name, "self.chassis.stop"))      { verb = 'S'; value = 1; }
    else if (!strcmp(name, "self.get_device_status")) {
        char status[96];
        snprintf(status, sizeof(status),
                 "{\"mode\":\"remote\",\"moving\":%s,\"queued\":%u,\"viewers\":%u}",
                 motionBusy() ? "true" : "false", moveQueueLen, viewerWs.connectedClients());
        mcpResultText(id, status, false);
        return;
    } else {
        JsonDocument payload;
        payload["jsonrpc"] = "2.0";
        payload["id"] = id;
        payload["error"]["code"] = -32602;
        payload["error"]["message"] = "Unknown tool";
        sendMcp(payload);
        return;
    }

    bool started = false;
    if (!motionCommand(verb, value, &started)) {
        mcpResultText(id, "queue full or bad argument", true);
        return;
    }
    char note[48];
    snprintf(note, sizeof(note), "%s %c%ld", started ? "running" : "queued", verb, value);
    viewerAction(note);
    mcpResultText(id, note, false);
}

static void handleMcp(JsonObjectConst payload) {
    const char* method = payload["method"] | "";
    // Firmware Xiaozhi bỏ qua mọi method bắt đầu bằng notifications, không trả lời.
    if (!strncmp(method, "notifications", 13)) return;
    if (!payload["id"].is<long>()) return;
    const long id = payload["id"].as<long>();

    if (!strcmp(method, "initialize")) {
        JsonDocument reply;
        reply["jsonrpc"] = "2.0";
        reply["id"] = id;
        JsonObject result = reply["result"].to<JsonObject>();
        result["protocolVersion"] = "2024-11-05";
        result["capabilities"]["tools"].to<JsonObject>();
        result["serverInfo"]["name"] = "alphabot2-esp32";
        result["serverInfo"]["version"] = "1.0.0";
        sendMcp(reply);
        return;
    }
    if (!strcmp(method, "tools/list")) {
        // Danh sách là hằng, dựng thẳng chuỗi để khỏi nhân đôi nó trong RAM.
        String out = "{\"session_id\":\"";
        out += sessionId;
        out += "\",\"type\":\"mcp\",\"payload\":{\"jsonrpc\":\"2.0\",\"id\":";
        out += id;
        out += ",\"result\":{\"tools\":";
        out += FPSTR(TOOLS_JSON);
        out += "}}}";
        serverWs.sendTXT(out);
        return;
    }
    if (!strcmp(method, "tools/call")) {
        JsonObjectConst params = payload["params"];
        const char* name = params["name"] | "";
        mcpToolCall(id, name, params["arguments"].as<JsonObjectConst>());
        return;
    }
    JsonDocument reply;
    reply["jsonrpc"] = "2.0";
    reply["id"] = id;
    reply["error"]["code"] = -32601;
    reply["error"]["message"] = "Method not implemented";
    sendMcp(reply);
}

// ---------------------------------------------------------------- nhận về

static void handleText(uint8_t* payload, size_t len) {
    JsonDocument doc;
    if (deserializeJson(doc, payload, len)) return;
    const char* type = doc["type"] | "";

    if (!strcmp(type, "hello")) {
        strlcpy(sessionId, doc["session_id"] | "", sizeof(sessionId));
        downRate = doc["audio_params"]["sample_rate"] | 24000;
        remoteReady = true;
        uplinkOpen = true;
        sendListen("start");
        Serial.printf("[WS] hello ok, session=%s downlink=%u Hz\n", sessionId, downRate);
        viewerMode("remote");
        return;
    }
    if (!strcmp(type, "stt")) {
        const char* text = doc["text"] | "";
        Serial.printf("[STT] %s\n", text);
        viewerHeard(text);
        return;
    }
    if (!strcmp(type, "tts")) {
        const char* state = doc["state"] | "";
        if (!strcmp(state, "start")) {
            // Robot không có loa, nhưng điện thoại người xem thì có, và nó có thể
            // nằm ngay cạnh mic. Ngưng đẩy tiếng lên trong lúc server đang nói,
            // nếu không robot sẽ nghe chính câu trả lời rồi tự kích hoạt lượt mới.
            uplinkOpen = false;
        } else if (!strcmp(state, "sentence_start")) {
            viewerSaid(doc["text"] | "");
        } else if (!strcmp(state, "stop")) {
            uplinkOpen = true;
            sendListen("start");
        }
        return;
    }
    if (!strcmp(type, "mcp")) {
        handleMcp(doc["payload"].as<JsonObjectConst>());
        return;
    }
}

static void onWsEvent(WStype_t type, uint8_t* payload, size_t len) {
    switch (type) {
        case WStype_CONNECTED:
            Serial.println("[WS] connected");
            remoteLinked = true;
            sendHello();
            break;
        case WStype_ERROR:
            Serial.printf("[WS] error: %.*s\n", (int)len, (const char*)payload);
            break;
        case WStype_DISCONNECTED:
            Serial.printf("[WS] disconnected (heap=%u)\n", (unsigned)ESP.getFreeHeap());
            remoteLinked = false;
            remoteReady = false;
            uplinkOpen = false;
            motionHalt();      // mất lệnh điều khiển thì không được chạy tiếp
            break;
        case WStype_TEXT:
            handleText(payload, len);
            break;
        case WStype_BIN:
            // Gói Opus của server. ESP32 không giải mã, chỉ chuyển tiếp sang
            // trình duyệt của người xem — WebCodecs lo phần còn lại.
            viewerAudio(payload, len);
            break;
        default:
            break;
    }
}

// ---------------------------------------------------------------- mic uplink

// Gom cho đủ một khung 60 ms rồi mới gửi.
//
// Đọc thẳng 60 ms trong một lần gọi sẽ chặn vòng lặp chừng ấy thời gian, mà
// vòng lặp còn phải phục vụ WebSocket và người xem. Nên mỗi vòng chỉ lấy phần
// I2S đã có sẵn, dồn vào khung, đầy thì đẩy đi. Chờ 0 tick: không có dữ liệu
// thì quay ra ngay chứ không ngồi đợi.
static size_t uplinkFill = 0;
static unsigned long uplinkFrames = 0;

static bool pumpUplink() {
    if (!remoteReady || !uplinkOpen || !micEnabled) {
        uplinkFill = 0;        // đừng gửi tiếng thu từ lượt trước
        return false;
    }
    const size_t want = (UPLINK_SAMPLES - uplinkFill) * 2;   // stereo
    size_t bytesRead = 0;
    // i2s_channel_read trả ESP_ERR_TIMEOUT khi chưa gom đủ số byte yêu cầu,
    // NHƯNG vẫn ghi phần đã đọc được vào bytesRead. Coi đó là lỗi rồi vứt hết
    // thì không khung nào đi lên cả — đúng lỗi đã làm robot câm lặng.
    const esp_err_t err = i2s_channel_read(rx_handle, uplinkRaw,
                                           want * sizeof(int32_t), &bytesRead, 5);
    if (err != ESP_OK && err != ESP_ERR_TIMEOUT) return false;
    const size_t got = bytesRead / sizeof(int32_t);
    if (got < 2) return false;

    // Mic chạy stereo 32-bit: lấy một kênh, và 24-bit thật nằm ở bit 8..31.
    for (size_t i = 0; i + 1 < got && uplinkFill < UPLINK_SAMPLES; i += 2) {
        long sample = (long)((uplinkRaw[i] >> 8) * MIC_GAIN) >> 8;
        if (sample > 32767) sample = 32767;
        if (sample < -32768) sample = -32768;
        uplinkPcm[uplinkFill++] = (int16_t)sample;
    }
    if (uplinkFill < UPLINK_SAMPLES) return false;
    serverWs.sendBIN((uint8_t*)uplinkPcm, UPLINK_SAMPLES * sizeof(int16_t));
    uplinkFill = 0;
    uplinkFrames++;

    // Báo nhịp mỗi 5 giây kèm biên độ lớn nhất. Im lặng không phân biệt được với
    // hỏng, nên in ra cả hai: số khung đã gửi và mic có nghe thấy gì không.
    static unsigned long lastReport = 0;
    if (millis() - lastReport > 5000) {
        lastReport = millis();
        int16_t peak = 0;
        for (size_t i = 0; i < UPLINK_SAMPLES; i++) {
            const int16_t v = uplinkPcm[i] < 0 ? -uplinkPcm[i] : uplinkPcm[i];
            if (v > peak) peak = v;
        }
        Serial.printf("[MIC] da gui %lu khung, dinh %d/32767\n", uplinkFrames, peak);
    }
    return true;
}

// ---------------------------------------------------------------- vòng đời

bool remoteWifiUp() { return WiFi.status() == WL_CONNECTED; }

void remoteBegin() {
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);      // ngủ WiFi làm uplink giật
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    Serial.printf("[WIFI] dang noi %s", WIFI_SSID);
    const unsigned long deadline = millis() + 15000;
    while (WiFi.status() != WL_CONNECTED && millis() < deadline) {
        delay(250);
        Serial.print(".");
    }
    Serial.println();
    if (!remoteWifiUp()) {
        Serial.println("[WIFI] that bai, chay che do local");
        return;
    }
    Serial.printf("[WIFI] ip=%s rssi=%d\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());

    char path[128];
    snprintf(path, sizeof(path), "/xiaozhi/v1/?device-id=%s&client-id=%s", DEVICE_ID, DEVICE_ID);
    Serial.printf("[WS] noi toi %s://%s:%d%s (heap=%u)\n",
                  SERVER_TLS ? "wss" : "ws", SERVER_HOST, SERVER_PORT, path,
                  (unsigned)ESP.getFreeHeap());
#if SERVER_TLS
    serverWs.beginSSL(SERVER_HOST, SERVER_PORT, path);
#else
    serverWs.begin(SERVER_HOST, SERVER_PORT, path);
#endif
    // Thu vien mac dinh gui kem "Origin: file://" va server tu choi bang 403:
    // chot chan Origin sinh ra de chan trinh duyet o site khac mo WebSocket
    // trom. Thiet bi ESP32 that khong gui Origin bao gio, nen bo han di - dung
    // hon la noi long chot chan o phia server.
    serverWs.setExtraHeaders("");
    serverWs.onEvent(onWsEvent);
    serverWs.setReconnectInterval(LINK_RETRY_MS);
    viewerBegin(DEVICE_ID);
}

void remoteTick() {
    serverWs.loop();
    viewerTick();
    motionTick();
    pumpUplink();
}

void remoteStop() {
    serverWs.disconnect();
    remoteLinked = false;
    remoteReady = false;
    uplinkOpen = false;
}
