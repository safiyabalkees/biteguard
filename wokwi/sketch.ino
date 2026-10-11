/*
  BiteGuard - ESP32 firmware (Wokwi simulation)

  Button (GPIO4)  = lid / access-attempt sensor
  Servo  (GPIO18) = lock          (0 deg = locked, 90 deg = unlocked)
  Buzzer (GPIO19) = alarm + stands in for the speaker
  Red LED (25)    = unauthorized      Green LED (26) = authorized

  Flow: press button -> POST /event -> server photographs + recognises person
        -> authorized: unlock 5 s | unauthorized: stay locked, alarm, red LED
  Every 3 s the ESP32 polls /voice/pending; a new voice message is "played"
  (buzzer melody here; the laptop plays the real audio in the simulation).
*/
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ESP32Servo.h>
#include <ArduinoJson.h>

// ---------- EDIT THIS: your ngrok URL (no trailing slash) ----------
const char* SERVER_URL = "https://YOUR-NAME.ngrok-free.app";
// -------------------------------------------------------------------

const char* WIFI_SSID = "Wokwi-GUEST";   // Wokwi's built-in Wi-Fi
const char* WIFI_PASS = "";

const int PIN_BTN = 4, PIN_SERVO = 18, PIN_BUZZ = 19, PIN_RED = 25, PIN_GREEN = 26;
const int LOCKED_ANGLE = 0, UNLOCKED_ANGLE = 90;

Servo lockServo;
WiFiClientSecure secureClient;
WiFiClient plainClient;
int lastVoiceId = 0;
unsigned long lastPoll = 0, lastPress = 0;

// ---------- helpers ----------
void beep(int freq, int ms) {            // bit-banged tone on the buzzer
  unsigned long end = millis() + ms;
  int half = 500000 / freq;
  while (millis() < end) {
    digitalWrite(PIN_BUZZ, HIGH); delayMicroseconds(half);
    digitalWrite(PIN_BUZZ, LOW);  delayMicroseconds(half);
  }
}

void alarm() { for (int i = 0; i < 6; i++) { beep(1800, 200); delay(120); } }

void voiceMelody() { int n[] = {880, 988, 1175, 988, 1175, 1319}; for (int f : n) { beep(f, 150); delay(30); } }

void setLeds(bool red, bool green) { digitalWrite(PIN_RED, red); digitalWrite(PIN_GREEN, green); }

String httpCall(const String& method, const String& path, const String& body = "") {
  String url = String(SERVER_URL) + path;
  HTTPClient http;
  bool https = url.startsWith("https");
  if (https) { secureClient.setInsecure(); http.begin(secureClient, url); }
  else       { http.begin(plainClient, url); }
  http.addHeader("ngrok-skip-browser-warning", "true");
  http.setTimeout(20000);
  int code;
  if (method == "POST") { http.addHeader("Content-Type", "application/json"); code = http.POST(body); }
  else code = http.GET();
  String res = (code > 0) ? http.getString() : "";
  if (code <= 0) Serial.printf("HTTP error: %s\n", http.errorToString(code).c_str());
  http.end();
  return res;
}

// ---------- behaviours ----------
void handleAccessAttempt() {
  Serial.println("\n>> Lid opened / access attempt! Asking server...");
  setLeds(true, true);                                   // both on = thinking
  String res = httpCall("POST", "/event", "{\"device\":\"esp32-box\",\"type\":\"access_attempt\"}");
  JsonDocument doc;
  if (res == "" || deserializeJson(doc, res)) {
    Serial.println("No valid answer from server -> staying locked");
    setLeds(true, false); alarm(); setLeds(false, false); return;
  }
  String decision = doc["decision"] | "unauthorized";
  String person = doc["person"] | "Unknown";
  Serial.printf("Decision: %s (%s)\n", decision.c_str(), person.c_str());

  if (decision == "authorized") {
    setLeds(false, true);
    lockServo.write(UNLOCKED_ANGLE);
    Serial.println("UNLOCKED for 5 seconds");
    delay(5000);
    lockServo.write(LOCKED_ANGLE);
    Serial.println("LOCKED again");
  } else {
    lockServo.write(LOCKED_ANGLE);                       // stays locked
    setLeds(true, false);
    Serial.println("ACCESS BLOCKED - alarm!");
    alarm();
  }
  setLeds(false, false);
}

void pollVoice() {
  String res = httpCall("GET", "/voice/pending?last=" + String(lastVoiceId));
  JsonDocument doc;
  if (res == "" || deserializeJson(doc, res)) return;
  if (doc["pending"] | false) {
    lastVoiceId = doc["id"] | lastVoiceId;
    Serial.printf("Voice message #%d received -> playing on speaker\n", lastVoiceId);
    voiceMelody();           // real speaker would stream the WAV via I2S
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_BTN, INPUT_PULLUP);
  pinMode(PIN_BUZZ, OUTPUT); pinMode(PIN_RED, OUTPUT); pinMode(PIN_GREEN, OUTPUT);
  lockServo.setPeriodHertz(50);
  lockServo.attach(PIN_SERVO, 500, 2400);
  lockServo.write(LOCKED_ANGLE);                         // locked by default
  setLeds(false, false);

  Serial.print("Connecting to Wi-Fi");
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) { delay(250); Serial.print("."); }
  Serial.println(" connected!");
  Serial.println("BiteGuard ready. Box is LOCKED. Press the button to simulate opening the lid.");
  beep(1500, 100);
}

void loop() {
  if (digitalRead(PIN_BTN) == LOW && millis() - lastPress > 1500) {
    lastPress = millis();
    handleAccessAttempt();
  }
  if (millis() - lastPoll > 3000) {
    lastPoll = millis();
    pollVoice();
  }
  delay(20);
}
