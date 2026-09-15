#pragma once

// Khai bao WiFi bang chinh robot: no tu phat mot hotspot, ai noi vao thi dien
// ten mang va mat khau.
//
// Truoc day credential nam cung trong secrets.h, nghia la doi WiFi la phai cam
// day, mo Arduino IDE va nap lai firmware. Doi mang o mot buoi demo la hong.
// Gio credential nam trong NVS, va secrets.h chi con la gia tri gieo mam cho
// lan chay dau.
//
// VONG DOI
//   boot -> doc NVS (khong co thi lay secrets.h) -> thu noi 15 giay
//        -> noi duoc  : che do REMOTE nhu cu, khong co hotspot nao
//        -> that bai  : bat hotspot "alphabot2-setup", robot van chay LOCAL
//
// Luu xong la khoi dong lai, khong thu noi ngay tai cho. Lam vay thi chi co
// mot duong chay duy nhat de kiem: sai mat khau thi hotspot hien lai, dung
// mat khau thi hotspot bien mat. Khong co trang thai nua voi.
//
// Cong 80 dung chung voi trang nguoi xem trong viewer.h, nhung hai cai khong
// bao gio song cung luc: co WiFi thi chay viewer, khong co thi chay portal.

#include <DNSServer.h>
#include <Preferences.h>

#define PORTAL_SSID   "alphabot2-setup"
#define PORTAL_PASS   "12345678"      // "" de mo han; WPA2 doi it nhat 8 ky tu

static Preferences provisionNvs;
static DNSServer   provisionDns;
static WebServer   provisionHttp(80);
static bool        provisionActive = false;

static String provisionStoredSsid;
static String provisionStoredPass;

const char* provisionSsid() { return provisionStoredSsid.c_str(); }
const char* provisionPass() { return provisionStoredPass.c_str(); }

// Doc credential dang dung. NVS thang thua secrets.h: ai da khai bao qua
// hotspot thi khong muon ban nap firmware sau do lang le keo ho ve mang cu.
void provisionLoad() {
    provisionNvs.begin("wifi", true);
    provisionStoredSsid = provisionNvs.getString("ssid", WIFI_SSID);
    provisionStoredPass = provisionNvs.getString("pass", WIFI_PASS);
    provisionNvs.end();
    Serial.printf("[WIFI] dung SSID \"%s\"\n", provisionStoredSsid.c_str());
}

void provisionSave(const String& ssid, const String& pass) {
    provisionNvs.begin("wifi", false);
    provisionNvs.putString("ssid", ssid);
    provisionNvs.putString("pass", pass);
    provisionNvs.end();
    provisionStoredSsid = ssid;
    provisionStoredPass = pass;
}

// Xoa han thay vi ghi chuoi rong: co the ban muon quay ve dung secrets.h.
void provisionClear() {
    provisionNvs.begin("wifi", false);
    provisionNvs.clear();
    provisionNvs.end();
    Serial.println("[WIFI] da xoa credential trong NVS");
}

bool provisionPortalActive() { return provisionActive; }

// ---------------------------------------------------------------- trang web

// Ten mang do hang xom dat, khong phai do ta. Co dau nhay hay dau nhon trong
// do la trang vo.
static String provisionEscape(const String& raw) {
    String out;
    out.reserve(raw.length() + 8);
    for (size_t i = 0; i < raw.length(); i++) {
        const char c = raw[i];
        if      (c == '&')  out += "&amp;";
        else if (c == '<')  out += "&lt;";
        else if (c == '>')  out += "&gt;";
        else if (c == '"')  out += "&quot;";
        else if (c == '\'') out += "&#39;";
        else                out += c;
    }
    return out;
}

static const char PORTAL_CSS[] PROGMEM =
    "<!doctype html><meta charset=utf-8>"
    "<meta name=viewport content='width=device-width,initial-scale=1'>"
    "<title>Cai WiFi cho robot</title><style>"
    "body{font:16px/1.5 system-ui,sans-serif;max-width:26rem;margin:0 auto;padding:1.5rem 1rem;"
    "background:#111;color:#eee}h1{font-size:1.25rem;margin:0 0 .25rem}"
    "p{color:#aaa;margin:.25rem 0 1.25rem}label{display:block;margin:1rem 0 .3rem}"
    "select,input[type=text],input[type=password]{width:100%;box-sizing:border-box;padding:.6rem;"
    "font-size:1rem;border:1px solid #444;border-radius:.4rem;background:#1c1c1c;color:#eee}"
    "button{margin-top:1.5rem;width:100%;padding:.75rem;font-size:1rem;border:0;border-radius:.4rem;"
    "background:#2d7;color:#052;font-weight:600}a{color:#2d7}.err{color:#f66}"
    "small{color:#888}</style>";

static void provisionHandleRoot() {
    const int found = WiFi.scanComplete();
    String html = FPSTR(PORTAL_CSS);
    html += F("<h1>Cai WiFi cho robot</h1><p>Chon mang de robot noi vao. "
              "Luu xong robot khoi dong lai, hotspot nay tat.</p>"
              "<form method=POST action=/save><label>Mang</label>"
              "<select onchange=\"document.getElementById('s').value=this.value\">"
              "<option value=''>-- chon tu danh sach --</option>");
    for (int i = 0; i < found; i++) {
        const String ssid = WiFi.SSID(i);
        if (!ssid.length()) continue;
        const String safe = provisionEscape(ssid);
        html += "<option value='" + safe + "'>" + safe;
        html += " (" + String(WiFi.RSSI(i)) + " dBm)</option>";
    }
    html += F("</select>"
              "<label>Ten mang</label>"
              "<input type=text id=s name=ssid autocapitalize=off autocorrect=off required value='");
    html += provisionEscape(provisionStoredSsid);
    html += F("'><label>Mat khau</label>"
              "<input type=password id=p name=pass autocapitalize=off autocorrect=off>"
              "<label><input type=checkbox style=width:auto "
              "onclick=\"p.type=this.checked?'text':'password'\"> Hien mat khau</label>"
              "<button type=submit>Luu va khoi dong lai</button></form>");
    // scanComplete(): -1 la dang chay, -2 la hong. Quet trong luc AP dang phat
    // thinh thoang hong that, nen phan biet ro thay vi bao "dang quet" mai mai.
    if (found == WIFI_SCAN_RUNNING) html += F("<p><small>Dang quet, tai lai trang sau vai giay.</small></p>");
    else if (found <= 0)            html += F("<p><small>Chua quet duoc mang nao - go tay ten mang o tren cung duoc.</small></p>");
    html += F("<p><a href=/rescan>Quet lai</a></p>");
    provisionHttp.sendHeader("Cache-Control", "no-store");
    provisionHttp.send(200, "text/html; charset=utf-8", html);
}

static void provisionHandleRescan() {
    WiFi.scanDelete();
    WiFi.scanNetworks(true);          // khong chan: trang tu tai lai sau 3 giay
    provisionHttp.sendHeader("Refresh", "3; url=/");
    provisionHttp.send(200, "text/html; charset=utf-8",
                       String(FPSTR(PORTAL_CSS)) + F("<p>Dang quet lai...</p>"));
}

static void provisionHandleSave() {
    const String ssid = provisionHttp.arg("ssid");
    const String pass = provisionHttp.arg("pass");
    String html = FPSTR(PORTAL_CSS);
    if (!ssid.length()) {
        html += F("<h1 class=err>Thieu ten mang</h1><p><a href=/>Quay lai</a></p>");
        provisionHttp.send(400, "text/html; charset=utf-8", html);
        return;
    }
    provisionSave(ssid, pass);
    Serial.printf("[WIFI] da luu \"%s\", khoi dong lai\n", ssid.c_str());
    html += F("<h1>Da luu</h1><p>Robot dang khoi dong lai de noi vao <b>");
    html += provisionEscape(ssid);
    html += F("</b>.</p><p><small>Noi duoc thi hotspot nay tat han. Con thay no "
              "hien lai sau mot phut nghia la sai mat khau &mdash; noi lai vao day "
              "va thu lan nua.</small></p>");
    provisionHttp.send(200, "text/html; charset=utf-8", html);
    provisionHttp.client().flush();
    delay(1200);                      // du de trinh duyet nhan xong trang
    ESP.restart();
}

// Dien thoai tu do mot URL bat ky de xem co bi chan khong. Tra 302 ve trang cai
// dat thi may hieu la "can dang nhap" va bat trang len - khong ai phai go IP.
static void provisionHandleCaptive() {
    provisionHttp.sendHeader("Location", "http://192.168.4.1/", true);
    provisionHttp.send(302, "text/plain", "");
}

// ---------------------------------------------------------------- vong doi

void provisionPortalBegin() {
    if (provisionActive) return;
    WiFi.mode(WIFI_AP_STA);           // AP de phuc vu trang, STA de quet duoc
    WiFi.softAP(PORTAL_SSID, PORTAL_PASS[0] ? PORTAL_PASS : nullptr);
    delay(100);
    const IPAddress ip = WiFi.softAPIP();
    provisionDns.setErrorReplyCode(DNSReplyCode::NoError);
    provisionDns.start(53, "*", ip);
    WiFi.scanNetworks(true);          // khong chan, trang doc ket qua sau

    provisionHttp.on("/", HTTP_GET, provisionHandleRoot);
    provisionHttp.on("/save", HTTP_POST, provisionHandleSave);
    provisionHttp.on("/rescan", HTTP_GET, provisionHandleRescan);
    provisionHttp.onNotFound(provisionHandleCaptive);
    provisionHttp.begin();

    provisionActive = true;
    Serial.printf("[PORTAL] hotspot \"%s\"%s -> http://%s/\n",
                  PORTAL_SSID,
                  PORTAL_PASS[0] ? " (mat khau " PORTAL_PASS ")" : " (mo)",
                  ip.toString().c_str());
}

void provisionPortalTick() {
    if (!provisionActive) return;
    provisionDns.processNextRequest();
    provisionHttp.handleClient();
}
