# 🍪 BiteGuard — Smart Snack Box (simulation package)

Sensor → Wi-Fi → Flask → face recognition → decision → phone alert → voice reply → box speaker.

```
biteguard/
├── server/        Flask backend + face recognition + camera simulator
├── wokwi/         ESP32 firmware + circuit (paste into wokwi.com)
└── flutter_app/   Phone app (alerts, photo, record & send voice)
```

## How it behaves
The box is **locked by default**. Pressing the button in Wokwi = "someone opened/lifted the lid".
The server photographs the person and runs face recognition:
- **Authorized** → green LED, servo unlocks for 5 s, entry logged.
- **Unauthorized / unknown / no face** → stays locked, red LED, buzzer, alert to the phone.
You open the alert, record a voice message, and it is played back ("box speaker").

## 1. Run the server
```bash
cd server
python -m venv venv
venv\Scripts\activate          # Windows   (Mac/Linux: source venv/bin/activate)
pip install -r requirements.txt
python app.py
```
Open http://localhost:5000 — you should see the dashboard. `/health` shows whether face recognition is active.

**If `face_recognition` fails to install** (dlib needs CMake/C++ build tools): delete that line from
requirements.txt and continue. The server then only *detects* faces and treats every face as
unauthorized — fine for testing the flow, but install it for the real demo
(Windows tip: `pip install cmake` first, and install "Desktop development with C++" build tools;
or use Python 3.10/3.11 with Anaconda: `conda install -c conda-forge dlib`).

### Enrol faces
```
server/authorized/safiya/1.jpg 2.jpg 3.jpg     <- authorized (use 3-5 clear photos each)
server/authorized/mother/1.jpg ...
server/known_others/sister/1.jpg 2.jpg         <- optional: recognised by name, NOT authorized
```
Restart the server (or `curl -X POST localhost:5000/reload`).

### What counts as "the camera"
Priority for each access event: (1) a photo uploaded in the last 30 s via `simulate_camera.py`,
(2) your laptop webcam, (3) cycling through `server/test_images/`.
Force a mode with `CAMERA_MODE=webcam` or `folder`.

## 2. Test with no Wokwi/app at all
```bash
python simulate_camera.py some_photo.jpg --trigger
```
Dashboard should show an Authorized/Unauthorized entry.

## 3. Wokwi (ESP32 simulation)
Wokwi's ESP32 runs in the cloud, so it can't see `localhost`. Expose Flask with ngrok:
```bash
ngrok http 5000          # copy the https://xxxx.ngrok-free.app URL
```
1. Go to https://wokwi.com → New Project → **ESP32** (Arduino).
2. Replace `sketch.ino` and `diagram.json` with the files in `wokwi/`; add `libraries.txt`
   contents via the Library Manager (**ESP32Servo**, **ArduinoJson**).
3. In `sketch.ino` set `SERVER_URL` to your ngrok URL. Press ▶.
4. Click the blue button = lid opened. Watch the Serial Monitor, LEDs, servo, buzzer.
(If a wire in the diagram looks unconnected, just re-draw it in the editor using the pin table below.)

| Part | ESP32 pin |
|---|---|
| Button (other side → GND) | D4 |
| Servo signal (V+ → VIN, GND → GND) | D18 |
| Buzzer + (other side → GND) | D19 |
| Red LED via 220Ω | D25 |
| Green LED via 220Ω | D26 |

## 4. Flutter app
```bash
cd flutter_app
flutter create .           # generates android/ ios/ ... (keeps our lib/ and pubspec.yaml)
flutter pub get
```
Then follow `ANDROID_SETUP.txt` (permissions + cleartext). Run on an emulator or a USB phone:
```bash
flutter run
```
Tap ⚙ and set the server URL:
- Android emulator + local server → `http://10.0.2.2:5000`
- Real phone → your ngrok `https://…` URL (or `http://<laptop-LAN-IP>:5000` on same Wi-Fi)

The app polls every 2 s; an unauthorized event pops up a red banner → tap **VIEW** → **Record voice** →
**Send Voice Message**. The laptop plays it (simulated box speaker) and the Wokwi buzzer plays a jingle.

## Demo script (2 minutes)
1. Show locked box + empty app. 2. Upload an authorized face, press button → green LED, servo unlocks.
3. Upload sister's/unknown face, press button → red LED, alarm, phone alert with photo.
4. Record "Hey, keep your hands off my snacks!" → sent → heard on laptop, jingle on the ESP32.

## Limits & next steps
- Voice audio: simulated by the laptop speaker. Real hardware: ESP32 downloads `/voice/file/<name>`
  and plays it through a MAX98357A I2S amp.
- Alerts reach the phone by polling; for alerts anywhere use Firebase Cloud Messaging.
- Face recognition isn't foolproof (photos can fool it); fine for a snack box, not for security.
- Real camera: ESP32-CAM can POST a JPEG to `/image` just before the box calls `/event`.
