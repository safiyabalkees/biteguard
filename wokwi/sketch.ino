/*
  BiteGuard - ESP32 firmware (Wokwi simulation)

  Button (GPIO4)  = lid / access-attempt sensor
  Servo  (GPIO18) = lock          (0 deg = locked, 90 deg = unlocked)
  Buzzer (GPIO25) = alarm + low-rate PCM voice output (ESP32 DAC)
  Red LED (GPIO19) = unauthorized      Green LED (GPIO26) = authorized

  Flow: press button -> POST /event -> server recognizes a fresh camera frame
        -> authorized: unlock 5 s | unauthorized: stay locked, short alarm

  Voice messages are polled cooperatively from loop() and sent as low-rate PCM
  through the ESP32 DAC to Wokwi's piezo buzzer; no melody is substituted.
*/
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ESP32Servo.h>
#include <ArduinoJson.h>
#include <vector>

// Local BiteGuard Flask server exposed through the active Cloudflare Quick Tunnel.
// This URL is temporary and changes whenever the tunnel is restarted.
const char* SERVER_URL = "https://subsequent-programmers-gather-administrative.trycloudflare.com";

const char* WIFI_SSID = "Wokwi-GUEST";   // Wokwi's built-in Wi-Fi
const char* WIFI_PASS = "";

const int PIN_BTN = 4, PIN_SERVO = 18, PIN_BUZZ = 25, PIN_RED = 19, PIN_GREEN = 26;
const int LOCKED_ANGLE = 0, UNLOCKED_ANGLE = 90;

Servo lockServo;
WiFiClientSecure secureClient;
WiFiClient plainClient;
volatile bool buttonPending = false;
volatile bool accessInProgress = false;
volatile uint32_t lastButtonInterruptUs = 0;
unsigned long unlockDeadline = 0;
unsigned long indicatorDeadline = 0;
bool servoUnlocked = false;
uint32_t alarmUntil = 0;

constexpr uint32_t AUDIO_RATE = 8000;
constexpr uint32_t MAX_VOICE_SECONDS = 20;
constexpr size_t MAX_VOICE_FILE_BYTES = 2 * 1024 * 1024;
std::vector<uint8_t> queuedAudio;
std::vector<uint8_t> playingAudio;
bool audioQueued = false;
size_t audioPosition = 0;
uint32_t nextAudioSampleUs = 0;
uint32_t nextVoicePollMs = 0;
uint32_t lastVoiceId = 0;

// Capture one press while the loop is busy. Ignore extra taps until the
// current server decision finishes so they are not replayed as extra attempts.
void IRAM_ATTR onButtonPress() {
  uint32_t now = micros();
  if (!accessInProgress && (uint32_t)(now - lastButtonInterruptUs) >= 200000UL) {
    lastButtonInterruptUs = now;
    buttonPending = true;
  }
}

// ---------- helpers ----------
void setLeds(bool red, bool green) { digitalWrite(PIN_RED, red); digitalWrite(PIN_GREEN, green); }

String httpCall(const String& method, const String& path, const String& body = "") {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[Wi-Fi] Offline; access remains locked.");
    return "";
  }

  String url = String(SERVER_URL) + path;
  HTTPClient http;
  bool https = url.startsWith("https");
  bool started = false;
  if (https) {
    secureClient.setInsecure(); // temporary Cloudflare Quick Tunnel certificate
    started = http.begin(secureClient, url);
  } else {
    started = http.begin(plainClient, url);
  }
  if (!started) {
    Serial.println("[HTTP] Could not start request; access remains locked.");
    return "";
  }
  http.setConnectTimeout(4000);
  http.setTimeout(6000);
  int code;
  if (method == "POST") { http.addHeader("Content-Type", "application/json"); code = http.POST(body); }
  else code = http.GET();
  String res = (code > 0) ? http.getString() : "";
  if (code <= 0) Serial.printf("HTTP error: %s\n", http.errorToString(code).c_str());
  http.end();
  return res;
}

uint16_t read16(const uint8_t* data) {
  return (uint16_t)data[0] | ((uint16_t)data[1] << 8);
}

uint32_t read32(const uint8_t* data) {
  return (uint32_t)data[0] | ((uint32_t)data[1] << 8) |
         ((uint32_t)data[2] << 16) | ((uint32_t)data[3] << 24);
}

bool decodeWavToDac(const std::vector<uint8_t>& wav, std::vector<uint8_t>& samples) {
  if (wav.size() < 44 || memcmp(wav.data(), "RIFF", 4) ||
      memcmp(wav.data() + 8, "WAVE", 4)) return false;

  uint16_t format = 0, channels = 0, bits = 0;
  uint32_t sampleRate = 0;
  size_t dataOffset = 0, dataLength = 0;
  for (size_t offset = 12; offset + 8 <= wav.size();) {
    const uint32_t chunkLength = read32(wav.data() + offset + 4);
    const size_t content = offset + 8;
    if (chunkLength > wav.size() - content) return false;
    if (!memcmp(wav.data() + offset, "fmt ", 4) && chunkLength >= 16) {
      format = read16(wav.data() + content);
      channels = read16(wav.data() + content + 2);
      sampleRate = read32(wav.data() + content + 4);
      bits = read16(wav.data() + content + 14);
    } else if (!memcmp(wav.data() + offset, "data", 4)) {
      dataOffset = content;
      dataLength = chunkLength;
      break;
    }
    offset = content + chunkLength + (chunkLength & 1);
  }

  if (format != 1 || (bits != 8 && bits != 16) ||
      (channels != 1 && channels != 2) || sampleRate < AUDIO_RATE ||
      sampleRate > 48000 || !dataLength) return false;

  const size_t bytesPerSample = bits / 8;
  const size_t bytesPerFrame = bytesPerSample * channels;
  const size_t sourceFrames = dataLength / bytesPerFrame;
  const size_t maxSamples = (size_t)AUDIO_RATE * MAX_VOICE_SECONDS;
  const size_t outputFrames = min(maxSamples,
      (size_t)(((uint64_t)sourceFrames * AUDIO_RATE) / sampleRate));
  if (!outputFrames) return false;

  samples.resize(outputFrames);
  for (size_t i = 0; i < outputFrames; ++i) {
    const size_t frame = (size_t)(((uint64_t)i * sampleRate) / AUDIO_RATE);
    int32_t mixed = 0;
    for (uint16_t channel = 0; channel < channels; ++channel) {
      const size_t at = dataOffset + frame * bytesPerFrame + channel * bytesPerSample;
      const int32_t value = bits == 16
          ? (int16_t)read16(wav.data() + at)
          : ((int32_t)wav[at] - 128) << 8;
      mixed += value;
    }
    mixed /= channels;
    samples[i] = (uint8_t)constrain((mixed >> 8) + 128, 0, 255);
  }
  return true;
}

bool readVoiceFile(HTTPClient& http, std::vector<uint8_t>& wav) {
  const int length = http.getSize();
  if (length <= 44 || (size_t)length > MAX_VOICE_FILE_BYTES) return false;
  wav.resize((size_t)length);
  WiFiClient* stream = http.getStreamPtr();
  size_t received = 0;
  const uint32_t started = millis();
  while (received < wav.size() && millis() - started < 8000) {
    // Give a lid press priority over voice download. The main loop will
    // handle the captured press as soon as this read returns.
    if (buttonPending || accessInProgress) {
      wav.clear();
      return false;
    }
    const int available = stream->available();
    if (available > 0) {
      const size_t count = min((size_t)available, wav.size() - received);
      received += stream->readBytes(wav.data() + received, count);
    } else if (!http.connected()) {
      break;
    } else {
      delay(1);
    }
  }
  wav.resize(received);
  return received == (size_t)length;
}

void pollVoiceQueue() {
  if (WiFi.status() != WL_CONNECTED || accessInProgress || buttonPending) return;

  WiFiClientSecure pendingClient;
  pendingClient.setInsecure();
  HTTPClient pendingHttp;
  pendingHttp.setConnectTimeout(1200);
  pendingHttp.setTimeout(1500);
  const String pendingUrl = String(SERVER_URL) + "/voice/pending?last=" + String(lastVoiceId);
  if (!pendingHttp.begin(pendingClient, pendingUrl)) return;
  const int pendingCode = pendingHttp.GET();
  const String response = pendingCode == 200 ? pendingHttp.getString() : "";
  pendingHttp.end();
  if (buttonPending || response.length() == 0) return;

  JsonDocument doc;
  if (deserializeJson(doc, response) || !(doc["pending"] | false)) return;
  const uint32_t id = doc["id"] | 0U;
  const char* path = doc["url"] | "";
  if (!id || id <= lastVoiceId || !path[0]) return;
  if (accessInProgress || buttonPending) return;

  const String voiceUrl = String(SERVER_URL) + String(path);
  WiFiClientSecure voiceClient;
  voiceClient.setInsecure();
  HTTPClient voiceHttp;
  voiceHttp.setConnectTimeout(1500);
  voiceHttp.setTimeout(1800);
  if (!voiceHttp.begin(voiceClient, voiceUrl)) return;
  const int voiceCode = voiceHttp.GET();
  std::vector<uint8_t> wav, samples;
  const bool loaded = voiceCode == 200 && readVoiceFile(voiceHttp, wav);
  voiceHttp.end();
  if (buttonPending || !loaded) return; // retry this voice on a later poll

  // Consume malformed/unplayable messages once, but leave interrupted or
  // failed downloads pending so transient network trouble does not reboot or
  // permanently discard the voice notification.
  lastVoiceId = id;
  if (!decodeWavToDac(wav, samples)) return;
  if (!audioQueued && playingAudio.empty()) {
    queuedAudio.swap(samples);
    audioQueued = true;
  }
}

// ---------- behaviours ----------
void handleAccessAttempt() {
  Serial.println("\n>> Lid opened / access attempt! Asking server...");
  // Secure the latch while the new face is being checked.
  lockServo.write(LOCKED_ANGLE);
  servoUnlocked = false;
  unlockDeadline = 0;
  indicatorDeadline = 0;
  setLeds(true, true);                                   // both on = thinking
  String res = httpCall("POST", "/event", "{\"device\":\"esp32-box\",\"type\":\"access_attempt\"}");
  JsonDocument doc;
  if (res == "" || deserializeJson(doc, res)) {
    Serial.println("No valid answer from server -> staying locked");
    setLeds(true, false);
    indicatorDeadline = millis() + 1500;
    // A network failure is not an unauthorized-face alarm.
    return;
  }
  String decision = doc["decision"] | "unauthorized";
  String person = doc["person"] | "Unknown";
  unsigned long serverMs = doc["recognition_ms"] | 0;
  Serial.printf("Decision: %s (%s) | server processing: %lu ms\n",
                decision.c_str(), person.c_str(), serverMs);

  if (decision == "authorized") {
    setLeds(false, true);
    lockServo.write(UNLOCKED_ANGLE);
    servoUnlocked = true;
    unlockDeadline = millis() + 5000;
    indicatorDeadline = unlockDeadline;
    Serial.println("UNLOCKED for 5 seconds");
  } else {
    lockServo.write(LOCKED_ANGLE);                       // stays locked
    setLeds(true, false);
    indicatorDeadline = millis() + 1500;
    Serial.println("ACCESS BLOCKED - alarm!");
    tone(PIN_BUZZ, 1800, 450);                           // brief, non-blocking alarm
    alarmUntil = millis() + 450;
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

  Serial.print("Connecting to Wi-Fi");
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  const unsigned long wifiStarted = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - wifiStarted < 15000) {
    delay(100);
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf(" connected: %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println(" unavailable; access checks will stay locked.");
  }
  nextVoicePollMs = millis() + 10000;
  Serial.println("BiteGuard ready. Box is LOCKED. Press the lid button once.");
}

void loop() {
  if (servoUnlocked && (long)(millis() - unlockDeadline) >= 0) {
    lockServo.write(LOCKED_ANGLE);
    servoUnlocked = false;
    Serial.println("LOCKED again");
  }
  if (indicatorDeadline != 0 && (long)(millis() - indicatorDeadline) >= 0) {
    setLeds(false, false);
    indicatorDeadline = 0;
  }

  // Take one pending press atomically and ignore further taps while the
  // server decides, so repeat clicks cannot become delayed duplicate attempts.
  bool buttonPressed = false;
  noInterrupts();
  if (buttonPending && !accessInProgress) {
    buttonPending = false;
    accessInProgress = true;
    buttonPressed = true;
  }
  interrupts();

  if (buttonPressed) {
    handleAccessAttempt();
    nextAudioSampleUs = micros();
    noInterrupts();
    accessInProgress = false;
    buttonPending = false;
    interrupts();
  }

  // Move a downloaded voice into the playback buffer. The access button
  // always takes priority over audio.
  if (playingAudio.empty()) {
    if (audioQueued) {
      playingAudio.swap(queuedAudio);
      audioQueued = false;
    }
    if (!playingAudio.empty()) {
      audioPosition = 0;
      nextAudioSampleUs = micros();
      Serial.println("[voice] playing WAV through the ESP32 buzzer/DAC");
    }
  }
  if (!playingAudio.empty() && !accessInProgress && !buttonPending) {
    if ((int32_t)(millis() - alarmUntil) < 0) {
      nextAudioSampleUs = micros() + 125;
    } else if ((int32_t)(micros() - nextAudioSampleUs) >= 0) {
      dacWrite(PIN_BUZZ, playingAudio[audioPosition++]);
      nextAudioSampleUs = micros() + 125; // 8 kHz sample rate
      if (audioPosition >= playingAudio.size()) {
        dacWrite(PIN_BUZZ, 128);
        playingAudio.clear();
        audioPosition = 0;
        Serial.println("[voice] playback complete");
      }
    }
  }
  if (!playingAudio.empty() && !accessInProgress && !buttonPending) {
    delayMicroseconds(10);
  } else {
    delay(5);
  }

  // Poll every 10 seconds only while idle. Since this runs in loop(), there
  // is no worker task to starve the ESP32 watchdog; a press during a download
  // aborts the read and gets handled on the next loop iteration.
  if (!accessInProgress && !buttonPending && playingAudio.empty() && !audioQueued &&
      (int32_t)(millis() - nextVoicePollMs) >= 0) {
    nextVoicePollMs = millis() + 10000;
    pollVoiceQueue();
  }
}
