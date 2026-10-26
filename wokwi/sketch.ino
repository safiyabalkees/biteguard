/*
  BiteGuard - ESP32 firmware (Wokwi simulation), low-latency edition

  Button (GPIO4)   = lid / access-attempt sensor
  Servo  (GPIO18)  = lock          (0 deg = locked, 90 deg = unlocked)
  Buzzer (GPIO25)  = alarm tone + 8 kHz voice playback (ESP32 DAC)
  Red LED (GPIO19) = blocked       Green LED (GPIO26) = authorized

  Why it is faster than before
  - Two persistent HTTP(S) connections: the TLS handshake (very slow in the
    simulator) is paid once at boot instead of on every request.
      * "event" connection : used by loop() for the button press (/event).
      * "poll"  connection : used by a background task that long-polls
                             /device/poll, so phone commands (buzzer / voice)
                             arrive instantly and polling never blocks the button.
  - Voice arrives already converted to 8 kHz / 8-bit by the server: no WAV
    parsing on the ESP32 and a download of only a few dozen KB.
*/
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ESP32Servo.h>
#include <ArduinoJson.h>

// Your server: the Cloudflare Quick Tunnel URL (https://....trycloudflare.com),
// or http://<LAN-IP>:5000 if your setup can reach it directly. No trailing slash.
const char* SERVER_URL = "https://YOUR-CLOUDFLARE-TUNNEL.trycloudflare.com";

const char* WIFI_SSID = "Wokwi-GUEST";
const char* WIFI_PASS = "";

const int PIN_BTN = 4, PIN_SERVO = 18, PIN_BUZZ = 25, PIN_RED = 19, PIN_GREEN = 26;
const int LOCKED_ANGLE = 0, UNLOCKED_ANGLE = 90;
const uint32_t UNLOCK_MS = 5000;
const int POLL_WAIT_S = 20;                    // server holds the poll this long
const size_t MAX_VOICE_BYTES = 64 * 1024;      // 8 s at 8 kHz
const uint32_t AUDIO_PERIOD_US = 125;          // 8 kHz

// ---------- persistent connection ----------
struct Link {
  WiFiClientSecure tls;
  WiFiClient plain;
  HTTPClient http;
  bool secure = true;
  void init() {
    secure = String(SERVER_URL).startsWith("https");
    if (secure) tls.setInsecure();             // quick-tunnel certificate, demo only
    http.setReuse(true);                       // keep the socket open between requests
  }
  WiFiClient& net() { return secure ? (WiFiClient&)tls : plain; }
};
Link ev;   // used only by loop()
Link pl;   // used only by netTask()

// ---------- state ----------
Servo lockServo;
volatile bool buttonPending = false;
volatile bool accessInProgress = false;
volatile uint32_t lastButtonInterruptUs = 0;
uint32_t unlockDeadline = 0, indicatorDeadline = 0, lastEvUse = 0, beepUntil = 0;
bool servoUnlocked = false, beeping = false;

enum CmdType : uint8_t { CMD_BUZZ, CMD_VOICE };
struct Cmd { CmdType type; uint32_t value; uint8_t* data; };   // net task -> loop()
QueueHandle_t cmdQ;
volatile bool voiceBusy = false;               // a voice buffer is queued or playing
uint8_t* playBuf = nullptr;
size_t playLen = 0, playPos = 0;
uint32_t nextSampleUs = 0;
int32_t lastBuzzId = -1, lastVoiceId = -1;     // owned by netTask

// Capture one press even while the loop is busy; ignore taps during a check.
void IRAM_ATTR onButtonPress() {
  uint32_t now = micros();
  if (!accessInProgress && (uint32_t)(now - lastButtonInterruptUs) >= 200000UL) {
    lastButtonInterruptUs = now;
    buttonPending = true;
  }
}

void setLeds(bool red, bool green) { digitalWrite(PIN_RED, red); digitalWrite(PIN_GREEN, green); }

void beep(uint16_t freq, uint16_t ms) {
  tone(PIN_BUZZ, freq);
  beepUntil = millis() + ms;
  beeping = true;
}
void serviceBeep() {
  if (beeping && (int32_t)(millis() - beepUntil) >= 0) {
    noTone(PIN_BUZZ);
    dacWrite(PIN_BUZZ, 128);
    beeping = false;
  }
}

// Collects an HTTP body (handles chunked or fixed length) into a fixed buffer.
class BufferSink : public Stream {
 public:
  BufferSink(uint8_t* b, size_t c) : buf(b), cap(c) {}
  size_t write(uint8_t c) override { if (len < cap) { buf[len++] = c; return 1; } return 0; }
  size_t write(const uint8_t* d, size_t n) override {
    n = min(n, cap - len);
    memcpy(buf + len, d, n);
    len += n;
    return n;
  }
  int available() override { return 0; }
  int read() override { return -1; }
  int peek() override { return -1; }
  size_t len = 0;
 private:
  uint8_t* buf;
  size_t cap;
};

// ---------- event request (loop task) ----------
int postEvent(String& response) {
  ev.http.setConnectTimeout(5000);
  ev.http.setTimeout(8000);
  if (!ev.http.begin(ev.net(), String(SERVER_URL) + "/event")) return -1;
  ev.http.addHeader("Content-Type", "application/json");
  String payload = "{\"device\":\"esp32-box\",\"type\":\"access_attempt\"}";
  int code = ev.http.POST(payload);
  if (code > 0) response = ev.http.getString();
  ev.http.end();                               // socket stays open (reuse)
  lastEvUse = millis();
  return code;
}

// Cheap request that keeps the event connection from idling out.
void keepEventLinkWarm(bool force = false) {
  if (WiFi.status() != WL_CONNECTED) return;
  if (!force && millis() - lastEvUse < 25000) return;
  uint32_t t0 = millis();
  ev.http.setConnectTimeout(5000);
  ev.http.setTimeout(4000);
  if (!ev.http.begin(ev.net(), String(SERVER_URL) + "/health")) return;
  int code = ev.http.GET();
  if (code > 0) ev.http.getString();
  ev.http.end();
  if (code <= 0) ev.net().stop();
  lastEvUse = millis();
  if (force) Serial.printf("[net] event link %s (%lu ms)\n", code == 200 ? "ready" : "NOT reachable", (unsigned long)(millis() - t0));
}

// ---------- command long-poll (background task on core 0) ----------
bool downloadVoice(const char* path, uint32_t bytes, uint8_t** out) {
  if (!bytes || bytes > MAX_VOICE_BYTES || !path[0]) return false;
  uint8_t* buf = (uint8_t*)malloc(bytes);
  if (!buf) return false;
  pl.http.setTimeout(8000);
  if (!pl.http.begin(pl.net(), String(SERVER_URL) + path)) { free(buf); return false; }
  size_t got = 0;
  if (pl.http.GET() == 200) {
    BufferSink sink(buf, bytes);
    pl.http.writeToStream(&sink);
    got = sink.len;
  }
  pl.http.end();
  if (got != bytes) { free(buf); return false; }
  *out = buf;
  return true;
}

void pollOnce() {
  const bool sync = lastBuzzId < 0 || lastVoiceId < 0;   // first call only learns current ids
  String url = String(SERVER_URL) + "/device/poll?buzz=" + String(lastBuzzId) +
               "&voice=" + String(lastVoiceId) + "&wait=" + String(sync ? 0 : POLL_WAIT_S);
  pl.http.setConnectTimeout(5000);
  pl.http.setTimeout((POLL_WAIT_S + 8) * 1000);
  if (!pl.http.begin(pl.net(), url)) { vTaskDelay(pdMS_TO_TICKS(2000)); return; }
  int code = pl.http.GET();
  if (code != 200) {
    pl.http.end();
    pl.net().stop();
    vTaskDelay(pdMS_TO_TICKS(code > 0 ? 2000 : 3000));
    return;
  }
  String body = pl.http.getString();
  pl.http.end();

  JsonDocument doc;
  if (deserializeJson(doc, body)) { vTaskDelay(pdMS_TO_TICKS(1000)); return; }
  if (sync) {
    lastBuzzId = doc["b"] | 0;
    lastVoiceId = doc["v"] | 0;
    Serial.println("[net] command channel ready");
    return;
  }

  JsonObject buzz = doc["buzz"].as<JsonObject>();
  if (!buzz.isNull()) {
    lastBuzzId = buzz["id"] | lastBuzzId;
    Cmd c = { CMD_BUZZ, (uint32_t)constrain((int)(buzz["ms"] | 350), 100, 1500), nullptr };
    xQueueSend(cmdQ, &c, 0);
  }
  JsonObject voice = doc["voice"].as<JsonObject>();
  if (!voice.isNull()) {
    if (voiceBusy) { vTaskDelay(pdMS_TO_TICKS(500)); return; }   // finish current clip first
    int32_t id = voice["id"] | 0;
    uint32_t bytes = voice["bytes"] | 0;
    String path = String((const char*)(voice["url"] | ""));
    uint8_t* data = nullptr;
    if (downloadVoice(path.c_str(), bytes, &data)) {
      lastVoiceId = id;
      voiceBusy = true;
      Cmd c = { CMD_VOICE, bytes, data };
      if (xQueueSend(cmdQ, &c, pdMS_TO_TICKS(100)) != pdTRUE) { free(data); voiceBusy = false; }
    } else {
      vTaskDelay(pdMS_TO_TICKS(1500));       // retry later; keep the voice pending
    }
  }
}

void netTask(void*) {
  for (;;) {
    if (WiFi.status() != WL_CONNECTED) { vTaskDelay(pdMS_TO_TICKS(500)); continue; }
    pollOnce();
  }
}

// ---------- behaviour ----------
void handleAccessAttempt() {
  Serial.println("\n>> Lid opened / access attempt! Asking server...");
  lockServo.write(LOCKED_ANGLE);                         // secure the latch while checking
  servoUnlocked = false;
  unlockDeadline = 0;
  indicatorDeadline = 0;
  setLeds(true, true);                                   // both on = thinking

  uint32_t t0 = millis();
  String res;
  int code = WiFi.status() == WL_CONNECTED ? postEvent(res) : -4;
  // -1..-5 = connection-level failure, typically a stale kept-alive socket: retry once, fresh.
  if (code <= -1 && code >= -5 && WiFi.status() == WL_CONNECTED) {
    ev.net().stop();
    code = postEvent(res);
  }
  JsonDocument doc;
  if (code != 200 || deserializeJson(doc, res)) {
    Serial.printf("No valid answer from server (HTTP %d) -> staying locked\n", code);
    setLeds(true, false);
    indicatorDeadline = millis() + 1500;
    return;                                              // a network error is not an alarm
  }
  String decision = doc["decision"] | "unauthorized";
  String person = doc["person"] | "Unknown";
  unsigned long serverMs = doc["recognition_ms"] | 0;
  Serial.printf("Decision: %s (%s) | round trip %lu ms, server %lu ms\n",
                decision.c_str(), person.c_str(), (unsigned long)(millis() - t0), serverMs);

  if (decision == "authorized") {
    setLeds(false, true);
    lockServo.write(UNLOCKED_ANGLE);
    servoUnlocked = true;
    unlockDeadline = millis() + UNLOCK_MS;
    indicatorDeadline = unlockDeadline;
    Serial.println("UNLOCKED for 5 seconds");
  } else {
    setLeds(true, false);
    indicatorDeadline = millis() + 1500;
    Serial.println("ACCESS BLOCKED - alarm!");
    beep(1800, 450);
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_BTN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_BTN), onButtonPress, FALLING);
  pinMode(PIN_BUZZ, OUTPUT); pinMode(PIN_RED, OUTPUT); pinMode(PIN_GREEN, OUTPUT);
  dacWrite(PIN_BUZZ, 128);
  lockServo.setPeriodHertz(50);
  lockServo.attach(PIN_SERVO, 500, 2400);
  lockServo.write(LOCKED_ANGLE);                         // locked by default
  setLeds(false, false);
  cmdQ = xQueueCreate(4, sizeof(Cmd));

  Serial.print("Connecting to Wi-Fi");
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  const uint32_t started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < 15000) delay(50);
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf(" connected: %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println(" unavailable; access checks will stay locked until Wi-Fi returns.");
  }

  ev.init();
  pl.init();
  keepEventLinkWarm(true);                               // pay the TLS handshake now, not at the first press
  xTaskCreatePinnedToCore(netTask, "net", 16384, nullptr, 1, nullptr, 0);
  Serial.println("BiteGuard ready. Box is LOCKED. Press the lid button once.");
}

void loop() {
  if (servoUnlocked && (int32_t)(millis() - unlockDeadline) >= 0) {
    lockServo.write(LOCKED_ANGLE);
    servoUnlocked = false;
    Serial.println("LOCKED again");
  }
  if (indicatorDeadline != 0 && (int32_t)(millis() - indicatorDeadline) >= 0) {
    setLeds(false, false);
    indicatorDeadline = 0;
  }
  serviceBeep();

  // Take one pending press atomically; extra taps during a check are ignored.
  bool pressed = false;
  noInterrupts();
  if (buttonPending && !accessInProgress) {
    buttonPending = false;
    accessInProgress = true;
    pressed = true;
  }
  interrupts();
  if (pressed) {
    handleAccessAttempt();
    nextSampleUs = micros();
    noInterrupts();
    accessInProgress = false;
    buttonPending = false;
    interrupts();
  }

  // Commands from the network task.
  Cmd c;
  while (xQueueReceive(cmdQ, &c, 0) == pdTRUE) {
    if (c.type == CMD_BUZZ) {
      Serial.printf("[buzzer] phone requested sound for %lu ms\n", (unsigned long)c.value);
      beep(1900, c.value);
    } else if (c.type == CMD_VOICE) {
      if (playBuf) free(playBuf);
      playBuf = c.data;
      playLen = c.value;
      playPos = 0;
      nextSampleUs = micros();
      Serial.println("[voice] playing through the ESP32 buzzer/DAC");
    }
  }

  // Voice playback (8 kHz). Lid presses and alarm tones always take priority.
  const bool playing = playBuf != nullptr;
  if (playing && !accessInProgress && !buttonPending) {
    if (beeping) {
      nextSampleUs = micros() + AUDIO_PERIOD_US;
    } else if ((int32_t)(micros() - nextSampleUs) >= 0) {
      dacWrite(PIN_BUZZ, playBuf[playPos++]);
      nextSampleUs = micros() + AUDIO_PERIOD_US;
      if (playPos >= playLen) {
        dacWrite(PIN_BUZZ, 128);
        free(playBuf);
        playBuf = nullptr;
        voiceBusy = false;
        Serial.println("[voice] playback complete");
      }
    }
    delayMicroseconds(10);
  } else {
    delay(5);
    if (!buttonPending) keepEventLinkWarm();
  }
}
