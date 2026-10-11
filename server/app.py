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
import itertools
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime

import cv2
from flask import Flask, jsonify, request, send_from_directory, render_template_string

try:
    import face_recognition
    HAVE_FR = True
except ImportError:
    HAVE_FR = False

BASE = os.path.dirname(os.path.abspath(__file__))
AUTH_DIR = os.path.join(BASE, "authorized")          # authorized/<name>/*.jpg
OTHERS_DIR = os.path.join(BASE, "known_others")      # known_others/<name>/*.jpg (known, NOT authorized)
CAPTURE_DIR = os.path.join(BASE, "captures")
VOICE_DIR = os.path.join(BASE, "voice")
TEST_DIR = os.path.join(BASE, "test_images")
LOG_FILE = os.path.join(BASE, "alerts.json")
for d in (CAPTURE_DIR, VOICE_DIR):
    os.makedirs(d, exist_ok=True)

CAMERA_MODE = os.environ.get("CAMERA_MODE", "auto")  # auto | webcam | folder
UPLOAD_TTL = 30        # seconds an uploaded image stays "fresh" for the next /event
TOLERANCE = 0.5        # lower = stricter face matching

app = Flask(__name__)
lock = threading.Lock()

# ------------------------------------------------------------------ face DB
known = []   # list of (encoding, name, authorized)


def load_faces():
    global known
    known = []
    if not HAVE_FR:
        print("[warn] face_recognition not installed -> every detected face is 'unknown' (unauthorized).")
        return
    for root, authorized in ((AUTH_DIR, True), (OTHERS_DIR, False)):
        for person_dir in sorted(glob.glob(os.path.join(root, "*"))):
            if not os.path.isdir(person_dir):
                continue
            name = os.path.basename(person_dir).capitalize()
            for img in glob.glob(os.path.join(person_dir, "*")):
                if not img.lower().endswith((".jpg", ".jpeg", ".png")):
                    continue
                encs = face_recognition.face_encodings(face_recognition.load_image_file(img))
                if encs:
                    known.append((encs[0], name, authorized))
                else:
                    print(f"[warn] no face found in {img}")
    print(f"[info] loaded {len(known)} face samples")


def identify(path):
    """Returns (status, name, authorized). status: ok | unknown | no_face"""
    if HAVE_FR:
        image = face_recognition.load_image_file(path)
        locs = face_recognition.face_locations(image)
        if not locs:
            return "no_face", None, False
        encs = face_recognition.face_encodings(image, locs)
        best = None
        for enc in encs:
            for k_enc, name, auth in known:
                dist = face_recognition.face_distance([k_enc], enc)[0]
                if dist < TOLERANCE and (best is None or dist < best[0]):
                    best = (dist, name, auth)
        if best:
            return "ok", best[1], best[2]
        return "unknown", None, False
    # Fallback: OpenCV Haar face detection only (cannot tell people apart)
    gray = cv2.cvtColor(cv2.imread(path), cv2.COLOR_BGR2GRAY)
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = cascade.detectMultiScale(gray, 1.1, 5)
    return ("unknown", None, False) if len(faces) else ("no_face", None, False)

# ------------------------------------------------------------------ camera
latest_upload = {"path": None, "time": 0.0}
_folder_cycle = None


def grab_image():
    """Pick the image for this access attempt."""
    global _folder_cycle
    # 1) fresh upload (real ESP32-CAM or simulate_camera.py)
    if latest_upload["path"] and time.time() - latest_upload["time"] < UPLOAD_TTL:
        return latest_upload["path"]
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


def play_on_laptop(path):
    """Simulates the box speaker by playing the WAV on the laptop."""
    try:
        if sys.platform.startswith("win"):
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME)
        elif sys.platform == "darwin":
            subprocess.run(["afplay", path])
        else:
            try:
                subprocess.run(["aplay", path], check=True)
            except Exception:
                subprocess.run(["ffplay", "-nodisp", "-autoexit", path])
    except Exception as e:
        print("[warn] could not play audio:", e)

# ------------------------------------------------------------------ routes
@app.get("/health")
def health():
    return jsonify(ok=True, face_recognition=HAVE_FR, faces=len(known))


@app.post("/image")
def upload_image():
    f = request.files.get("image")
    if not f:
        return jsonify(error="send multipart field 'image'"), 400
    path = os.path.join(CAPTURE_DIR, f"up_{int(time.time()*1000)}.jpg")
    f.save(path)
    latest_upload.update(path=path, time=time.time())
    return jsonify(ok=True, file=os.path.basename(path))


@app.post("/event")
def event():
    data = request.get_json(silent=True) or {}
    path = grab_image()
    with lock:
        alert_id = (alerts[0]["id"] + 1) if alerts else 1
        if path is None:
            person, authorized, status = "No camera image", False, "no_image"
            image_name = None
        else:
            status, name, authorized = identify(path)
            person = name if name else ("No face detected" if status == "no_face" else "Unknown")
            image_name = os.path.basename(path)
            if os.path.dirname(path) != CAPTURE_DIR:  # copy test image into captures/
                dst = os.path.join(CAPTURE_DIR, f"cap_{alert_id}_{image_name}")
                cv2.imwrite(dst, cv2.imread(path))
                image_name = os.path.basename(dst)
        latest_upload["path"] = None  # consume the upload
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
        alerts.insert(0, alert)
        save_log()
    decision = "authorized" if authorized else "unauthorized"
    print(f"[event] #{alert_id} -> {decision} ({person})")
    return jsonify(decision=decision, person=person, alert_id=alert_id)


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
    threading.Thread(target=play_on_laptop, args=(path,), daemon=True).start()
    print(f"[voice] message #{voice_state['id']} saved, playing on laptop")
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
    print(f"[info] face_recognition available: {HAVE_FR} | camera mode: {CAMERA_MODE}")
    app.run(host="0.0.0.0", port=5000, debug=False)
