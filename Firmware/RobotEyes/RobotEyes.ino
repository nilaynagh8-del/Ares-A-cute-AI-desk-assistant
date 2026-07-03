/* ============================================================================
 *  Cute Robot Eyes  +  swipe-down brightness menu
 *  Board : Seeed XIAO ESP32-S3
 *  Screen: Seeed Studio Round Display for XIAO  (GC9A01 240x240, IPS)
 *  Touch : auto-detect CST816 (0x15) or CHSC6X (0x2E) on I2C (D4/D5)
 *  Render: Adafruit_GFX 240x240 framebuffer (GFXcanvas16), pushed via SPI
 *
 *  Interaction:
 *    - Two big oval robot eyes blink, breathe and glance around on their own.
 *    - Swipe DOWN  -> brightness panel slides in from the top.
 *    - Drag finger left/right on the panel -> set brightness (live).
 *    - Swipe UP (or tap below the panel) -> close it.
 *
 *  NOTE: the round display board has a small "KE" slide switch. It must be on
 *  the side that connects the backlight/battery, or the brightness PWM (D6)
 *  won't physically reach the LED. If the slider does nothing, flip that switch.
 * ========================================================================== */

#include <Adafruit_GFX.h>
#include <Adafruit_GC9A01A.h>
#include <SPI.h>
#include <Wire.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <ESP_I2S.h>
#include <ArduinoJson.h>
#include <WebServer.h>
#include <WebSocketsServer.h>
#include "esp_sleep.h"
#include "driver/gpio.h"
#include "page.h"

/* ---------- Pins ---------------------------------------------------------- */
#define PIN_TFT_DC    4    // D3
#define PIN_TFT_CS    2    // D1
#define PIN_TFT_SCK   7    // D8
#define PIN_TFT_MOSI  9    // D10
#define PIN_TFT_MISO  8    // D9  (needed for the SD card)
#define PIN_SD_CS     3    // D2  (microSD chip-select on the round display)
#define PIN_TFT_RST   -1
#define PIN_BL        43   // D6  -> backlight (PWM)
#define PIN_TOUCH_SDA 5    // D4
#define PIN_TOUCH_SCL 6    // D5
#define PIN_TOUCH_INT 44   // D7  (active low)

/* ---------- Orientation (display + touch rotate together) --------------- */
#define SCREEN_ROT    1   // 0,1,2,3 = 0/90/180/270 deg.  Try 3 for the other way.
/* fine touch tweaks (usually leave 0) */
#define TOUCH_INV_X   0
#define TOUCH_INV_Y   0

/* ---------- WiFi (hardcoded auto-join) ---------------------------------- *
 * Fill these to join your network automatically on boot.
 * MUST be a 2.4 GHz network - the ESP32-S3 has no 5 GHz radio.            */
#define WIFI_SSID ""   // optional boot fallback — prefer USB provisioning or NVS
#define WIFI_PASS ""

Adafruit_GC9A01A tft(&SPI, PIN_TFT_DC, PIN_TFT_CS, PIN_TFT_RST);
GFXcanvas16      canvas(240, 240);

static const int16_t W = 240, H = 240, CX = 120, CY = 120;

/* ---------- Colour helpers (RGB565) -------------------------------------- */
static inline uint16_t rgb(uint8_t r, uint8_t g, uint8_t b) {
  return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3);
}
const uint16_t COL_BG     = rgb(2, 4, 8);
const uint16_t COL_EYE    = rgb(16, 110, 170);   // ocean blue
const uint16_t COL_GLOW1  = rgb(8, 48, 86);
const uint16_t COL_GLOW2  = rgb(4, 22, 44);
const uint16_t COL_HI     = rgb(120, 185, 225);
const uint16_t COL_PANEL  = rgb(14, 20, 32);
const uint16_t COL_BORDER = rgb(42, 78, 100);
const uint16_t COL_TRACK  = rgb(40, 50, 66);
const uint16_t COL_TEXT   = rgb(200, 236, 255);

/* live (brightness-scaled) palette - recomputed every frame */
uint16_t cBG, cEye, cGlow1, cGlow2, cHi, cPanel, cBorder, cTrack, cText;
static uint16_t dimColor(uint16_t c, float s) {
  int r = (int)(((c >> 11) & 0x1F) * s);
  int g = (int)(((c >> 5) & 0x3F) * s);
  int b = (int)((c & 0x1F) * s);
  return ((r & 0x1F) << 11) | ((g & 0x3F) << 5) | (b & 0x1F);
}

/* ---------- Backlight (ledc) --------------------------------------------- */
static void backlightInit() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  bool ok = ledcAttach(PIN_BL, 5000, 8);
  Serial.printf("[bl] ledcAttach(GPIO%d) = %s\n", PIN_BL, ok ? "ok" : "FAIL");
#else
  ledcSetup(0, 5000, 8);
  ledcAttachPin(PIN_BL, 0);
#endif
}
static void backlightWrite(uint8_t v) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(PIN_BL, v);
#else
  ledcWrite(0, v);
#endif
}

/* ---------- Touch --------------------------------------------------------- */
uint8_t touchAddr = 0;   // 0x15 = CST816, 0x2E = CHSC6X, 0 = none

static void i2cScan(const char *tag) {
  uint8_t found = 0;
  Serial.printf("[i2c] scan (%s):", tag);
  for (uint8_t a = 0x08; a < 0x78; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) { Serial.printf(" 0x%02X", a); found++; }
  }
  if (!found) Serial.print(" (no devices)");
  Serial.println();
  if (touchAddr == 0) {
    Wire.beginTransmission(0x2E);
    if (Wire.endTransmission() == 0) touchAddr = 0x2E;
    else { Wire.beginTransmission(0x15);
           if (Wire.endTransmission() == 0) touchAddr = 0x15; }
    if (touchAddr) Serial.printf("[touch] using chip @ 0x%02X\n", touchAddr);
  }
}

// read one specific chip's report; true + raw coords if a finger is present
static bool readChip(uint8_t addr, int16_t &rx, int16_t &ry) {
  if (addr == 0x2E) {                       // CHSC6X
    uint8_t t[5] = {0};
    if (Wire.requestFrom((uint8_t)0x2E, (uint8_t)5) != 5) return false;
    for (uint8_t i = 0; i < 5; i++) t[i] = Wire.read();
    if (t[0] != 0x01) return false;
    rx = t[2]; ry = t[4];
    return true;
  } else {                                  // CST816 (0x15)
    Wire.beginTransmission(0x15);
    Wire.write(0x01);
    if (Wire.endTransmission(false) != 0) return false;
    if (Wire.requestFrom((uint8_t)0x15, (uint8_t)6) != 6) return false;
    uint8_t t[6];
    for (uint8_t i = 0; i < 6; i++) t[i] = Wire.read();
    if (!(t[1] & 0x0F)) return false;
    rx = ((t[2] & 0x0F) << 8) | t[3];
    ry = ((t[4] & 0x0F) << 8) | t[5];
    return true;
  }
}

// true while a finger is down; fills mapped screen coords.
// Detects the touch chip on first real contact (address scans are unreliable
// for these controllers, but a register read while INT is low works).
static bool touchRead(int16_t &sx, int16_t &sy) {
  if (digitalRead(PIN_TOUCH_INT) == HIGH) return false;   // INT idle high
  int16_t rx = 0, ry = 0;
  bool ok = false;

  if (touchAddr && readChip(touchAddr, rx, ry)) ok = true;
  if (!ok) {                                              // (re)detect
    if (readChip(0x2E, rx, ry)) { touchAddr = 0x2E; ok = true; }
    else if (readChip(0x15, rx, ry)) { touchAddr = 0x15; ok = true; }
    if (ok) Serial.printf("[touch] locked chip 0x%02X\n", touchAddr);
  }
  if (!ok) return false;

  // rotate raw (native) coords to match the displayed orientation
  int16_t mx, my;
  switch (SCREEN_ROT & 3) {
    case 0:  mx = rx;           my = ry;           break;
    case 1:  mx = ry;           my = (W - 1) - rx; break;
    case 2:  mx = (W - 1) - rx; my = (H - 1) - ry; break;
    default: mx = (H - 1) - ry; my = rx;           break;
  }
  if (TOUCH_INV_X) mx = (W - 1) - mx;
  if (TOUCH_INV_Y) my = (H - 1) - my;
  sx = mx; sy = my;
  return true;
}

/* ---------- Animation / UI state ----------------------------------------- */
float gazeX = 0, gazeY = 0, gazeTX = 0, gazeTY = 0;
uint32_t nextGazeMs = 0;

float    blink = 1.0f;          // 1 = open, ~0.08 = closed
int      blinkPhase = 0;        // 0 idle, 1 closing, 2 opening
uint32_t nextBlinkMs = 0;

bool  menuOpen = false;
float menuShown = 0.0f;         // 0 hidden -> 1 fully down
const int16_t PANEL_H = 150;

/* battery readout (filled by monitorTask) */
volatile int  g_batPct = 0;
volatile float g_batV = 0;
volatile bool g_charging = false, g_charged = false, g_haveBat = false;

float   brightFrac = 0.80f;     // 0..1
const uint8_t BL_MIN = 12;      // never fully black

/* shared with the network task (assistant) */
volatile int  eyeState = 0;     // 0 idle, 1 listening, 2 speaking
volatile bool lowPower = false;
volatile bool wifiConnected = false;
volatile bool screenSleep = false;   // triple-tap to turn the screen off
char g_ip[24] = "connecting...";     // shown in the swipe-down menu
#define ROT_DEG 0                    // fixed eye tilt in degrees (flip sign for the other way)
float g_rc = 1.0f, g_rs = 0.0f;      // cos/sin of ROT_DEG, set in setup

static float powerScale() { return lowPower ? 0.45f : 1.0f; }

static void applyBrightness() {
  if (screenSleep) { backlightWrite(0); return; }
  float b = brightFrac * powerScale();
  backlightWrite((uint8_t)(BL_MIN + b * (255 - BL_MIN)));
}

// recompute the on-screen palette so brightness also works in software
// (covers boards where the D6 backlight line isn't routed to the LED)
static void computePalette() {
  float s = (0.18f + 0.82f * brightFrac) * powerScale();   // content never fully black
  cBG = COL_BG;                           // background stays black
  cEye = dimColor(COL_EYE, s);
  cGlow1 = dimColor(COL_GLOW1, s);
  cGlow2 = dimColor(COL_GLOW2, s);
  cHi = dimColor(COL_HI, s);
  cPanel = dimColor(COL_PANEL, s);
  cBorder = dimColor(COL_BORDER, s);
  cTrack = dimColor(COL_TRACK, s);
  cText = dimColor(COL_TEXT, s);
}

/* gesture tracking */
bool     touching = false;
int16_t  tStartX, tStartY, tCurX, tCurY;
uint32_t lastTouchMs = 0, tDownMs = 0, lastTapMs = 0;
uint8_t  tapCount = 0;
const int16_t SWIPE = 34;

uint32_t lastScanMs = 0, frames = 0, lastFpsMs = 0;

/* ---------- Eye drawing --------------------------------------------------- */
const int16_t EYE_HW = 37, EYE_HH = 52, EYE_R = 30;
const int16_t EYE_LX = 78, EYE_RX = 162;

static int16_t imin(int16_t a, int16_t b) { return a < b ? a : b; }

// rotate a screen point around the screen centre by the fixed enclosure tilt
static void rotpt(int16_t px, int16_t py, int16_t &ox, int16_t &oy) {
  float dx = px - CX, dy = py - CY;
  ox = (int16_t)lroundf(g_rc * dx - g_rs * dy) + CX;
  oy = (int16_t)lroundf(g_rs * dx + g_rc * dy) + CY;
}

// filled rounded rect centred at (cx,cy), drawn tilted (triangles + corner circles)
static void fillTiltRR(int16_t cx, int16_t cy, int16_t hw, int16_t hh, int16_t r, uint16_t col) {
  if (r > hw) r = hw;
  if (r > hh) r = hh;
  int16_t ax, ay, bx, by, c2x, c2y, d2x, d2y;
  rotpt(cx - hw, cy - (hh - r), ax, ay);   rotpt(cx + hw, cy - (hh - r), bx, by);
  rotpt(cx + hw, cy + (hh - r), c2x, c2y); rotpt(cx - hw, cy + (hh - r), d2x, d2y);
  canvas.fillTriangle(ax, ay, bx, by, c2x, c2y, col);
  canvas.fillTriangle(ax, ay, c2x, c2y, d2x, d2y, col);
  rotpt(cx - (hw - r), cy - hh, ax, ay);   rotpt(cx + (hw - r), cy - hh, bx, by);
  rotpt(cx + (hw - r), cy + hh, c2x, c2y); rotpt(cx - (hw - r), cy + hh, d2x, d2y);
  canvas.fillTriangle(ax, ay, bx, by, c2x, c2y, col);
  canvas.fillTriangle(ax, ay, c2x, c2y, d2x, d2y, col);
  int16_t px, py;
  rotpt(cx - (hw - r), cy - (hh - r), px, py); canvas.fillCircle(px, py, r, col);
  rotpt(cx + (hw - r), cy - (hh - r), px, py); canvas.fillCircle(px, py, r, col);
  rotpt(cx + (hw - r), cy + (hh - r), px, py); canvas.fillCircle(px, py, r, col);
  rotpt(cx - (hw - r), cy + (hh - r), px, py); canvas.fillCircle(px, py, r, col);
}

static void drawEye(int16_t cx, int16_t cy, int16_t hw, int16_t hh, float open) {
  int16_t eh = (int16_t)(hh * open);
  if (eh < 3) eh = 3;
  int16_t r = imin(EYE_R, imin(hw, eh));
  fillTiltRR(cx, cy, hw + 4, eh + 4, r + 4, cGlow1);   // single glow halo (faster)
  fillTiltRR(cx, cy, hw, eh, r, cEye);
  if (open > 0.55f) {
    int16_t hx = cx - hw * 0.32f;
    int16_t hy = cy - eh * 0.42f;
    fillTiltRR(hx, hy, 8, 12, 8, cHi);
    int16_t sx, sy;
    rotpt(cx + hw * 0.36f, cy + eh * 0.30f, sx, sy);
    canvas.fillCircle(sx, sy, 5, cHi);
  }
}

static void textCentered(const char *s, int16_t cyc, uint8_t size, uint16_t col) {
  int16_t w = strlen(s) * 6 * size, h = 8 * size;
  canvas.setTextSize(size);
  canvas.setTextColor(col);
  canvas.setCursor(CX - w / 2, cyc - h / 2);
  canvas.print(s);
}

/* ---------- Brightness panel --------------------------------------------- */
static void drawSun(int16_t x, int16_t y, int16_t r, uint16_t c) {
  canvas.fillCircle(x, y, r, c);
  for (int i = 0; i < 8; i++) {
    float a = i * PI / 4.0f;
    canvas.drawLine(x + cosf(a) * (r + 3), y + sinf(a) * (r + 3),
                    x + cosf(a) * (r + 6), y + sinf(a) * (r + 6), c);
  }
}

static void drawBattery(int16_t x, int16_t y, int16_t w, int16_t h,
                        int pct, bool charged) {
  uint16_t col = charged ? rgb(70, 205, 130)
                         : (pct < 20 ? rgb(225, 95, 80) : cEye);
  canvas.drawRoundRect(x, y, w, h, 3, col);
  canvas.fillRect(x + w, y + h / 3, 3, h / 3, col);          // + terminal
  int fw = (w - 4) * pct / 100;
  if (fw < 0) fw = 0; else if (fw > w - 4) fw = w - 4;
  canvas.fillRect(x + 2, y + 2, fw, h - 4, col);
}

static void drawMenu() {
  if (menuShown < 0.02f) return;
  int16_t cb = (int16_t)(menuShown * PANEL_H);   // panel bottom edge

  canvas.fillRoundRect(-30, cb - PANEL_H - 40, W + 60, PANEL_H + 40, 28, cPanel);
  canvas.drawFastHLine(8, cb, W - 16, cBorder);

  textCentered("BRIGHTNESS", cb - 118, 2, cText);

  const int16_t x0 = 54, x1 = 186, sy = cb - 90, th = 12;
  drawSun(34, sy, 4, cTrack);
  drawSun(206, sy, 6, cEye);
  canvas.fillRoundRect(x0, sy - th / 2, x1 - x0, th, th / 2, cTrack);
  int16_t fillW = (int16_t)((x1 - x0) * brightFrac);
  if (fillW < th) fillW = th;
  canvas.fillRoundRect(x0, sy - th / 2, fillW, th, th / 2, cEye);
  int16_t knobX = x0 + (int16_t)((x1 - x0) * brightFrac);
  canvas.fillCircle(knobX, sy, 11, cHi);
  canvas.drawCircle(knobX, sy, 11, cEye);

  char pct[8];
  snprintf(pct, sizeof(pct), "%d%%", (int)roundf(brightFrac * 100));
  textCentered(pct, cb - 66, 2, cEye);

  // IP address (open this in your phone's browser)
  canvas.drawFastHLine(40, cb - 48, W - 80, cBorder);
  textCentered("open in your browser", cb - 34, 1, cTrack);
  textCentered(g_ip, cb - 14, 2, cEye);
}

/* ---------- push framebuffer to display ---------------------------------- */
static void pushCanvas() {
  tft.startWrite();
  tft.setAddrWindow(0, 0, W, H);
  tft.writePixels(canvas.getBuffer(), (uint32_t)W * H);
  tft.endWrite();
}

/* ===================== Assistant: WiFi + onboard PDM mic =================
 * The device streams its mic (16 kHz / 16-bit / mono PCM) over a raw TCP
 * socket to the companion app, and reads newline-delimited JSON commands
 * back (eye state / brightness / low-power / wifi). Discoverable via mDNS as
 * "robot-eyes.local" / service _roboteyes._tcp on port 8080.
 * ====================================================================== */
#define AUDIO_PORT 8080
#define MIC_GAIN   6          // PDM mic is quiet; amplify (tune if clipping)

Preferences prefs;
WiFiServer  audioServer(AUDIO_PORT);
WebSocketsServer wsServer(81);     // phone app: robot mic out + eye-state in
I2SClass    I2S;
char wifiSsid[33] = "";
char wifiPass[65] = "";

static void loadWifi() {
  prefs.begin("robot", true);
  prefs.getString("ssid", "").toCharArray(wifiSsid, sizeof(wifiSsid));
  prefs.getString("pass", "").toCharArray(wifiPass, sizeof(wifiPass));
  prefs.end();
}

static void saveWifi(const char *s, const char *p) {
  prefs.begin("robot", false);
  prefs.putString("ssid", s);
  prefs.putString("pass", p);
  prefs.end();
  strncpy(wifiSsid, s, sizeof(wifiSsid) - 1);
  strncpy(wifiPass, p, sizeof(wifiPass) - 1);
  Serial.printf("[wifi] saved '%s', connecting...\n", wifiSsid);
  WiFi.disconnect();
  WiFi.begin(wifiSsid, wifiPass);
}

// JSON control commands from USB serial OR the connected app (over TCP)
static void handleCommand(const char *line) {
  JsonDocument doc;
  if (deserializeJson(doc, line)) return;
  const char *cmd = doc["cmd"];
  if (!cmd) return;
  if (!strcmp(cmd, "state")) {
    const char *v = doc["value"] | "idle";
    eyeState = !strcmp(v, "speaking") ? 2 : (!strcmp(v, "listening") ? 1 : 0);
  } else if (!strcmp(cmd, "brightness")) {
    float f = doc["value"] | 0.8f;
    brightFrac = f < 0 ? 0 : (f > 1 ? 1 : f);
    applyBrightness();
  } else if (!strcmp(cmd, "lowpower")) {
    lowPower = doc["value"] | false;
    applyBrightness();
  } else if (!strcmp(cmd, "wifi")) {
    const char *s = doc["ssid"] | "";
    const char *p = doc["pass"] | "";
    if (strlen(s)) saveWifi(s, p);
  } else if (!strcmp(cmd, "ping")) {
    Serial.println("[net] pong");
  }
}

// WebSocket (phone app): text frames carry eye-state commands
static void wsEvent(uint8_t num, WStype_t type, uint8_t *payload, size_t len) {
  if (type == WStype_CONNECTED) {
    Serial.println("[ws] phone connected");
    if (eyeState == 0) eyeState = 1;
  } else if (type == WStype_DISCONNECTED) {
    Serial.println("[ws] phone disconnected");
  } else if (type == WStype_TEXT) {
    String s; s.reserve(len);
    for (size_t i = 0; i < len; i++) s += (char)payload[i];
    handleCommand(s.c_str());
  }
}

// WiFi event logging - reason codes tell us why a join fails
static void onWiFiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED)
    Serial.printf("[wifi] disconnected reason=%d (201=AP-not-found, 15/2/3=bad-password)\n",
                  info.wifi_sta_disconnected.reason);
  else if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
    Serial.print("[wifi] GOT IP -> ");
    Serial.println(WiFi.localIP());
  }
}

// Core-0 task: onboard PDM mic -> TCP client; JSON commands TCP -> device
static void netTask(void *arg) {
  I2S.setPinsPdmRx(42, 41);   // XIAO ESP32-S3 Sense: CLK=42, DATA=41
  if (!I2S.begin(I2S_MODE_PDM_RX, 16000, I2S_DATA_BIT_WIDTH_16BIT,
                 I2S_SLOT_MODE_MONO))
    Serial.println("[mic] I2S begin FAILED");
  else
    Serial.println("[mic] PDM mic ready (16 kHz mono)");

  static uint8_t buf[1024];   // 512 samples = ~32 ms
  String netBuf;
  bool announced = false;
  WiFiClient client;          // optional desktop (raw-TCP) client

  for (;;) {
    if (screenSleep) {                          // asleep: stop mic + wifi work entirely
      wifiConnected = false;
      announced = false;
      vTaskDelay(pdMS_TO_TICKS(300));
      continue;
    }
    if (WiFi.status() != WL_CONNECTED) {
      wifiConnected = false;
      announced = false;
      strcpy(g_ip, "no wifi");
      static uint32_t lastTry = 0;
      if (millis() - lastTry > 9000) {
        lastTry = millis();
        Serial.printf("[wifi] status=%d, retrying join to '%s'\n",
                      WiFi.status(), wifiSsid);
        WiFi.disconnect();
        WiFi.begin(wifiSsid, wifiPass);
      }
      vTaskDelay(pdMS_TO_TICKS(300));
      continue;
    }
    if (!announced) {
      announced = true;
      wifiConnected = true;
      Serial.print("[wifi] connected  IP ");
      Serial.println(WiFi.localIP());
      WiFi.localIP().toString().toCharArray(g_ip, sizeof(g_ip));
      if (MDNS.begin("robot-eyes")) {
        MDNS.addService("roboteyes", "tcp", AUDIO_PORT);
        Serial.println("[mdns] robot-eyes.local advertised");
      }
      audioServer.begin();
      wsServer.begin();
      wsServer.onEvent(wsEvent);
    }

    wsServer.loop();                                  // phone WS protocol

    if (!client || !client.connected()) {             // accept a desktop client
      WiFiClient c = audioServer.available();
      if (c) { client = c; netBuf = "";
               Serial.println("[net] desktop app connected");
               if (eyeState == 0) eyeState = 1; }
    }

    size_t n = I2S.readBytes((char *)buf, sizeof(buf));   // blocks ~32 ms
    if (n) {
      int16_t *s = (int16_t *)buf;
      for (size_t i = 0; i < n / 2; i++) {
        int v = s[i] * MIC_GAIN;
        s[i] = v > 32767 ? 32767 : (v < -32768 ? -32768 : v);
      }
      if (client && client.connected()) client.write(buf, n);  // -> desktop app
      wsServer.broadcastBIN(buf, n);                           // -> phone(s)
    }

    if (client && client.connected()) {
      while (client.available()) {
        char c = client.read();
        if (c == '\n') { if (netBuf.length()) { handleCommand(netBuf.c_str()); netBuf = ""; } }
        else if (c != '\r') { netBuf += c; if (netBuf.length() > 300) netBuf = ""; }
      }
    }
  }
}

/* ---------- Battery monitor (A0 via the round-display 1:2 divider) -------
 * Requires the board's "KE" switch on the battery side. Status is pushed to
 * the app on a SEPARATE port (8081) so it never corrupts the audio stream.
 * ====================================================================== */
#define STATUS_PORT 8081
#define BAT_CAL 1.0f          // calibration: set to (true battery V / reported V)
WiFiServer statusServer(STATUS_PORT);
float g_batRawA0 = 0;         // measured voltage at A0 (diagnostics)

static float readBatteryVolts() {
  analogReadMilliVolts(1);                            // dummy read, settle ADC mux
  uint32_t mv = 0;
  for (int i = 0; i < 16; i++) {
    mv += analogReadMilliVolts(1);                    // A0 = GPIO1
    delayMicroseconds(150);                           // let the cap charge (high-Z divider)
  }
  g_batRawA0 = (mv / 16.0f) / 1000.0f;                // volts at A0
  return g_batRawA0 * 2.0f * BAT_CAL;                 // 1:2 divider (+ calibration)
}

static int pctFromVolts(float v) {
  static const float pv[][2] = {{3.30f, 0}, {3.60f, 10}, {3.70f, 25},
    {3.80f, 45}, {3.85f, 55}, {3.90f, 68}, {4.00f, 83}, {4.10f, 95}, {4.20f, 100}};
  if (v <= 3.30f) return 0;
  if (v >= 4.20f) return 100;
  for (int i = 1; i < 9; i++)
    if (v < pv[i][0]) {
      float t = (v - pv[i - 1][0]) / (pv[i][0] - pv[i - 1][0]);
      return (int)(pv[i - 1][1] + t * (pv[i][1] - pv[i - 1][1]));
    }
  return 100;
}

static void monitorTask(void *arg) {
  analogReadResolution(12);
  analogSetPinAttenuation(1, ADC_11db);     // full ~0-3.1V range on A0
  float smooth = 0;
  bool have = false, chargedFired = false, begun = false;
  WiFiClient sc;
  uint32_t lastSend = 0;
  for (;;) {
    float v = readBatteryVolts();
    if (!have) { smooth = v; have = true; } else smooth += (v - smooth) * 0.1f;
    g_batV = smooth;
    g_batPct = pctFromVolts(smooth);
    g_charged = smooth >= 4.15f;
    g_charging = smooth >= 3.95f && !g_charged;
    g_haveBat = smooth >= 1.8f && smooth <= 4.5f;    // wide while we calibrate
    bool doneEvent = false;
    if (g_charged && !chargedFired) { chargedFired = true; doneEvent = true;
                                      Serial.println("[bat] charge complete"); }
    if (smooth < 4.0f) chargedFired = false;

    if (WiFi.status() == WL_CONNECTED) {
      if (!begun) { statusServer.begin(); begun = true; }
      if (!sc || !sc.connected()) sc = statusServer.available();
      if (sc && sc.connected() && (millis() - lastSend > 1500 || doneEvent)) {
        char buf[180];
        snprintf(buf, sizeof(buf),
          "{\"battery\":%d,\"volts\":%.2f,\"charging\":%s,\"charged\":%s,\"event\":\"%s\"}\n",
          g_batPct, smooth, g_charging ? "true" : "false",
          g_charged ? "true" : "false", doneEvent ? "charged" : "");
        sc.print(buf);
        lastSend = millis();
      }
    } else {
      begun = false;
    }
    vTaskDelay(pdMS_TO_TICKS(1000));
  }
}

/* ---------- Personality on the microSD (served over HTTP) ----------------
 * Stores /system.md and /memory.md on the round-display's SD card so the
 * robot's personality travels with it. The app fetches them on connect and
 * seeds the card from its own copy the first time.
 * ====================================================================== */
WebServer http(80);

// Personality lives in the ESP32's own flash (NVS) - no SD card needed, and it
// survives reflashes. The app fetches/seeds it over HTTP on connect.
static String cfgRead(const char *path) {
  Preferences p;
  p.begin("persona", true);
  String s = p.getString(strstr(path, "system") ? "sys" : "mem", "");
  p.end();
  return s;
}
static bool cfgWrite(const char *path, const String &body) {
  Preferences p;
  p.begin("persona", false);
  p.putString(strstr(path, "system") ? "sys" : "mem", body);
  p.end();
  return true;
}
static void httpRoutes() {
  http.on("/", HTTP_GET, []() {
    http.sendHeader("Cache-Control", "no-store, must-revalidate");  // never serve a stale page
    http.send_P(200, "text/html", PHONE_PAGE);
  });
  http.on("/system.md", HTTP_GET, []() { http.send(200, "text/markdown", cfgRead("/system.md")); });
  http.on("/memory.md", HTTP_GET, []() { http.send(200, "text/markdown", cfgRead("/memory.md")); });
  http.on("/system.md", HTTP_POST, []() {
    cfgWrite("/system.md", http.arg("plain"));
    http.send(200, "text/plain", "saved");
  });
  http.on("/memory.md", HTTP_POST, []() {
    cfgWrite("/memory.md", http.arg("plain"));
    http.send(200, "text/plain", "saved");
  });
}

/* ---------- Setup --------------------------------------------------------- */
void setup() {
  Serial.begin(115200);
  Serial.setTxTimeoutMs(0);   // CRITICAL: don't block the loop when USB is unplugged
                              // (HWCDC: no host draining TX -> Serial.print() would hang
                              //  the whole loop on every touch/frame = the "freeze")
  delay(200);

  pinMode(PIN_TOUCH_INT, INPUT_PULLUP);
  Wire.begin(PIN_TOUCH_SDA, PIN_TOUCH_SCL);
  Wire.setClock(100000);
  delay(120);
  i2cScan("boot");

  backlightInit();
  backlightWrite(0);                       // dark until first frame

  SPI.begin(PIN_TFT_SCK, PIN_TFT_MISO, PIN_TFT_MOSI, -1);
  tft.begin(40000000);                     // 40 MHz
  tft.setRotation(SCREEN_ROT);

  Serial.println("[cfg] personality stored in flash (no SD needed)");

  float rr = ROT_DEG * (float)(PI / 180.0);
  g_rc = cosf(rr); g_rs = sinf(rr);
  Serial.printf("[disp] eye tilt = %d deg (drawn, not buffer-rotated)\n", ROT_DEG);

  Serial.printf("[mem] heap=%u psram=%u  canvas_buf=%s\n",
                ESP.getFreeHeap(), ESP.getFreePsram(),
                canvas.getBuffer() ? "ok" : "NULL");

  canvas.fillScreen(COL_BG);
  pushCanvas();

  uint8_t target = BL_MIN + brightFrac * (255 - BL_MIN);
  for (int v = 0; v <= target; v += 6) { backlightWrite(v); delay(8); }
  applyBrightness();

  // WiFi + onboard-mic streaming task (eyes keep running while it connects)
  WiFi.mode(WIFI_STA);
  WiFi.setTxPower(WIFI_POWER_19_5dBm);  // max power - the AP signal here is weak (-89 dBm)
  WiFi.setSleep(false);
  WiFi.onEvent(onWiFiEvent);
  loadWifi();
  if (!strlen(wifiSsid) && strlen(WIFI_SSID)) {      // hardcoded fallback
    strncpy(wifiSsid, WIFI_SSID, sizeof(wifiSsid) - 1);
    strncpy(wifiPass, WIFI_PASS, sizeof(wifiPass) - 1);
  }
  int nn = WiFi.scanNetworks();
  Serial.printf("[scan] %d nets (enc: 2=WPA 3=WPA2 4=WPA/2 6=WPA3 7=WPA2/3):\n", nn);
  for (int i = 0; i < nn && i < 20; i++)
    Serial.printf("  '%s' rssi=%d ch=%d enc=%d\n", WiFi.SSID(i).c_str(),
                  WiFi.RSSI(i), WiFi.channel(i), (int)WiFi.encryptionType(i));

  if (strlen(wifiSsid)) {
    Serial.printf("[wifi] connecting to '%s'...\n", wifiSsid);
    WiFi.begin(wifiSsid, wifiPass);
  } else {
    Serial.println("[wifi] no credentials set");
  }
  xTaskCreatePinnedToCore(netTask, "net", 8192, NULL, 1, NULL, 0);
  xTaskCreatePinnedToCore(monitorTask, "bat", 4096, NULL, 1, NULL, 1);

  uint32_t now = millis();
  nextBlinkMs = now + 1200;
  nextGazeMs  = now + 1500;
  lastScanMs = lastFpsMs = now;
  Serial.println("[boot] robot eyes ready");
}

/* ---------- Animation update --------------------------------------------- */
static void updateEyes(uint32_t now) {
  if (blinkPhase == 0 && now >= nextBlinkMs) blinkPhase = 1;
  if (blinkPhase == 1) { blink -= 0.20f; if (blink <= 0.08f) { blink = 0.08f; blinkPhase = 2; } }
  else if (blinkPhase == 2) {
    blink += 0.16f;
    if (blink >= 1.0f) {
      blink = 1.0f; blinkPhase = 0;
      nextBlinkMs = now + random(2200, 5200);
      if (random(100) < 22) nextBlinkMs = now + 180;
    }
  }

  if (menuOpen) { gazeTX = 0; gazeTY = -12; }
  else if (eyeState != 0) { gazeTX = 0; gazeTY = 0; }   // focus forward when engaged
  else if (now >= nextGazeMs) {
    if (random(100) < 35) { gazeTX = 0; gazeTY = 0; }
    else { gazeTX = random(-18, 19); gazeTY = random(-12, 13); }
    nextGazeMs = now + random(900, 2600);
  }
  gazeX += (gazeTX - gazeX) * 0.12f;
  gazeY += (gazeTY - gazeY) * 0.12f;
}

/* ---------- Touch / gesture handling ------------------------------------- */
static void setBrightFromX(int16_t x) {
  const int16_t x0 = 54, x1 = 186;
  float f = (float)(x - x0) / (x1 - x0);
  if (f < 0) f = 0;
  if (f > 1) f = 1;
  brightFrac = f;
  applyBrightness();
}

static void handleTouch(uint32_t now) {
  int16_t x, y;
  bool down = touchRead(x, y);

  // asleep: ANY touch wakes it. The finger holds INT low so the CPU stays awake
  // and reads it reliably (no fragile triple-tap-while-sleeping detection). Reboot
  // gives a clean re-init of screen + wifi + servers.
  if (screenSleep) {
    if (down) { Serial.println("[wake] touch -> reboot"); delay(30); ESP.restart(); }
    return;                            // no menu/swipe/tap logic while asleep
  }

  if (down) {
    lastTouchMs = now;
    tCurX = x; tCurY = y;
    if (!touching) { touching = true; tStartX = x; tStartY = y; tDownMs = now;
                     Serial.printf("[touch] down x=%d y=%d\n", x, y); }
    if (menuShown > 0.85f && y < PANEL_H - 50) setBrightFromX(x);  // slider band
  } else if (touching && now - lastTouchMs > 70) {
    touching = false;
    int16_t dx = tCurX - tStartX, dy = tCurY - tStartY;

    // triple-tap (3 quick taps) toggles the screen on/off to save battery
    if (abs(dx) < 16 && abs(dy) < 16) {
      tapCount = (now - lastTapMs < 550) ? (tapCount + 1) : 1;
      lastTapMs = now;
      if (tapCount >= 3) {                  // 3 quick taps -> sleep the whole device
        tapCount = 0;
        screenSleep = true;                // enter deep power-save
        menuOpen = false;
        backlightWrite(0);                 // screen fully off
        WiFi.mode(WIFI_OFF);               // radio off = the real battery win; netTask idles
        Serial.println("[sleep] device OFF (screen+wifi). Tap to wake.");
        return;
      }
    } else {
      tapCount = 0;                 // a swipe breaks the tap chain
    }
    if (screenSleep) return;        // ignore swipes/menu while asleep

    bool vertical = abs(dy) > abs(dx) * 1.2f;
    Serial.printf("[swipe] dx=%d dy=%d menu=%d\n", dx, dy, menuOpen);
    if (!menuOpen) {
      if (vertical && dy > SWIPE) menuOpen = true;
    } else {
      if (vertical && dy < -SWIPE) menuOpen = false;
      else if (tStartY > PANEL_H && abs(dx) < 16 && abs(dy) < 16) menuOpen = false;
    }
  }
}

/* ---------- Loop ---------------------------------------------------------- */
void loop() {
  uint32_t now = millis();

  // JSON control commands over USB serial (WiFi provisioning, manual control)
  static String sb;
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') { if (sb.length()) { handleCommand(sb.c_str()); sb = ""; } }
    else if (c != '\r') { sb += c; if (sb.length() > 300) sb = ""; }
  }

  static bool httpUp = false;
  if (wifiConnected && !httpUp) {
    httpRoutes(); http.begin(); httpUp = true;
    Serial.println("[http] config server on :80");
  }
  if (httpUp) http.handleClient();

  handleTouch(now);
  updateEyes(now);
  menuShown += ((menuOpen ? 1.0f : 0.0f) - menuShown) * 0.25f;

  if (!screenSleep) {
  computePalette();
  canvas.fillScreen(cBG);
  float breathe = sinf(now * 0.0018f);
  int16_t cy = CY + (int16_t)(breathe * 2) + (int16_t)gazeY;
  int16_t hh = EYE_HH + (int16_t)(breathe * 3);
  int st = eyeState;
  if (st == 1) hh += 5;                                 // listening: alert/wider
  else if (st == 2) {                                   // speaking: lively bounce
    hh += (int16_t)(sinf(now * 0.030f) * 7) + 3;
    cy += (int16_t)(sinf(now * 0.022f) * 3);
  }
  drawEye(EYE_LX + (int16_t)gazeX, cy, EYE_HW, hh, blink);
  drawEye(EYE_RX + (int16_t)gazeX, cy, EYE_HW, hh, blink);
  drawMenu();
  pushCanvas();
  }

  frames++;
  if (now - lastFpsMs >= 3000) {
    Serial.printf("[fps] %.1f  wifi=%d sleep=%d bright=%.2f\n",
                  frames * 1000.0f / (now - lastFpsMs),
                  wifiConnected, screenSleep, brightFrac);
    frames = 0; lastFpsMs = now;
  }

  // NOTE: esp_light_sleep_start() was tried here for deeper power savings but froze
  // the board solid (USB/CDC doesn't survive light sleep on this chip) - reverted to
  // a plain idle spin. WiFi is still off during screenSleep, which is the real win.
  if (screenSleep) delay(120);
  else if (lowPower) delay(80);      // throttle frame rate to save power
}
