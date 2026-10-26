BiteGuard source bundle

Included: Flask server source, Wokwi ESP32 sketch/circuit, Flutter app source and platform configuration.
Excluded: installed dependencies, build/cache outputs, model weights, logs, captures, voice files, test photos, and enrolled face photos.

Setup: see README.md. Install server packages with `pip install -r server/requirements.txt`; Flutter packages with `flutter pub get`. The face-recognition model files are downloaded by the server on first run. Enrolled face images are private runtime data and are not included; add/enroll them on the server after setup.

The Wokwi sketch contains a Cloudflare URL placeholder. See README.md for installing cloudflared and starting a Quick Tunnel, then replace the placeholder with the current URL before running Wokwi. Quick Tunnel URLs change when the tunnel restarts. The Android app defaults to the emulator's local server address; set the current Quick Tunnel URL in Settings for remote access.
