#include <Arduino.h>
#include <math.h>
#include <Adafruit_NeoPixel.h>
#include "driver/i2s_std.h"
#include <WiFi.h>
#include <WebServer.h>
#include <WebSocketsClient.h>
#include <WebSocketsServer.h>
#include <ESPmDNS.h>
#include <ArduinoJson.h>


// ============================================================
// ESP32-S3 + ICS-43434 + AlphaBot2
//
// SOUND:
//   Sound detected
//       -> Change RGB
//       -> Lock trigger
//       -> MIC OFF
//       -> Robot forward
//       -> Robot stop
//       -> Wait motor noise
//       -> MIC ON
//       -> Flush microphone buffer
//       -> Wait until silence
//       -> ARM again
//
// ============================================================


// ============================================================
// MICROPHONE
// ============================================================

#define AUDIO_I2S_MIC_GPIO_WS   GPIO_NUM_40
#define AUDIO_I2S_MIC_GPIO_SCK  GPIO_NUM_42
#define AUDIO_I2S_MIC_GPIO_DIN  GPIO_NUM_41

#define SAMPLE_RATE 16000

// Giữ gain = 3.0
#define MIC_GAIN 3.0f


// ============================================================
// MOTOR GPIO
// ============================================================

// LEFT MOTOR
#define MOTOR_LEFT_PWM   16
#define MOTOR_LEFT_IN1   10
#define MOTOR_LEFT_IN2   11

// RIGHT MOTOR
#define MOTOR_RIGHT_PWM  17
#define MOTOR_RIGHT_IN1  12
#define MOTOR_RIGHT_IN2  13


// ============================================================
// MOTOR SETTINGS
// ============================================================

#define PWM_FREQ        20000
#define PWM_RESOLUTION  8
#define PWM_MAX         255

// Tốc độ thấp để robot chạy chậm
#define FORWARD_SPEED   80

// Robot tiến bao lâu sau mỗi lần phát hiện âm thanh
#define MOVE_TIME_MS    500


// ============================================================
// MOTOR DIRECTION
// ============================================================
//
// Nếu hiện tại robot đi tiến đúng thì giữ nguyên.
//
// Nếu một bánh chạy ngược:
// false <-> true
//

const bool LEFT_MOTOR_REVERSE  = false;
const bool RIGHT_MOTOR_REVERSE = true;


// ============================================================
// RGB LED
// ============================================================

// ESP32 GPIO14 -> RGB AlphaBot2
#define RGB_PIN    14

// AlphaBot2 có 4 WS2812
#define RGB_COUNT  4

#define RGB_BRIGHTNESS 50

Adafruit_NeoPixel rgb(
    RGB_COUNT,
    RGB_PIN,
    NEO_GRB + NEO_KHZ800
);

int currentColor = 0;


// ============================================================
// SOUND DETECTION SETTINGS
// ============================================================

// Calibration khi mới khởi động
#define CALIBRATION_TIME_MS 2000

// Threshold = noise * multiplier
float SOUND_THRESHOLD_MULTIPLIER = 2.0f;

// Threshold tối thiểu
#define MIN_SOUND_THRESHOLD 500.0f


// ============================================================
// ANTI SELF-TRIGGER SETTINGS
// ============================================================

// Sau khi motor dừng,
// chờ tiếng/rung motor biến mất
#define MIC_SETTLE_TIME_MS 400

// Sau khi bật mic,
// phải thấy im lặng liên tục từng này ms
// mới cho phép trigger tiếp theo
#define REARM_SILENCE_MS 400

// Sau khi bật I2S,
// bỏ một số buffer đầu
#define MIC_FLUSH_COUNT 6



// ============================================================
// REMOTE CONTROL
// ============================================================
//
// HAI CHE DO
//
//   REMOTE  - robot la mot thiet bi Xiaozhi that. Mic di len server, server
//             lam VAD/ASR/LLM/TTS, LLM goi MCP tools/call de quay banh xe.
//             Noi duoc cau day du: "re phai roi di thang hai muoi phan".
//
//   LOCAL   - hanh vi goc cua sketch nay: nghe thay tieng dong thi doi mau RGB
//             va nhich toi. Khong can mang.
//
// Chuyen che do TU DONG: boot len thu WiFi. Noi duoc thi REMOTE, khong thi
// LOCAL. Mat socket giua chung cung roi ve LOCAL, va tu quay lai REMOTE khi
// noi lai duoc. Mat mang thi robot van dung duoc, chi kem thong minh di.
//
// SUA TRUOC KHI NAP
//   1. WIFI_SSID / WIFI_PASS.
//   2. SERVER_HOST: server mac dinh bind 127.0.0.1 nen ESP32 KHONG voi toi.
//      Hoac doi web.host thanh 0.0.0.0 roi dung IP LAN cua may (nhanh nhat,
//      khong can TLS), hoac tro qua tunnel va bat SERVER_TLS.
//   3. Uplink la PCM tho ~256 kbps. Dung trong LAN. Dung chay qua ket noi
//      tinh theo dung luong.

#define WIFI_SSID     "doi-ten-wifi"
#define WIFI_PASS     "doi-mat-khau"

// Dung IP LAN cua may chay server, khong phai 127.0.0.1.
#define SERVER_HOST   "192.168.0.204"
#define SERVER_PORT   8080
#define SERVER_TLS    0          // 1 neu di qua tunnel https

#define DEVICE_ID     "alphabot2"

// Bat ca hai deu ve 0 neu chi muon dung hanh vi goc.
#define ENABLE_REMOTE 1


enum ControlMode { MODE_LOCAL, MODE_REMOTE };
ControlMode controlMode = MODE_LOCAL;

const char* modeLabel()
{
    return controlMode == MODE_REMOTE ? "remote" : "local";
}

// ============================================================
// GLOBAL VARIABLES
// ============================================================

i2s_chan_handle_t rx_handle = NULL;

bool micEnabled = false;

// Thoi diem cuoi cung thu quay lai REMOTE, de khong hoi WiFi moi vong loop.
unsigned long lastLocalRetry = 0;

// true  = cho phép sound trigger
// false = đang khóa trigger
bool soundArmed = true;

float noiseLevel = 0.0f;
float soundThreshold = 0.0f;


// ============================================================
// RGB
// ============================================================

void setAllRGB(
    uint8_t r,
    uint8_t g,
    uint8_t b)
{
    for (int i = 0; i < RGB_COUNT; i++)
    {
        rgb.setPixelColor(
            i,
            rgb.Color(r, g, b)
        );
    }

    rgb.show();
}


// ============================================================
// CHANGE RGB COLOR
// ============================================================

void changeRGBColor()
{
    switch (currentColor)
    {
        case 0:
            setAllRGB(255, 0, 0);
            Serial.println("RGB -> RED");
            break;

        case 1:
            setAllRGB(0, 255, 0);
            Serial.println("RGB -> GREEN");
            break;

        case 2:
            setAllRGB(0, 0, 255);
            Serial.println("RGB -> BLUE");
            break;

        case 3:
            setAllRGB(255, 255, 0);
            Serial.println("RGB -> YELLOW");
            break;

        case 4:
            setAllRGB(255, 0, 255);
            Serial.println("RGB -> PURPLE");
            break;

        case 5:
            setAllRGB(0, 255, 255);
            Serial.println("RGB -> CYAN");
            break;

        case 6:
            setAllRGB(255, 255, 255);
            Serial.println("RGB -> WHITE");
            break;

        case 7:
            setAllRGB(255, 80, 0);
            Serial.println("RGB -> ORANGE");
            break;
    }

    currentColor++;

    if (currentColor >= 8)
    {
        currentColor = 0;
    }
}


// ============================================================
// RGB INIT
// ============================================================

void rgbInit()
{
    Serial.println("Initializing RGB...");

    rgb.begin();

    rgb.setBrightness(
        RGB_BRIGHTNESS
    );

    rgb.clear();
    rgb.show();

    Serial.println("RGB initialized");
}


// ============================================================
// SINGLE MOTOR
// ============================================================

void setMotorSingle(
    int pwmPin,
    int in1,
    int in2,
    int speed,
    bool reverseMotor)
{
    speed = constrain(
        speed,
        -PWM_MAX,
        PWM_MAX
    );


    if (reverseMotor)
    {
        speed = -speed;
    }


    // ========================================================
    // FORWARD
    // ========================================================

    if (speed > 0)
    {
        digitalWrite(
            in1,
            HIGH
        );

        digitalWrite(
            in2,
            LOW
        );

        ledcWrite(
            pwmPin,
            speed
        );
    }


    // ========================================================
    // BACKWARD
    // ========================================================

    else if (speed < 0)
    {
        digitalWrite(
            in1,
            LOW
        );

        digitalWrite(
            in2,
            HIGH
        );

        ledcWrite(
            pwmPin,
            -speed
        );
    }


    // ========================================================
    // STOP
    // ========================================================

    else
    {
        ledcWrite(
            pwmPin,
            0
        );

        digitalWrite(
            in1,
            LOW
        );

        digitalWrite(
            in2,
            LOW
        );
    }
}


// ============================================================
// BOTH MOTOR
// ============================================================

void motorSet(
    int leftSpeed,
    int rightSpeed)
{
    setMotorSingle(
        MOTOR_LEFT_PWM,
        MOTOR_LEFT_IN1,
        MOTOR_LEFT_IN2,
        leftSpeed,
        LEFT_MOTOR_REVERSE
    );


    setMotorSingle(
        MOTOR_RIGHT_PWM,
        MOTOR_RIGHT_IN1,
        MOTOR_RIGHT_IN2,
        rightSpeed,
        RIGHT_MOTOR_REVERSE
    );
}


// ============================================================
// FORWARD
// ============================================================

void robotForward(int speed)
{
    speed = abs(speed);

    motorSet(
        speed,
        speed
    );
}


// ============================================================
// STOP
// ============================================================

void robotStop()
{
    motorSet(
        0,
        0
    );
}


// ============================================================
// MOTOR INIT
// ============================================================

void motorInit()
{
    Serial.println(
        "Initializing motors..."
    );


    pinMode(
        MOTOR_LEFT_IN1,
        OUTPUT
    );

    pinMode(
        MOTOR_LEFT_IN2,
        OUTPUT
    );

    pinMode(
        MOTOR_RIGHT_IN1,
        OUTPUT
    );

    pinMode(
        MOTOR_RIGHT_IN2,
        OUTPUT
    );


    // LEFT PWM
    ledcAttach(
        MOTOR_LEFT_PWM,
        PWM_FREQ,
        PWM_RESOLUTION
    );


    // RIGHT PWM
    ledcAttach(
        MOTOR_RIGHT_PWM,
        PWM_FREQ,
        PWM_RESOLUTION
    );


    robotStop();


    Serial.println(
        "Motor initialized"
    );
}


// ============================================================
// MIC INIT
// ============================================================

bool micInit()
{
    Serial.println(
        "Initializing I2S microphone..."
    );


    // ========================================================
    // CREATE I2S RX CHANNEL
    // ========================================================

    i2s_chan_config_t chan_cfg =
        I2S_CHANNEL_DEFAULT_CONFIG(
            I2S_NUM_AUTO,
            I2S_ROLE_MASTER
        );


    esp_err_t err =
        i2s_new_channel(
            &chan_cfg,
            NULL,
            &rx_handle
        );


    if (err != ESP_OK)
    {
        Serial.print(
            "i2s_new_channel ERROR: "
        );

        Serial.println(err);

        return false;
    }


    // ========================================================
    // STANDARD I2S CONFIG
    // ========================================================

    i2s_std_config_t std_cfg = {};


    std_cfg.clk_cfg =
        I2S_STD_CLK_DEFAULT_CONFIG(
            SAMPLE_RATE
        );


    // ICS-43434:
    // 24-bit audio inside 32-bit frame
    //
    // Stereo để nhận được dữ liệu
    // bất kể chân L/R của mic.
    std_cfg.slot_cfg =
        I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(
            I2S_DATA_BIT_WIDTH_32BIT,
            I2S_SLOT_MODE_STEREO
        );


    // ========================================================
    // GPIO
    // ========================================================

    std_cfg.gpio_cfg.mclk =
        I2S_GPIO_UNUSED;

    std_cfg.gpio_cfg.bclk =
        AUDIO_I2S_MIC_GPIO_SCK;

    std_cfg.gpio_cfg.ws =
        AUDIO_I2S_MIC_GPIO_WS;

    std_cfg.gpio_cfg.dout =
        I2S_GPIO_UNUSED;

    std_cfg.gpio_cfg.din =
        AUDIO_I2S_MIC_GPIO_DIN;


    std_cfg.gpio_cfg.invert_flags.mclk_inv =
        false;

    std_cfg.gpio_cfg.invert_flags.bclk_inv =
        false;

    std_cfg.gpio_cfg.invert_flags.ws_inv =
        false;


    // ========================================================
    // INITIALIZE
    // ========================================================

    err =
        i2s_channel_init_std_mode(
            rx_handle,
            &std_cfg
        );


    if (err != ESP_OK)
    {
        Serial.print(
            "I2S init ERROR: "
        );

        Serial.println(err);

        return false;
    }


    // ========================================================
    // ENABLE MIC
    // ========================================================

    err =
        i2s_channel_enable(
            rx_handle
        );


    if (err != ESP_OK)
    {
        Serial.print(
            "I2S enable ERROR: "
        );

        Serial.println(err);

        return false;
    }


    micEnabled = true;


    Serial.println(
        "I2S microphone initialized"
    );


    return true;
}


// ============================================================
// MIC OFF
// ============================================================

void micPause()
{
    if (
        rx_handle == NULL ||
        !micEnabled
    )
    {
        return;
    }


    esp_err_t err =
        i2s_channel_disable(
            rx_handle
        );


    if (err == ESP_OK)
    {
        micEnabled = false;

        Serial.println(
            ">>> MIC OFF"
        );
    }
    else
    {
        Serial.print(
            "MIC disable ERROR: "
        );

        Serial.println(err);
    }
}


// ============================================================
// FLUSH MIC
// ============================================================
//
// Sau khi bật I2S lại,
// bỏ một số buffer đầu.
//
// Điều này giúp loại:
//
// - sample cũ
// - transient lúc I2S start
// - buffer DMA đầu tiên
//
// ============================================================

void flushMic()
{
    if (
        rx_handle == NULL ||
        !micEnabled
    )
    {
        return;
    }


    int32_t discard[256];

    size_t bytesRead = 0;


    for (
        int i = 0;
        i < MIC_FLUSH_COUNT;
        i++)
    {
        i2s_channel_read(
            rx_handle,
            discard,
            sizeof(discard),
            &bytesRead,
            pdMS_TO_TICKS(30)
        );
    }


    Serial.println(
        ">>> MIC BUFFER FLUSHED"
    );
}


// ============================================================
// MIC ON
// ============================================================

void micResume()
{
    if (
        rx_handle == NULL ||
        micEnabled
    )
    {
        return;
    }


    esp_err_t err =
        i2s_channel_enable(
            rx_handle
        );


    if (err == ESP_OK)
    {
        micEnabled = true;


        Serial.println(
            ">>> MIC ON"
        );


        // Cho I2S clock ổn định
        delay(50);


        // Bỏ buffer đầu
        flushMic();
    }
    else
    {
        Serial.print(
            "MIC enable ERROR: "
        );

        Serial.println(err);
    }
}


// ============================================================
// READ SOUND LEVEL
// ============================================================

float readSoundLevel()
{
    if (!micEnabled)
    {
        return 0.0f;
    }


    static int32_t samples[512];

    size_t bytesRead = 0;


    esp_err_t err =
        i2s_channel_read(
            rx_handle,
            samples,
            sizeof(samples),
            &bytesRead,
            pdMS_TO_TICKS(100)
        );


    if (err != ESP_OK)
    {
        return 0.0f;
    }


    int sampleCount =
        bytesRead /
        sizeof(int32_t);


    if (sampleCount <= 0)
    {
        return 0.0f;
    }


    // ========================================================
    // RMS
    // ========================================================

    double sumSquares = 0.0;


    for (
        int i = 0;
        i < sampleCount;
        i++)
    {
        // 24-bit audio inside
        // 32-bit I2S word

        int32_t sample =
            samples[i] >> 8;


        // ====================================================
        // MIC SOFTWARE GAIN = 3.0
        // ====================================================

        sample =
            (int32_t)(
                sample *
                MIC_GAIN
            );


        double s =
            (double)sample;


        sumSquares +=
            s * s;
    }


    double meanSquare =
        sumSquares /
        sampleCount;


    return sqrt(
        meanSquare
    );
}


// ============================================================
// CALIBRATE MIC
// ============================================================

void calibrateNoise()
{
    Serial.println();

    Serial.println(
        "=================================="
    );

    Serial.println(
        "MIC CALIBRATION"
    );

    Serial.println(
        "KEEP QUIET FOR 2 SECONDS"
    );

    Serial.println(
        "=================================="
    );


    unsigned long startTime =
        millis();


    double total = 0.0;

    int count = 0;


    while (
        millis() -
        startTime <
        CALIBRATION_TIME_MS
    )
    {
        float level =
            readSoundLevel();


        if (level > 0)
        {
            total += level;

            count++;
        }


        delay(5);
    }


    if (count > 0)
    {
        noiseLevel =
            total / count;
    }
    else
    {
        noiseLevel =
            0.0f;
    }


    // ========================================================
    // AUTO THRESHOLD
    // ========================================================

    soundThreshold =
        noiseLevel *
        SOUND_THRESHOLD_MULTIPLIER;


    if (
        soundThreshold <
        MIN_SOUND_THRESHOLD
    )
    {
        soundThreshold =
            MIN_SOUND_THRESHOLD;
    }


    Serial.println();

    Serial.print(
        "Noise level = "
    );

    Serial.println(
        noiseLevel
    );


    Serial.print(
        "Threshold = "
    );

    Serial.println(
        soundThreshold
    );


    Serial.print(
        "MIC gain = "
    );

    Serial.println(
        MIC_GAIN
    );


    Serial.println();

    Serial.println(
        "Calibration finished"
    );

    Serial.println();
}


// ============================================================
// SOUND EVENT
// ============================================================

void handleSoundDetected()
{
    Serial.println();

    Serial.println(
        "=================================="
    );

    Serial.println(
        ">>> SOUND DETECTED"
    );


    // ========================================================
    // 1. CHANGE RGB
    // ========================================================

    changeRGBColor();


    // ========================================================
    // 2. MIC OFF
    // ========================================================

    micPause();


    // Cho I2S stop hoàn toàn
    delay(20);


    // ========================================================
    // 3. ROBOT FORWARD
    // ========================================================

    Serial.print(
        ">>> ROBOT FORWARD - SPEED = "
    );

    Serial.println(
        FORWARD_SPEED
    );


    robotForward(
        FORWARD_SPEED
    );


    delay(
        MOVE_TIME_MS
    );


    // ========================================================
    // 4. STOP MOTOR
    // ========================================================

    robotStop();


    Serial.println(
        ">>> ROBOT STOP"
    );


    // ========================================================
    // 5. WAIT MOTOR NOISE
    // ========================================================

    Serial.print(
        ">>> WAIT MOTOR NOISE: "
    );

    Serial.print(
        MIC_SETTLE_TIME_MS
    );

    Serial.println(
        " ms"
    );


    delay(
        MIC_SETTLE_TIME_MS
    );


    // ========================================================
    // 6. MIC ON + FLUSH
    // ========================================================

    micResume();


    // ========================================================
    // 7. IMPORTANT
    //
    // Không cho trigger ngay.
    // Phải chờ silence trước.
    // ========================================================

    soundArmed = false;


    Serial.println(
        ">>> WAITING FOR SILENCE BEFORE RE-ARM"
    );

    Serial.println(
        "=================================="
    );

    Serial.println();
}


// ============================================================
// MODULE
// ============================================================
//
// De o day chu khong phai dau file: ba module nay goi motorSet(), rx_handle,
// micEnabled... nen phai nam sau khi nhung thu do da duoc dinh nghia.

#include "motion.h"
#include "viewer.h"
#include "remote.h"


// ============================================================
// SETUP
// ============================================================

void setup()
{
    Serial.begin(
        115200
    );


    delay(
        2000
    );


    Serial.println();

    Serial.println(
        "=================================="
    );

    Serial.println(
        "ESP32-S3 + AlphaBot2"
    );

    Serial.println(
        "Sound Controlled Robot"
    );

    Serial.println(
        "Anti Motor Self-Trigger"
    );

    Serial.println(
        "=================================="
    );


    // ========================================================
    // RGB
    // ========================================================

    rgbInit();


    // ========================================================
    // MOTOR
    // ========================================================

    motorInit();


    // ========================================================
    // MIC
    // ========================================================

    if (!micInit())
    {
        Serial.println(
            "MIC INITIALIZATION FAILED!"
        );


        robotStop();


        // RED = ERROR
        setAllRGB(
            255,
            0,
            0
        );


        while (true)
        {
            delay(
                1000
            );
        }
    }


    delay(
        500
    );


    // ========================================================
    // CALIBRATE
    // ========================================================

    calibrateNoise();


    // LEDs OFF
    rgb.clear();
    rgb.show();


    // Ban đầu cho phép trigger
    soundArmed = true;


    Serial.println(
        "=================================="
    );

    Serial.println(
        "SYSTEM READY"
    );


    Serial.print(
        "MIC Gain          = "
    );

    Serial.println(
        MIC_GAIN
    );


    Serial.print(
        "Forward Speed     = "
    );

    Serial.println(
        FORWARD_SPEED
    );


    Serial.print(
        "Move Time         = "
    );

    Serial.print(
        MOVE_TIME_MS
    );

    Serial.println(
        " ms"
    );


    Serial.print(
        "Motor settle      = "
    );

    Serial.print(
        MIC_SETTLE_TIME_MS
    );

    Serial.println(
        " ms"
    );


    Serial.print(
        "Re-arm silence    = "
    );

    Serial.print(
        REARM_SILENCE_MS
    );

    Serial.println(
        " ms"
    );


    Serial.println(
        "=================================="
    );

    Serial.println();

#if ENABLE_REMOTE
    // Thu len REMOTE. That bai thi im lang o lai LOCAL: robot van dung duoc.
    remoteBegin();
    if (remoteWifiUp())
    {
        controlMode = MODE_REMOTE;
        Serial.println("[MODE] REMOTE - dieu khien bang giong noi qua server");
    }
    else
    {
        Serial.println("[MODE] LOCAL - khong co WiFi, nghe tieng dong thi nhich toi");
    }
#else
    Serial.println("[MODE] LOCAL - remote bi tat bang ENABLE_REMOTE");
#endif
}


// ============================================================
// LOOP
// ============================================================

void loop()
{
#if ENABLE_REMOTE
    // WiFi rot thi roi ve LOCAL ngay, va tu quay lai khi noi lai duoc. Khong de
    // robot ket o trang thai cho mot server khong con tra loi.
    if (controlMode == MODE_REMOTE)
    {
        if (!remoteWifiUp())
        {
            Serial.println("[MODE] mat WiFi -> LOCAL");
            remoteStop();
            motionHalt();
            controlMode = MODE_LOCAL;
            viewerMode("local");
        }
        else
        {
            remoteTick();
            return;
        }
    }
    else if (millis() - lastLocalRetry > 20000)
    {
        lastLocalRetry = millis();
        if (remoteWifiUp())
        {
            Serial.println("[MODE] co WiFi lai -> REMOTE");
            controlMode = MODE_REMOTE;
            viewerMode("remote");
            return;
        }
    }
#endif

    // ========================================================
    // READ MIC
    // ========================================================

    float soundLevel =
        readSoundLevel();


    // ========================================================
    // SERIAL DEBUG
    // ========================================================

    static unsigned long
        lastPrintTime = 0;


    if (
        millis() -
        lastPrintTime >=
        200
    )
    {
        Serial.print(
            "Sound = "
        );

        Serial.print(
            soundLevel
        );


        Serial.print(
            " | Threshold = "
        );

        Serial.print(
            soundThreshold
        );


        Serial.print(
            " | Gain = "
        );

        Serial.print(
            MIC_GAIN
        );


        Serial.print(
            " | MIC = "
        );


        if (micEnabled)
        {
            Serial.print(
                "ON"
            );
        }
        else
        {
            Serial.print(
                "OFF"
            );
        }


        Serial.print(
            " | Armed = "
        );


        if (soundArmed)
        {
            Serial.println(
                "YES"
            );
        }
        else
        {
            Serial.println(
                "NO"
            );
        }


        lastPrintTime =
            millis();
    }


    // ========================================================
    // RE-ARM LOGIC
    // ========================================================
    //
    // Sau khi robot chạy:
    //
    // soundArmed = false
    //
    // Mic phải thấy level dưới threshold liên tục
    // REARM_SILENCE_MS
    //
    // mới chuyển sang true.
    //
    // ========================================================

    static unsigned long
        silenceStartTime = 0;


    if (!soundArmed)
    {
        // ====================================================
        // MIC IS QUIET
        // ====================================================

        if (
            soundLevel <
            soundThreshold
        )
        {
            // Bắt đầu tính thời gian silence
            if (
                silenceStartTime == 0
            )
            {
                silenceStartTime =
                    millis();
            }


            // Silence đủ lâu
            if (
                millis() -
                silenceStartTime >=
                REARM_SILENCE_MS
            )
            {
                soundArmed = true;


                silenceStartTime = 0;


                Serial.println();

                Serial.println(
                    ">>> MIC ARMED"
                );

                Serial.println(
                    ">>> READY FOR NEXT SOUND"
                );

                Serial.println();
            }
        }


        // ====================================================
        // STILL NOISY
        // ====================================================

        else
        {
            // Noise vẫn còn
            // reset thời gian silence

            silenceStartTime =
                0;
        }


        // Khi chưa Armed,
        // tuyệt đối không trigger motor

        return;
    }


    // ========================================================
    // SOUND TRIGGER
    // ========================================================

    if (
        micEnabled &&
        soundArmed &&
        soundLevel >
        soundThreshold
    )
    {
        // ====================================================
        // LOCK IMMEDIATELY
        // ====================================================

        soundArmed =
            false;


        // ====================================================
        // RUN ACTION
        // ====================================================

        handleSoundDetected();
    }
}
