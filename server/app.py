"""
BiteGuard backend (Flask)
Endpoints
  POST /event          ESP32 reports an access attempt -> returns decision
  POST /image          Camera (real ESP32-CAM or simulate_camera.py) uploads a photo
  GET  /alerts         Flutter app reads alert history
  GET  /captures/<f>   Captured images
  POST /voice          Flutter app uploads a voice message (WAV)
  GET  /voice/pending  ESP32 polls for a new voice message
  POST /reload         Re-read enrolled faces
  GET  /               Simple web dashboard (handy for testing)
"""
import glob
import hashlib
import itertools
import json
import os
import shutil
import threading
import time
from datetime import datetime

import cv2
import numpy as np
import requests
from flask import Flask, jsonify, request, send_from_directory, render_template_string

BASE = os.path.dirname(os.path.abspath(__file__))
AUTH_DIR = os.path.join(BASE, "authorized")          # authorized/<name>/*.jpg
OTHERS_DIR = os.path.join(BASE, "known_others")      # known_others/<name>/*.jpg (known, NOT authorized)
CAPTURE_DIR = os.path.join(BASE, "captures")
LIVE_CAMERA_PATH = os.path.join(CAPTURE_DIR, ".live_camera.jpg")
VOICE_DIR = os.path.join(BASE, "voice")
TEST_DIR = os.path.join(BASE, "test_images")
MODEL_DIR = os.path.join(BASE, "models")
LOG_FILE = os.path.join(BASE, "alerts.json")
for d in (CAPTURE_DIR, VOICE_DIR):
    os.makedirs(d, exist_ok=True)

CAMERA_MODE = os.environ.get("CAMERA_MODE", "auto")  # auto | webcam | folder | upload
UPLOAD_TTL = float(os.environ.get("CAMERA_UPLOAD_TTL", "5"))
FACE_MATCH_THRESHOLD = float(os.environ.get("FACE_MATCH_THRESHOLD", "0.363"))
FACE_DETECT_THRESHOLD = float(os.environ.get("FACE_DETECT_THRESHOLD", "0.5"))
MAX_DETECT_SIZE = 640

# OpenCV Zoo model assets. Hash checks make moving branch URLs fail safely if
# an upstream file changes, rather than silently running a different model.
FACE_MODELS = {
    "sface": (
        "face_recognition_sface_2021dec.onnx",
        "https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    ),
    "yunet_2023": (
        "face_detection_yunet_2023mar.onnx",
        "https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    "yunet_2026": (
        "face_detection_yunet_2026may.onnx",
        "https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2026may.onnx",
        "ebafce4e3c118d6554634be5c27ab333b4c047a9a8c3faf1d7cf93101c22f0f0",
    ),
}

app = Flask(__name__)
lock = threading.Lock()
event_lock = threading.Lock()
face_model_lock = threading.Lock()

# ------------------------------------------------------------------ face DB
known = []   # list of (normalized embedding, name, authorized)
face_detector = None
face_recognizer = None
FACE_ENGINE_READY = False


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_model(model_key):
    filename, url, expected_hash = FACE_MODELS[model_key]
    os.makedirs(MODEL_DIR, exist_ok=True)
    path = os.path.join(MODEL_DIR, filename)
    if os.path.isfile(path) and _sha256(path) == expected_hash:
        return path

    temp_path = path + ".download"
    print(f"[info] downloading face model {filename}")
    digest = hashlib.sha256()
    try:
        with requests.get(url, stream=True, timeout=(10, 120)) as response:
            response.raise_for_status()
            with open(temp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        digest.update(chunk)
        if digest.hexdigest() != expected_hash:
            raise RuntimeError(f"SHA-256 verification failed for {filename}")
        os.replace(temp_path, path)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise
    return path


def _load_face_models():
    global face_detector, face_recognizer
    if face_detector is not None and face_recognizer is not None:
        return

    sface_path = _ensure_model("sface")
    # OpenCV 5 needs the dynamic-shape YuNet export. OpenCV 4 uses the
    # fixed-shape export from the same OpenCV Zoo model directory.
    model_key = "yunet_2026" if int(cv2.__version__.split(".", 1)[0]) >= 5 else "yunet_2023"
    yunet_path = _ensure_model(model_key)
    face_detector = cv2.FaceDetectorYN.create(
        yunet_path, "", (320, 320), FACE_DETECT_THRESHOLD, 0.3, 5000
    )
    face_recognizer = cv2.FaceRecognizerSF.create(sface_path, "")


def _face_embeddings(image):
    """Detect and align faces, returning normalized SFace embeddings."""
    if image is None or image.size == 0 or face_detector is None or face_recognizer is None:
        return []

    height, width = image.shape[:2]
    scale = min(1.0, MAX_DETECT_SIZE / max(height, width))
    detector_image = cv2.resize(image, None, fx=scale, fy=scale) if scale < 1.0 else image
    with face_model_lock:
        face_detector.setInputSize((detector_image.shape[1], detector_image.shape[0]))
        _, faces = face_detector.detect(detector_image)
        if faces is None:
            return []

        embeddings = []
        for face in faces:
            # YuNet returns a box, five landmarks, then a confidence score.
            full_size_face = face.copy()
            full_size_face[:14] /= scale
            aligned = face_recognizer.alignCrop(image, full_size_face)
            embedding = face_recognizer.feature(aligned).reshape(-1).astype(np.float32)
            norm = np.linalg.norm(embedding)
            if norm > 0:
                embeddings.append(embedding / norm)
        return embeddings


def load_faces():
    global known, FACE_ENGINE_READY
    known = []
    FACE_ENGINE_READY = False
    try:
        _load_face_models()
    except Exception as e:
        print(f"[error] OpenCV SFace is unavailable; access checks will fail closed: {e}")
        return

    new_known = []
    for root, authorized in ((AUTH_DIR, True), (OTHERS_DIR, False)):
        for person_dir in sorted(glob.glob(os.path.join(root, "*"))):
            if not os.path.isdir(person_dir):
                continue
            name = os.path.basename(person_dir).capitalize()
            for img in glob.glob(os.path.join(person_dir, "*")):
                if not img.lower().endswith((".jpg", ".jpeg", ".png")):
                    continue
                image = cv2.imread(img)
                encs = _face_embeddings(image)
                if len(encs) == 1:
                    new_known.append((encs[0], name, authorized))
                elif not encs:
                    print(f"[warn] no face found in {img}")
                else:
                    print(f"[warn] multiple faces found in {img}; skipping enrollment photo")
    known = new_known
    FACE_ENGINE_READY = True
    print(f"[info] loaded {len(known)} face samples")


def identify(path):
    """Returns (status, name, authorized). status: ok | unknown | no_face"""
    if not FACE_ENGINE_READY:
        return "unknown", None, False
    image = cv2.imread(path)
    encs = _face_embeddings(image)
    if not encs:
        return "no_face", None, False

    best = None
    for enc in encs:
        for known_enc, name, authorized in known:
            similarity = float(np.dot(known_enc, enc))
            if similarity >= FACE_MATCH_THRESHOLD and (best is None or similarity > best[0]):
                best = (similarity, name, authorized)
    if best:
        return "ok", best[1], best[2]
    return "unknown", None, False

# ------------------------------------------------------------------ camera
latest_upload = {"path": None, "time": 0.0}
_folder_cycle = None


def grab_image():
    """Pick the image for this access attempt."""
    global _folder_cycle
    # 1) A fresh upload from the laptop camera bridge or simulate_camera.py.
    with lock:
        uploaded = latest_upload.copy()
    if uploaded["path"] and time.time() - uploaded["time"] < UPLOAD_TTL:
        return uploaded["path"]
    # In upload mode, do not silently substitute a webcam or test photo. This
    # is the deterministic mode used by the Wokwi + laptop-camera demo.
    if CAMERA_MODE == "upload":
        return None
    # 2) laptop webcam
    if CAMERA_MODE in ("auto", "webcam"):
        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            for _ in range(5):
                cap.read()
            ok, frame = cap.read()
            cap.release()
            if ok:
                path = os.path.join(CAPTURE_DIR, f"cap_{int(time.time()*1000)}.jpg")
                cv2.imwrite(path, frame)
                return path
        cap.release()
    # 3) cycle through test_images/
    if CAMERA_MODE in ("auto", "folder"):
        files = sorted(f for f in glob.glob(os.path.join(TEST_DIR, "*"))
                       if f.lower().endswith((".jpg", ".jpeg", ".png")))
        if files:
            if _folder_cycle is None:
                _folder_cycle = itertools.cycle(files)
            return next(_folder_cycle)
    return None

# ------------------------------------------------------------------ state
alerts = []
voice_state = {"id": 0, "file": None}


def load_log():
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE) as f:
                alerts.extend(json.load(f))
        except Exception:
            pass


def save_log():
    with open(LOG_FILE, "w") as f:
        json.dump(alerts, f, indent=2)


# ------------------------------------------------------------------ routes
CAMERA_BRIDGE = r"""
<!doctype html>
<html lang="en">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BiteGuard laptop camera</title>
<style>
  body { font: 16px system-ui, sans-serif; background: #111; color: #eee;
         max-width: 760px; margin: 28px auto; padding: 0 16px; }
  video { display: block; width: 100%; max-height: 65vh; background: #222;
          object-fit: contain; transform: scaleX(-1); border-radius: 12px; }
  button { padding: 10px 16px; margin: 12px 8px 12px 0; cursor: pointer; }
  #status { color: #9ee493; }
</style>
<h1>BiteGuard camera bridge</h1>
<p>Keep this page open. It sends a fresh laptop-camera frame to BiteGuard about once a second.
   Press the button in Wokwi to run recognition on the latest frame.</p>
<video id="preview" autoplay playsinline muted></video>
<button id="start">Start camera</button><button id="stop" disabled>Stop camera</button>
<p id="status">Camera stopped.</p>
<canvas id="frame" hidden></canvas>
<script>
const video = document.getElementById('preview');
const canvas = document.getElementById('frame');
const statusLine = document.getElementById('status');
let stream = null;
let active = false;
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

async function uploadFrames() {
  while (active) {
    if (video.videoWidth && video.videoHeight) {
      const scale = Math.min(1, 640 / video.videoWidth);
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);
      canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.82));
      if (blob && active) {
        const data = new FormData();
        data.append('image', blob, 'laptop-camera.jpg');
        try {
          const response = await fetch('/camera/frame', { method: 'POST', body: data });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const result = await response.json();
          statusLine.textContent = `Camera live — latest frame sent (${result.file}). Press the Wokwi button.`;
        } catch (error) {
          statusLine.textContent = `Upload failed: ${error.message}`;
        }
      }
    }
    // Keep the frame in the backend fresh without saturating the local server.
    await wait(350);
  }
}

document.getElementById('start').addEventListener('click', async () => {
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'user', width: { ideal: 640 }, height: { ideal: 480 } }, audio: false
    });
    video.srcObject = stream;
    active = true;
    document.getElementById('start').disabled = true;
    document.getElementById('stop').disabled = false;
    statusLine.textContent = 'Camera connected; sending frames…';
    uploadFrames();
  } catch (error) {
    statusLine.textContent = `Could not start camera: ${error.message}. Allow camera access and retry.`;
  }
});

document.getElementById('stop').addEventListener('click', () => {
  active = false;
  if (stream) stream.getTracks().forEach(track => track.stop());
  video.srcObject = null;
  stream = null;
  document.getElementById('start').disabled = false;
  document.getElementById('stop').disabled = true;
  statusLine.textContent = 'Camera stopped.';
});
</script>
</html>
"""


@app.get("/health")
def health():
    return jsonify(
        ok=True,
        face_recognition=FACE_ENGINE_READY,
        face_engine="opencv_sface" if FACE_ENGINE_READY else None,
        faces=len(known),
    )


@app.get("/camera")
def camera_bridge():
    """Browser camera bridge: preview and upload fresh frames for Wokwi."""
    return CAMERA_BRIDGE


@app.post("/image")
def upload_image():
    f = request.files.get("image")
    if not f:
        return jsonify(error="send multipart field 'image'"), 400
    path = os.path.join(CAPTURE_DIR, f"up_{int(time.time()*1000)}.jpg")
    f.save(path)
    with lock:
        latest_upload.update(path=path, time=time.time())
    return jsonify(ok=True, file=os.path.basename(path))


@app.post("/camera/frame")
def upload_camera_frame():
    """Replace the rolling browser-camera frame without retaining every frame."""
    f = request.files.get("image")
    if not f:
        return jsonify(error="send multipart field 'image'"), 400
    temp_path = LIVE_CAMERA_PATH + f".{threading.get_ident()}.tmp"
    f.save(temp_path)
    with lock:
        os.replace(temp_path, LIVE_CAMERA_PATH)
        latest_upload.update(path=LIVE_CAMERA_PATH, time=time.time())
    return jsonify(ok=True)


@app.post("/event")
def event():
    data = request.get_json(silent=True) or {}
    started = time.perf_counter()
    with event_lock:
        path = grab_image()
        # Reserve an id, then release the shared state lock during inference so
        # camera uploads and the dashboard remain responsive while matching.
        with lock:
            alert_id = (alerts[0]["id"] + 1) if alerts else 1
        if path is None:
            person, authorized, status, image_name = "No camera image", False, "no_image", None
        else:
            if path == LIVE_CAMERA_PATH:
                # Snapshot the rolling frame so a concurrent camera upload
                # cannot change the image while OpenCV is reading it.
                dst = os.path.join(CAPTURE_DIR, f"cap_{alert_id}_laptop_camera.jpg")
                shutil.copyfile(path, dst)
                path = dst
                image_name = os.path.basename(dst)
            elif os.path.dirname(path) != CAPTURE_DIR:
                dst = os.path.join(CAPTURE_DIR, f"cap_{alert_id}_{os.path.basename(path)}")
                cv2.imwrite(dst, cv2.imread(path))
                path = dst
                image_name = os.path.basename(dst)
            else:
                image_name = os.path.basename(path)
            status, name, authorized = identify(path)
            person = name if name else ("No face detected" if status == "no_face" else "Unknown")
        recognition_ms = round((time.perf_counter() - started) * 1000)
        alert = {
            "id": alert_id,
            "time": datetime.now().strftime("%d %b %Y, %I:%M:%S %p"),
            "device": data.get("device", "esp32"),
            "type": data.get("type", "access_attempt"),
            "person": person,
            "status": status,
            "authorized": bool(authorized),
            "image": image_name,
        }
        with lock:
            alerts.insert(0, alert)
            save_log()
    decision = "authorized" if authorized else "unauthorized"
    print(f"[event] #{alert_id} -> {decision} ({person}) in {recognition_ms} ms")
    return jsonify(decision=decision, person=person, alert_id=alert_id, recognition_ms=recognition_ms)


@app.get("/alerts")
def get_alerts():
    return jsonify(alerts[:100])


@app.get("/captures/<path:name>")
def captures(name):
    return send_from_directory(CAPTURE_DIR, name)


@app.post("/voice")
def voice():
    f = request.files.get("audio")
    if not f:
        return jsonify(error="send multipart field 'audio'"), 400
    name = f"voice_{int(time.time()*1000)}.wav"
    path = os.path.join(VOICE_DIR, name)
    f.save(path)
    with lock:
        voice_state["id"] += 1
        voice_state["file"] = name
    print(f"[voice] message #{voice_state['id']} queued for the simulated box speaker")
    return jsonify(ok=True, id=voice_state["id"])


@app.get("/voice/pending")
def voice_pending():
    last = int(request.args.get("last", 0))
    if voice_state["id"] > last:
        return jsonify(pending=True, id=voice_state["id"], url=f"/voice/file/{voice_state['file']}")
    return jsonify(pending=False, id=voice_state["id"])


@app.get("/voice/file/<path:name>")
def voice_file(name):
    return send_from_directory(VOICE_DIR, name)


@app.post("/reload")
def reload_faces():
    load_faces()
    return jsonify(ok=True, faces=len(known))


DASH = """
<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>BiteGuard</title><meta http-equiv=refresh content=3>
<style>body{font-family:sans-serif;background:#111;color:#eee;max-width:700px;margin:auto;padding:12px}
.c{background:#1e1e1e;border-radius:10px;padding:10px;margin:10px 0;display:flex;gap:12px}
.c img{width:120px;height:90px;object-fit:cover;border-radius:6px}.bad{border-left:5px solid #e53935}
.ok{border-left:5px solid #43a047}</style>
<h2>🍪 BiteGuard dashboard</h2>
{% for a in alerts %}
<div class="c {{ 'ok' if a.authorized else 'bad' }}">
{% if a.image %}<img src="/captures/{{ a.image }}">{% endif %}
<div><b>{{ '✅ Authorized' if a.authorized else '🚨 Unauthorized' }}</b><br>
Person: {{ a.person }}<br>{{ a.time }}<br>#{{ a.id }}</div></div>
{% else %}<p>No events yet.</p>{% endfor %}
"""


@app.get("/")
def dashboard():
    return render_template_string(DASH, alerts=alerts[:30])


if __name__ == "__main__":
    load_log()
    load_faces()
    print(f"[info] face engine: {'OpenCV SFace' if FACE_ENGINE_READY else 'unavailable'} | camera mode: {CAMERA_MODE}")
    app.run(host="0.0.0.0", port=5000, debug=False)
