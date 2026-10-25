# 🍪 BiteGuard — Smart Snack Box (simulation package)

Sensor → Wi-Fi → Flask → face recognition → decision → phone alert → optional ESP32 voice playback.

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
You can record a voice message in the phone app. The Wokwi ESP32 fetches it independently of
access attempts and outputs low-rate PCM through its DAC to the simulated piezo buzzer; this is
only a rough speaker simulation, not high-quality audio.

## 1. Run the server
```powershell
cd server
python -m venv venv
venv\Scripts\activate          # Windows   (Mac/Linux: source venv/bin/activate)
pip install -r requirements.txt
$env:CAMERA_MODE = "upload"     # Use `export CAMERA_MODE=upload` on Mac/Linux
python app.py
```
Open http://localhost:5000 — you should see the dashboard. `/health` reports whether the OpenCV SFace
recognition engine is active. On first start, the server downloads the SFace and YuNet model files
into `server/models/` and verifies their SHA-256 hashes. An internet connection is needed for that
first download; subsequent starts use the local files.

### Wokwi presentation flow
Keep the ESP32 simulation as the device under demonstration. The laptop camera bridge supplies
frames because Wokwi does not provide a camera. The simulated button triggers the actual Flask
face check, and the ESP32 simulation drives the servo, LEDs, and buzzer from the response.

If the model download fails, the server still starts, but face checks fail closed: every attempt is
unauthorized. Check the startup log and try `POST /reload` after network access is available.

### Enrol faces
```
server/authorized/safiya/1.jpg 2.jpg 3.jpg     <- authorized (use 3-5 clear photos each)
server/authorized/mother/1.jpg ...
server/known_others/sister/1.jpg 2.jpg         <- optional: recognized by name, NOT authorized
```
Use one face per enrollment photo. Restart the server (or `curl -X POST localhost:5000/reload`).
The recognition threshold defaults to the OpenCV Zoo SFace cosine threshold (`0.363`); set
`FACE_MATCH_THRESHOLD` to tune it against the project's own photos. The detector confidence can be
adjusted with `FACE_DETECT_THRESHOLD` (default `0.5`).

### Laptop camera bridge
Open http://localhost:5000/camera in one browser tab, press **Start camera**, and allow camera
access. Keep it open with the Wokwi simulation in another tab. Wait for “Camera live” before
pressing the simulated lid button. The bridge uploads a new, downscaled JPEG several times per
second; Wokwi sends `/event`, Flask recognizes the latest frame, then Wokwi updates its servo,
LEDs, and buzzer from the decision.
Uploaded frames expire after 5 seconds by default (`CAMERA_UPLOAD_TTL` can change that).

Other server camera modes are `auto` (fresh upload, laptop webcam, then test-image folder),
`webcam`, `folder`, and `upload`. For Wokwi, use `upload` to avoid silently substituting a
different image if the browser camera stops sending frames.

## 2. Test with no Wokwi/app at all
```bash
python simulate_camera.py some_photo.jpg --trigger
```
Dashboard should show an Authorized/Unauthorized entry.

## 3. Wokwi (ESP32 simulation)
Wokwi's ESP32 runs in the cloud, so it can't see `localhost`. Keep Flask and the laptop camera
bridge running, then expose Flask with the free Cloudflare Quick Tunnel:
```bash
cloudflared tunnel --url http://localhost:5000
```
Copy the `https://….trycloudflare.com` URL printed by cloudflared. In the existing BiteGuard Wokwi
project, set `SERVER_URL` in `sketch.ino` to that URL (no trailing slash). Keep the simulation and
the laptop camera bridge open. Wait for “Camera live”, then click the blue lid button. The ESP32
posts the attempt through Wi-Fi and the tunnel; Flask runs recognition and returns the decision;
the simulated servo, LEDs, and buzzer respond. The Quick Tunnel URL changes when cloudflared restarts.

If creating a new Wokwi project, choose **ESP32** (Arduino), paste in `wokwi/sketch.ino` and
`wokwi/diagram.json`, then add `ESP32Servo` and `ArduinoJson` through the Library Manager.
(If a wire in the diagram looks unconnected, just re-draw it in the editor using the pin table below.)

| Part | ESP32 pin |
|---|---|
| Button (other side → GND) | D4 |
| Servo signal (V+ → VIN, GND → GND) | D18 |
| Buzzer + (other side → GND) | D25 (ESP32 DAC output) |
| Red LED via 220Ω | D19 |
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
