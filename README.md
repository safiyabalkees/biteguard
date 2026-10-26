# BiteGuard — Smart Snack Box

BiteGuard connects a lid sensor to a Flask server, performs face matching, and sends decisions and live camera information to an Android app.

```
server/       Flask API, camera bridge, face matching, benchmark utility
wokwi/        ESP32 firmware and circuit for Wokwi
flutter_app/  Android app source
```

## Requirements

- Python 3.10 or newer (Python 3.13 is the recommended setup here).
- Flutter stable SDK and Android Studio.
- Android SDK platform and build tools, Android SDK Command-line Tools, Platform-Tools, NDK side by side, and CMake. Use the latest stable versions offered by Android Studio's SDK Manager.
- JDK 17 or newer for the Android Gradle build. The checked-in Android Gradle Plugin 9.1.0 uses Gradle 9.3.1 through the Gradle wrapper.
- Internet access during setup and the first server start. Gradle downloads Android build components, Flutter downloads Dart packages, and the server downloads the face models.
- Optional: Cloudflare `cloudflared` for remote access or Wokwi, and a webcam for live camera input.

On Windows, after installing Android Studio and the Android SDK, accept the SDK licenses and check the toolchain:

```powershell
flutter doctor --android-licenses
flutter doctor
flutter devices
```

Connect an Android phone with USB debugging enabled or start an Android emulator. Flutter's Android setup guide has current SDK installation details: <https://docs.flutter.dev/platform-integration/android/setup>.

## Run the server

Windows PowerShell:

```powershell
cd server
py -3 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python app.py
```

If PowerShell blocks script activation, run the venv's Python directly instead:

```powershell
cd server
py -3 -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe app.py
```

Linux or macOS:

```bash
cd server
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python app.py
```

The server listens on `http://localhost:5000` and `/health` reports face-engine and camera status. On first start, it downloads the SFace and YuNet ONNX models and verifies their SHA-256 checksums. Model files are cached under `server/models/` and do not need to be committed.

`CAMERA_MODE` controls camera input:

- `upload` (default): use the browser camera bridge at `http://localhost:5000/camera`; press **Start** and leave the page open.
- `webcam`: use a webcam attached to the server computer.
- `folder`: cycle images placed in `server/test_images/`.
- `auto`: use uploads, then fall back to the image folder.

Set a mode before starting the server, for example in PowerShell: `$env:CAMERA_MODE = "webcam"; python app.py`. In Bash: `CAMERA_MODE=webcam python app.py`.

To enroll people, use **People → Add person** in the phone app, or put photos in `server/authorized/<name>/` (allowed) and `server/known_others/<name>/` (known, not allowed). After changing files manually, reload with PowerShell:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:5000/reload
```

For camera testing without a webcam, run `python simulate_camera.py photo.jpg --stream` from `server/`.

## Run the Android app

```powershell
cd flutter_app
flutter pub get
flutter devices
flutter run -d <android-device-id>
```

Replace `<android-device-id>` with the Android device ID shown by `flutter devices`. This avoids selecting the Windows desktop target by default.
If Flutter reports that the Visual Studio Windows desktop toolchain is missing, that warning does not block Android builds when you select an Android device explicitly.

Set the server address in the app's **Settings**, then choose **Save & test**:

- Android emulator: `http://10.0.2.2:5000` (the app defaults to this address).
- Physical phone on the same Wi-Fi: `http://<computer-LAN-IP>:5000`. Allow Python through the computer firewall if prompted.
- Phone connecting remotely: use the current Cloudflare tunnel URL.

Build a single 64-bit ARM release APK:

```powershell
flutter build apk --release --target-platform android-arm64
```

The output is `flutter_app/build/app/outputs/flutter-apk/app-release.apk`. To copy it to the project folder for direct installation, run from `flutter_app/`:

```powershell
Copy-Item build\app\outputs\flutter-apk\app-release.apk ..\BiteGuard-Android-arm64.apk
```

The current Gradle configuration signs release builds with the debug key for easy sideloading. Create and configure a private release key before publishing on an app store. Alerts are delivered while the app is running; push notifications when it is closed require a push-notification service such as Firebase Cloud Messaging.

## Remote access with Cloudflare Quick Tunnel

Quick Tunnels create a temporary public HTTPS URL for a local server. They do not require a Cloudflare account or a domain. Install `cloudflared` from [Cloudflare's official downloads page](https://developers.cloudflare.com/tunnel/downloads/). On Windows, download and install the 64-bit MSI, then open a new PowerShell window and confirm it is available:

```powershell
cloudflared --version
```

If PowerShell says the command is not found, reopen the terminal after installation or run `cloudflared.exe` from its install folder.

Start BiteGuard in one PowerShell window and leave it running. In a second window, start the tunnel:

```powershell
cloudflared tunnel --url http://localhost:5000
```

Copy the generated `https://….trycloudflare.com` URL into the phone app's **Settings**. For Wokwi, copy it into `SERVER_URL` in `wokwi/sketch.ino`. The tunnel terminal must stay open while the app or Wokwi uses it. Stop the tunnel with Ctrl+C. Its hostname is temporary and changes when restarted, so update both clients with the new URL.

Quick Tunnels are for development and testing. The URL is public, and this demo server has no login. Anyone who has the URL can reach the server endpoints. Do not expose private face photos or personal data. For stable production hosting, use a named Cloudflare Tunnel with an access policy instead.

## Wokwi ESP32 simulation

Wokwi's ESP32 runs in the cloud, so it needs the public Cloudflare URL described above. Wokwi libraries: `ESP32Servo` and `ArduinoJson`.

| Part | ESP32 pin |
|---|---|
| Button (other side → GND) | D4 |
| Servo signal (V+ → VIN, GND → GND) | D18 |
| Buzzer + (other side → GND) | D25 (DAC) |
| Red LED via 220Ω | D19 |
| Green LED via 220Ω | D26 |

The Wokwi sketch reports round-trip time for access attempts. `python server/bench.py [url]` measures server performance against a local or tunnel URL.

## Voice messages

`POST /voice` accepts PCM WAV in the multipart field `audio`, converts it to 8 kHz / 8-bit mono (maximum 8 seconds), and queues it for the ESP32 DAC. The app does not currently include a record button.

## Important notes

- A Cloudflare Quick Tunnel is public and this demo server has no login or access token. Anyone with the URL can reach its endpoints. Use it only for demos; do not expose private face photos or personal data.
- Face matching is a convenience demo, not a security system. A photo can fool it.
- Face enrollment photos, captures, voice files, logs, generated models, and local Android SDK paths are runtime or machine-specific data and should not be committed.
