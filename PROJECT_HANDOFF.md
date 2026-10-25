# BiteGuard Project Handoff

Updated: 2026-10-25  
Repository: `C:\Users\Anas Meerasahib\Documents\Project\bite gaurd\biteguard`  
Remote: `https://github.com/safiyabalkees/biteguard.git`

This file is a context handoff for the next Codex chat. Read it together with the current source files; the repository is the source of truth for implementation details. This report summarizes conversation decisions and currently observed state, and does not certify that the whole system currently runs.

## User goals and working preferences

- This is a final-year open-elective/fun IoT simulation project, not a commercial product. Do not treat it as a production security system.
- The intended demo should feel like a smart snack box: an access/lid attempt triggers face recognition; the simulated lock/servo, LEDs, buzzer, phone alerts, and voice feature respond.
- Wokwi remains part of the project. The user objected when an alternate environment removed ESP32/hardware behavior from the demonstration. Keep the ESP32 simulation in the project flow.
- Wokwi does not provide the camera path needed here. The current design bridges a laptop/browser camera to Flask; Wokwi's simulated ESP32 reaches Flask through an internet tunnel.
- User said: **do not push code to the remote until everything works**. Do not push or commit unless later explicitly asked. The current working tree has many uncommitted changes.
- When the user asks a question, answer it; do not make unrelated edits or silently decide on additional work. Implement when asked.
- Keep troubleshooting clear and step-by-step. User prefers the full replacement sketch when asking where to paste code, rather than fragments.

## What the system is

Three connected parts:

1. **Flask server (`server/`)** on the laptop. It accepts access attempts, obtains a camera frame, recognizes a face, logs decisions/alerts, and stores/serves voice WAV messages.
2. **Wokwi ESP32 (`wokwi/`)** represents the device. Button = lid/access attempt; servo = lock; LEDs and buzzer communicate results. It calls Flask over Wi-Fi and a Cloudflare Quick Tunnel URL.
3. **Flutter Android app (`flutter_app/`)** polls server alerts, displays access events, and can record/upload a voice WAV.

The server endpoints documented in `server/app.py` include `POST /event`, `POST /image`, `GET /alerts`, `GET /captures/<file>`, `POST /voice`, `GET /voice/pending`, `GET /voice/file/<name>`, and `POST /reload`. The README also documents `/health` and the `/camera` browser bridge.

## Decisions and history

### Face recognition

- Dlib was considered alongside alternatives; the project uses **OpenCV YuNet for face detection and OpenCV SFace for face embeddings/matching**. The health response previously reported `face_engine: "opencv_sface"` and recognition enabled.
- The enrolled directories currently observed are `server/authorized/owner/` and `server/known_others/other_person/`. The user supplied personal face photos and a separate other-person image, then added photos taken in different locations for comparison.
- This is a small demonstration dataset, not a measured production-quality evaluation. False matches and failures are possible. Keep the demo’s threshold behavior in mind (`FACE_MATCH_THRESHOLD`, documented default `0.363`).
- Model files are downloaded/checked by the server on first use according to README; network may be needed once.

### Camera and Wokwi

- The browser camera bridge is at `http://localhost:5000/camera`. It uploads frames to Flask; it must be started and allowed to use the laptop camera before a Wokwi access attempt.
- Wokwi cloud simulation cannot reach laptop `localhost`; Flask needs to be exposed via a tunnel. The user initially selected ngrok, but ngrok HTTPS from ESP32/Wokwi had TLS EOF failures (`-29312`) even though browser access worked. Plain HTTP redirected with `307` back to HTTPS.
- User stated there must be no payment; Cloudflare Quick Tunnel was chosen instead. A prior Wokwi test successfully made HTTPS `/health` return HTTP 200 and JSON like `{"face_engine":"opencv_sface","face_recognition":true,"faces":6,"ok":true}`.
- Quick Tunnel URLs are temporary and change when restarted. The URL currently embedded in `wokwi/sketch.ino` and `flutter_app/lib/main.dart` is `https://subsequent-programmers-gather-administrative.trycloudflare.com`. Its **current availability has not been rechecked in this handoff**. Check the current tunnel before debugging app behavior; do not assume this URL is still live.
- A prior URL, `hawk-individually-wan-mil.trycloudflare.com`, had a DNS failure from the machine and was superseded. Do not use it.
- Wokwi circuit pin map in `wokwi/diagram.json` / README:
  - button GPIO4 to GND
  - servo PWM GPIO18, V+ to VIN, GND to GND
  - buzzer GPIO25 (ESP32 DAC)
  - red LED GPIO19 through 220-ohm resistor
  - green LED GPIO26 through 220-ohm resistor
- Flow: press lid button -> ESP32 posts `/event` -> server checks the newest camera frame -> authorized means servo unlocks for 5 seconds/green indicator; unknown or unauthorized means stays locked/red indication and short alarm.
- The user emphasized that the server is not the simulated box: the ESP32/button/servo/LED/buzzer must remain integral to the demo.

### Wokwi voice playback and watchdog

- Voice messages are uploaded by the Flutter app as WAV; the ESP32 polls `/voice/pending` and downloads `/voice/file/...`.
- The firmware converts supported PCM WAV (8/16-bit, mono/stereo, source rate 8–48 kHz) to low-rate 8 kHz DAC samples and sends them on GPIO25. It is a rough Wokwi buzzer simulation, not high-quality speech reproduction. A real ESP32 speaker would need suitable audio output hardware (earlier README mentions MAX98357A I2S amp).
- Earlier firmware used `xTaskCreatePinnedToCore(..., "voice-poll", ..., core 0)` and caused this WDT report: CPU0 idle task starved, CPU0 running `voice-poll`, task watchdog abort/reboot. User asked why it rebooted, then explicitly asked for voice polling/retry behavior without rebooting.
- **Latest requested implementation** in `wokwi/sketch.ino`: remove the separate pinned worker and run `pollVoiceQueue()` cooperatively from `loop()` every 10 seconds when idle. Poll/network failure simply returns and is retried on a later interval. A lid press during WAV reading aborts the read; interrupted/failed downloads do not advance `lastVoiceId`, allowing a later retry. A successful but malformed WAV is consumed once to avoid retry loops. Button access takes priority over audio. No task pinned to CPU0 remains in the current sketch.
- This code was edited but **has not yet been compiled or run in Wokwi since the edit**. Next useful action is to run the simulation and watch serial output for: no WDT reset, polling retries after endpoint failure, button still being serviced, and normal voice playback when available.
- Avoid claiming “it works” until that run happens. User asked only to implement this latest change; do not push it.

### Flutter app and Android setup

- Existing app is in `flutter_app/`, not a newly generated app. It uses Flutter, HTTP, `record`, `path_provider`, and `shared_preferences` (see `pubspec.yaml`).
- User asked to reduce app size (reported APK around 153 MB), add system notifications for access attempts, remove emoji except BiteGuard name/logo, and use the cookie image as app icon. The current tree contains modifications in Dart/Android/build files and an Android drawable/XML icon, but no new build/install verification has been done in this handoff. Inspect current implementation rather than assuming every requested visual detail is complete.
- Current `main.dart` polls `/alerts` every 2 seconds and invokes a native Android notification MethodChannel (`biteguard/notifications`). The app stores its base URL in shared preferences. The default is currently the same trycloudflare URL above.
- Flutter SDK zip was downloaded/extracted by the user; their reported Flutter version: 3.47.8. Earlier `flutter doctor` output showed Android SDK/cmdline-tools missing, then user said setup was done. Android emulator caused the laptop to freeze. Do not launch a heavyweight emulator without the user explicitly asking; use a connected physical Android phone/USB if available. Chrome was not detected; Visual Studio was missing, but those are not needed for Android APKs.
- The current SDK/Android SDK path and `flutter doctor` status have not been verified in this session.

### Velxio detour and cleanup

- User briefly asked to try Velxio after signing in, but later directed: “delete and undo all remnants of vloxi”; the user clarified this meant code/zip/leftover files, not clearing browser tabs.
- User asked whether Wokwi ZIP matched the working folder and then asked to delete the ZIP. The ZIP was removed at that time. Current file inventory has not been independently checked for all Velxio remnants in this handoff. Do not delete anything further unless asked; do not clear browser tabs.

## Current repository state (observed 2026-10-25)

`git status --short` showed many modified/untracked files. Important modified paths included:

- `.gitignore`, `README.md`
- `server/app.py`, `server/requirements.txt`
- `wokwi/sketch.ino`, `wokwi/diagram.json`, `wokwi/libraries.txt`
- `flutter_app/lib/main.dart`, `pubspec.lock`, Android Gradle/manifest/Kotlin files, and generated plugin files
- untracked Android icon/proguard files
- several untracked Flask/cloudflared log files

Do not reset or overwrite this work. No code has been pushed by this chat. Review `git status` and the diff before changing anything. The README has some known stale text: it still describes old laptop audio/jingle behavior in the demo section, while current firmware attempts DAC playback; it also contains old/ngrok-oriented header hints in code. Reconcile only if asked or as part of a specifically requested documentation update.

## Environment / troubleshooting lessons

- Flask local port is 5000. User previously saw `localhost:5000` working. If `/camera` times out, first check whether Flask is running and responding at `/health`, then check the camera bridge; browser errors alone do not prove the server code is broken.
- ESP32/Wokwi HTTPS requires using the hostname for SNI. Generic ESP32 TLS worked against `example.com`; ngrok-specific TLS produced EOF. Cloudflare Quick Tunnel worked in an earlier Wokwi health test.
- Wokwi serial logs include routine ESP32 boot messages. The significant failure was specifically `task_wdt` naming `voice-poll`, followed by `SW_CPU_RESET`.
- Voice pending endpoint can return no pending message; that should be a normal no-op. Network errors should be retried without rebooting and should not block lid/access handling for long.

## Next-chat checklist

1. Read this handoff and inspect current `git status`/diff before editing.
2. Do not push. User wants everything working first.
3. Verify the Flask server and active Cloudflare URL before using a tunnel URL; these Quick Tunnel links expire/change.
4. In Wokwi, test the current sketch after the cooperative polling change; specifically provoke an unavailable voice endpoint and a lid press during voice download. Confirm no watchdog reboot and access still has priority.
5. If fixing compile/runtime errors, provide a full `wokwi/sketch.ino` replacement when the user needs pasteable code.
6. For Flutter work, avoid emulator due prior laptop freeze; inspect if a physical Android device is connected. Confirm SDK and Android SDK paths before relying on `flutter doctor`.
7. Keep explanations factual: distinguish prior successful checks from anything newly verified.
