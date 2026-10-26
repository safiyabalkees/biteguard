"""
BiteGuard backend (Flask) - low-latency edition.

Design: everything that used to be "poll every N ms" is now push/stream:
  camera bridge --WebSocket--> FrameHub (RAM) --MJPEG--> phone / dashboard
  ESP32  <--long-poll-- /device/poll        phone <--long-poll-- /alerts/changes

Endpoints
  GET  /                   live dashboard
  GET  /camera             laptop camera bridge page (uploads frames)
  WS   /camera/ws          binary JPEG frames in
  POST /camera/frame       one JPEG frame in (fallback; raw body or multipart 'image')
  POST /image              one photo in (simulate_camera.py); normalised then published
  GET  /camera/stream      MJPEG out (multipart/x-mixed-replace)
  GET  /camera/live        latest single JPEG
  POST /event              ESP32 access attempt -> decision
  GET  /alerts             alert history (plain list)
  GET  /alerts/changes     ?since=<rev>&wait=<s>  delta + long-poll
  GET  /captures/<f>       snapshots and thumbnails
  POST /buzzer             phone -> ESP32 buzzer
  GET  /device/poll        ESP32 long-poll for buzzer/voice commands
  POST /voice              WAV in -> converted to 8 kHz 8-bit PCM for the ESP32
  GET  /voice/pcm/<id>     raw PCM for the ESP32
  GET/POST/DELETE people   /people, /enroll, /people/<slug>
  POST /reload, GET /health
"""
import glob
import hashlib
import io
import json
import os
import re
import shutil
import threading
import time
import wave
from datetime import datetime

import cv2
import numpy as np
import requests
from flask import Flask, Response, abort, jsonify, render_template_string, request, send_from_directory
from flask_sock import Sock

BASE = os.path.dirname(os.path.abspath(__file__))
AUTH_DIR = os.path.join(BASE, "authorized")
OTHERS_DIR = os.path.join(BASE, "known_others")
CAPTURE_DIR = os.path.join(BASE, "captures")
VOICE_DIR = os.path.join(BASE, "voice")
TEST_DIR = os.path.join(BASE, "test_images")
MODEL_DIR = os.path.join(BASE, "models")
LOG_FILE = os.path.join(BASE, "alerts.json")
EMBED_CACHE = os.path.join(BASE, "models", "embeddings_cache.npz")
for d in (CAPTURE_DIR, VOICE_DIR, MODEL_DIR):
    os.makedirs(d, exist_ok=True)

CAMERA_MODE = os.environ.get("CAMERA_MODE", "upload")  # upload | auto | folder | webcam
UPLOAD_TTL = float(os.environ.get("CAMERA_UPLOAD_TTL", "5"))
FACE_MATCH_THRESHOLD = float(os.environ.get("FACE_MATCH_THRESHOLD", "0.363"))
FACE_DETECT_THRESHOLD = float(os.environ.get("FACE_DETECT_THRESHOLD", "0.5"))
MAX_DETECT_SIZE = int(os.environ.get("MAX_DETECT_SIZE", "640"))
MAX_ALERTS = int(os.environ.get("MAX_ALERTS", "200"))
VOICE_RATE = 8000
VOICE_MAX_SECONDS = 8
LIVE_MAX_WIDTH = 640

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
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024
sock = Sock(app)


# ====================================================================== frames
class FrameHub:
    """Latest camera frame, in RAM. Readers block until a newer frame exists,
    so a slow reader just skips frames instead of building a backlog."""

    def __init__(self):
        self._cond = threading.Condition()
        self._frame = None
        self._seq = 0
        self._time = 0.0
        self._count = 0
        self._count_t = time.time()
        self.fps = 0.0

    def publish(self, jpeg):
        now = time.time()
        with self._cond:
            self._frame, self._time = jpeg, now
            self._seq += 1
            self._count += 1
            if now - self._count_t >= 2:
                self.fps = self._count / (now - self._count_t)
                self._count, self._count_t = 0, now
            self._cond.notify_all()

    def latest(self):
        with self._cond:
            return self._frame, self._time

    def fresh(self, ttl):
        frame, t = self.latest()
        return frame if frame is not None and time.time() - t < ttl else None

    def wait_next(self, last_seq, timeout):
        with self._cond:
            if self._seq == last_seq:
                self._cond.wait(timeout)
            if self._seq == last_seq:
                return None, last_seq
            return self._frame, self._seq

    def age(self):
        return None if not self._time else round(time.time() - self._time, 2)


hub = FrameHub()


def normalise_jpeg(data, max_width=LIVE_MAX_WIDTH):
    """Decode arbitrary image bytes and return a modest JPEG (used for /image uploads)."""
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    if w > max_width:
        img = cv2.resize(img, (max_width, int(h * max_width / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return buf.tobytes() if ok else None


def looks_like_jpeg(b):
    return len(b) > 4 and b[0] == 0xFF and b[1] == 0xD8


# ================================================================= face engine
known_matrix = np.zeros((0, 128), np.float32)
known_meta = []  # (name, authorized) aligned with known_matrix rows
face_detector = None
face_recognizer = None
FACE_ENGINE_READY = False
face_model_lock = threading.Lock()
load_lock = threading.Lock()


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_model(key):
    filename, url, expected = FACE_MODELS[key]
    path = os.path.join(MODEL_DIR, filename)
    marker = path + ".ok"
    # Skip re-hashing 40 MB on every start: trust the marker if size+mtime match.
    if os.path.isfile(path) and os.path.isfile(marker):
        st = os.stat(path)
        if open(marker).read().strip() == f"{st.st_size}:{int(st.st_mtime)}":
            return path
    if os.path.isfile(path) and _sha256(path) == expected:
        st = os.stat(path)
        open(marker, "w").write(f"{st.st_size}:{int(st.st_mtime)}")
        return path
    tmp = path + ".download"
    print(f"[info] downloading face model {filename}")
    digest = hashlib.sha256()
    try:
        with requests.get(url, stream=True, timeout=(10, 120)) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
                    digest.update(chunk)
        if digest.hexdigest() != expected:
            raise RuntimeError(f"SHA-256 verification failed for {filename}")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    st = os.stat(path)
    open(marker, "w").write(f"{st.st_size}:{int(st.st_mtime)}")
    return path


def _load_models():
    global face_detector, face_recognizer
    if face_detector is not None:
        return
    sface = _ensure_model("sface")
    key = "yunet_2026" if int(cv2.__version__.split(".", 1)[0]) >= 5 else "yunet_2023"
    yunet = _ensure_model(key)
    face_detector = cv2.FaceDetectorYN.create(yunet, "", (320, 320), FACE_DETECT_THRESHOLD, 0.3, 5000)
    face_recognizer = cv2.FaceRecognizerSF.create(sface, "")
    # Warm-up: the first ONNX inference is slow; pay for it now, not on the first lid press.
    blank = np.zeros((240, 320, 3), np.uint8)
    _face_embeddings(blank)


def _face_embeddings(image):
    """Detect + align faces -> list of unit-length SFace embeddings."""
    if image is None or image.size == 0 or face_detector is None or face_recognizer is None:
        return []
    h, w = image.shape[:2]
    scale = min(1.0, MAX_DETECT_SIZE / max(h, w))
    det_img = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else image
    with face_model_lock:
        face_detector.setInputSize((det_img.shape[1], det_img.shape[0]))
        _, faces = face_detector.detect(det_img)
        if faces is None:
            return []
        out = []
        for face in faces:
            f = face.copy()
            f[:14] /= scale
            emb = face_recognizer.feature(face_recognizer.alignCrop(image, f)).reshape(-1).astype(np.float32)
            n = np.linalg.norm(emb)
            if n > 0:
                out.append(emb / n)
        return out


def _load_cache():
    try:
        z = np.load(EMBED_CACHE, allow_pickle=False)
        return {k: z[k] for k in z.files}
    except Exception:
        return {}


def load_faces():
    """(Re)build the enrolled-face matrix. Embeddings are cached per photo, so a
    restart only recomputes photos that are new or changed."""
    global known_matrix, known_meta, FACE_ENGINE_READY
    with load_lock:
        try:
            _load_models()
        except Exception as e:
            FACE_ENGINE_READY = False
            print(f"[error] face engine unavailable; checks fail closed: {e}")
            return
        t0 = time.perf_counter()
        old = _load_cache()
        new_cache, rows, meta = {}, [], []
        for root, authorized in ((AUTH_DIR, True), (OTHERS_DIR, False)):
            for person_dir in sorted(glob.glob(os.path.join(root, "*"))):
                if not os.path.isdir(person_dir):
                    continue
                name = _person_name(person_dir)
                for img in sorted(glob.glob(os.path.join(person_dir, "*"))):
                    if not img.lower().endswith((".jpg", ".jpeg", ".png")):
                        continue
                    st = os.stat(img)
                    key = hashlib.md5(f"{img}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()
                    emb = old.get(key)
                    if emb is None:
                        encs = _face_embeddings(cv2.imread(img))
                        if len(encs) != 1:
                            print(f"[warn] {len(encs)} faces in {img}; skipped")
                            continue
                        emb = encs[0]
                    new_cache[key] = emb
                    rows.append(emb)
                    meta.append((name, authorized))
        if new_cache.keys() != old.keys():
            try:
                np.savez(EMBED_CACHE, **new_cache)
            except Exception as e:
                print(f"[warn] could not write embedding cache: {e}")
        known_matrix = np.stack(rows).astype(np.float32) if rows else np.zeros((0, 128), np.float32)
        known_meta = meta
        FACE_ENGINE_READY = True
        print(f"[info] face engine ready: {len(meta)} samples in {(time.perf_counter() - t0) * 1000:.0f} ms")


def _person_name(person_dir):
    try:
        with open(os.path.join(person_dir, "person.json"), encoding="utf-8") as f:
            return json.load(f).get("name") or os.path.basename(person_dir).capitalize()
    except Exception:
        return os.path.basename(person_dir).capitalize()


def identify_image(image):
    """-> (status, name, authorized). status: ok | unknown | no_face | engine_not_ready"""
    if not FACE_ENGINE_READY:
        return "engine_not_ready", None, False
    encs = _face_embeddings(image)
    if not encs:
        return "no_face", None, False
    matrix, meta = known_matrix, known_meta
    if not len(meta):
        return "unknown", None, False
    best = None
    for enc in encs:
        sims = matrix @ enc
        i = int(np.argmax(sims))
        if sims[i] >= FACE_MATCH_THRESHOLD and (best is None or sims[i] > best[0]):
            best = (float(sims[i]), meta[i])
    if best:
        return "ok", best[1][0], best[1][1]
    return "unknown", None, False


# ===================================================================== alerts
class AlertStore:
    """Bounded, thread-safe alert list with a revision counter for long-polling.
    Disk writes happen on a background thread, debounced, never on the request path."""

    def __init__(self, path, cap):
        self.path, self.cap = path, cap
        self._cond = threading.Condition()
        self._items = []  # newest first
        self._rev = 0
        self._next_id = 1
        self._dirty = threading.Event()
        self._load()
        threading.Thread(target=self._writer, daemon=True).start()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                items = json.load(f)
        except Exception:
            return
        items = [a for a in items if isinstance(a, dict) and "id" in a][: self.cap]
        for rev, a in enumerate(reversed(items), 1):  # oldest gets the lowest rev
            a["rev"] = rev
            if a.get("status") == "processing":
                a["status"] = "unknown"
                a["person"] = "Unknown"
        self._items = items
        self._rev = len(items)
        self._next_id = max((a["id"] for a in items), default=0) + 1

    def _writer(self):
        while True:
            self._dirty.wait()
            time.sleep(0.3)
            self._dirty.clear()
            with self._cond:
                data = json.dumps(self._items, separators=(",", ":"))
            try:
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(data)
                os.replace(tmp, self.path)
            except Exception as e:
                print(f"[warn] could not save alerts: {e}")

    def add(self, **fields):
        with self._cond:
            alert = {"id": self._next_id, **fields}
            self._next_id += 1
            self._rev += 1
            alert["rev"] = self._rev
            self._items.insert(0, alert)
            del self._items[self.cap:]
            self._cond.notify_all()
        self._dirty.set()
        return dict(alert)

    def update(self, alert_id, **fields):
        with self._cond:
            for a in self._items:
                if a["id"] == alert_id:
                    a.update(fields)
                    self._rev += 1
                    a["rev"] = self._rev
                    self._cond.notify_all()
                    self._dirty.set()
                    return dict(a)
        return None

    def changes(self, since, wait, limit=50):
        with self._cond:
            if wait > 0 and self._rev <= since:
                self._cond.wait_for(lambda: self._rev > since, timeout=wait)
            items = [dict(a) for a in self._items if a["rev"] > since][:limit]
            return self._rev, items

    def snapshot(self, limit=100):
        with self._cond:
            return [dict(a) for a in self._items[:limit]]


store = AlertStore(LOG_FILE, MAX_ALERTS)


# ================================================================== commands
class Commands:
    """Phone -> ESP32 commands, delivered by long-poll so they arrive instantly."""

    def __init__(self):
        self.cond = threading.Condition()
        self.buzz_id, self.buzz_ms = 0, 350
        self.voice_id, self.voice_bytes = 0, 0

    def poll(self, last_buzz, last_voice, wait):
        with self.cond:
            def ready():
                return self.buzz_id > last_buzz or self.voice_id > last_voice
            # last_* < 0 means "just tell me the current ids" (device boot).
            if last_buzz >= 0 and last_voice >= 0 and wait > 0 and not ready():
                self.cond.wait_for(ready, timeout=wait)
            out = {"b": self.buzz_id, "v": self.voice_id}
            if last_buzz >= 0 and self.buzz_id > last_buzz:
                out["buzz"] = {"id": self.buzz_id, "ms": self.buzz_ms}
            if last_voice >= 0 and self.voice_id > last_voice:
                out["voice"] = {"id": self.voice_id, "bytes": self.voice_bytes,
                                "url": f"/voice/pcm/{self.voice_id}"}
            return out


commands = Commands()


# ============================================================== camera sources
_folder_files = None
_folder_i = 0


def grab_frame():
    """JPEG bytes for this access attempt, or None."""
    frame = hub.fresh(UPLOAD_TTL)
    if frame is not None or CAMERA_MODE in ("upload", "webcam"):
        return frame
    global _folder_files, _folder_i
    if CAMERA_MODE in ("auto", "folder"):
        if _folder_files is None:
            _folder_files = sorted(f for f in glob.glob(os.path.join(TEST_DIR, "*"))
                                   if f.lower().endswith((".jpg", ".jpeg", ".png")))
        if _folder_files:
            path = _folder_files[_folder_i % len(_folder_files)]
            _folder_i += 1
            with open(path, "rb") as f:
                return normalise_jpeg(f.read())
    return None


def _webcam_thread():
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    print("[info] webcam source started" if cap.isOpened() else "[error] could not open webcam 0")
    while cap.isOpened():
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.1)
            continue
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            hub.publish(buf.tobytes())
        time.sleep(0.03)


# ==================================================================== routes
CAMERA_BRIDGE = r"""<!doctype html>
<html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BiteGuard camera</title>
<style>
:root{color-scheme:dark}
body{font:16px system-ui,sans-serif;background:#111;color:#eee;max-width:720px;margin:24px auto;padding:0 16px}
video{display:block;width:100%;max-height:60vh;background:#222;object-fit:contain;transform:scaleX(-1);border-radius:12px}
button{padding:10px 18px;margin:12px 8px 12px 0;border:0;border-radius:8px;background:#ff7043;color:#111;font-weight:600;cursor:pointer}
button:disabled{opacity:.4;cursor:default}
#stats{color:#9ee493;font-variant-numeric:tabular-nums}
.dim{color:#999;font-size:14px}
</style>
<h2>BiteGuard camera bridge</h2>
<video id="v" autoplay playsinline muted></video>
<button id="go">Start camera</button><button id="stop" disabled>Stop</button>
<p id="stats">Camera stopped.</p>
<p class="dim">Keep this tab open and visible. The phone shows this video live; the Wokwi button runs face recognition on the newest frame.</p>
<canvas id="c" hidden></canvas>
<script>
const $=id=>document.getElementById(id), v=$('v'), c=$('c'), st=$('stats');
const WIDTH=480, FPS=15, QUALITY=0.6;
let stream=null, on=false, ws=null, wsOk=false, sent=0, t0=0, bytes=0, inflight=0;

function openWS(){
  const proto=location.protocol==='https:'?'wss':'ws';
  ws=new WebSocket(`${proto}://${location.host}/camera/ws`);
  ws.binaryType='arraybuffer';
  ws.onopen=()=>{wsOk=true};
  ws.onclose=()=>{wsOk=false; if(on) setTimeout(openWS,1000)};
  ws.onerror=()=>{wsOk=false};
}
function grab(){
  return new Promise(res=>{
    const s=Math.min(1,WIDTH/v.videoWidth);
    c.width=Math.round(v.videoWidth*s); c.height=Math.round(v.videoHeight*s);
    c.getContext('2d').drawImage(v,0,0,c.width,c.height);
    c.toBlob(res,'image/jpeg',QUALITY);
  });
}
async function loop(){
  const gap=1000/FPS;
  while(on){
    const started=performance.now();
    if(v.videoWidth){
      if(wsOk && ws.bufferedAmount<150000){           // drop frames instead of queueing
        const b=await grab(); if(b&&wsOk){ws.send(await b.arrayBuffer()); sent++; bytes+=b.size;}
      } else if(!wsOk && inflight<2){                   // fallback: pipelined POSTs
        const b=await grab(); inflight++;
        fetch('/camera/frame',{method:'POST',body:b,headers:{'Content-Type':'image/jpeg'}})
          .finally(()=>inflight--); sent++; bytes+=b.size;
      }
    }
    const now=performance.now();
    if(now-t0>1000){
      st.textContent=`Live · ${(sent*1000/(now-t0)).toFixed(1)} fps · ${(bytes*8/(now-t0)/1000).toFixed(0)} kbit/s · ${wsOk?'WebSocket':'HTTP fallback'}`;
      sent=0; bytes=0; t0=now;
    }
    await new Promise(r=>setTimeout(r,Math.max(0,gap-(performance.now()-started))));
  }
}
$('go').onclick=async()=>{
  try{
    stream=await navigator.mediaDevices.getUserMedia({video:{width:{ideal:640},height:{ideal:480},frameRate:{ideal:30}},audio:false});
  }catch(e){st.textContent='Could not start camera: '+e.message; return;}
  v.srcObject=stream; on=true; t0=performance.now(); openWS(); loop();
  $('go').disabled=true; $('stop').disabled=false;
};
$('stop').onclick=()=>{
  on=false; if(ws) ws.close(); if(stream) stream.getTracks().forEach(t=>t.stop());
  v.srcObject=null; $('go').disabled=false; $('stop').disabled=true; st.textContent='Camera stopped.';
};
</script></html>"""

DASH = r"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>BiteGuard</title>
<style>
:root{color-scheme:dark}
body{font-family:system-ui,sans-serif;background:#111;color:#eee;max-width:720px;margin:auto;padding:12px}
img.live{width:100%;border-radius:12px;background:#222;aspect-ratio:4/3;object-fit:cover}
.c{background:#1e1e1e;border-radius:10px;padding:10px;margin:8px 0;display:flex;gap:12px;border-left:5px solid #e53935}
.c.ok{border-left-color:#43a047}.c img{width:96px;height:72px;object-fit:cover;border-radius:6px}
small{color:#999}
</style>
<h2>BiteGuard</h2>
<img class="live" src="/camera/stream" alt="live camera">
<div id="list"></div>
<script>
let rev=0, items=[];
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function draw(){
  document.getElementById('list').innerHTML=items.slice(0,30).map(a=>`<div class="c ${a.authorized?'ok':''}">
  ${a.thumb?`<img src="/captures/${esc(a.thumb)}">`:''}
  <div><b>${a.status==='processing'?'Checking…':a.authorized?'Authorized':'Blocked'}</b> · ${esc(a.person)}<br>
  <small>${esc(a.time)} · #${a.id}</small></div></div>`).join('')||'<p>No events yet.</p>';
}
async function poll(){
  try{
    const r=await (await fetch(`/alerts/changes?since=${rev}&wait=20`)).json();
    rev=r.rev;
    for(const a of r.alerts){const i=items.findIndex(x=>x.id===a.id); if(i>=0) items[i]=a; else items.push(a);}
    items.sort((a,b)=>b.id-a.id); draw();
  }catch(e){await new Promise(r=>setTimeout(r,2000));}
  poll();
}
poll();
</script></html>"""


def no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/health")
def health():
    return no_store(jsonify(
        ok=True, face_recognition=FACE_ENGINE_READY, faces=len(known_meta),
        camera_mode=CAMERA_MODE, camera_age=hub.age(), camera_fps=round(hub.fps, 1),
        now=time.time()))


@app.get("/")
def dashboard():
    return DASH


@app.get("/camera")
def camera_bridge():
    return CAMERA_BRIDGE


@sock.route("/camera/ws")
def camera_ws(ws):
    while True:
        data = ws.receive()
        if data is None:
            break
        if isinstance(data, (bytes, bytearray)) and looks_like_jpeg(data):
            hub.publish(bytes(data))


@app.post("/camera/frame")
def camera_frame():
    data = request.get_data() if request.mimetype == "image/jpeg" else None
    if data is None:
        f = request.files.get("image")
        data = f.read() if f else b""
    if not looks_like_jpeg(data):
        return jsonify(error="send a JPEG (raw body or multipart field 'image')"), 400
    hub.publish(data)
    return ("", 204)


@app.post("/image")
def upload_image():
    f = request.files.get("image")
    jpeg = normalise_jpeg(f.read()) if f else None
    if jpeg is None:
        return jsonify(error="send an image in multipart field 'image'"), 400
    hub.publish(jpeg)
    return jsonify(ok=True)


@app.get("/camera/live")
def camera_live():
    frame, _ = hub.latest()
    if frame is None:
        return no_store(jsonify(error="camera frame not available")), 404
    return no_store(Response(frame, mimetype="image/jpeg"))


@app.get("/camera/stream")
def camera_stream():
    def gen():
        seq = 0
        while True:
            frame, seq = hub.wait_next(seq, 10)
            if frame is None:  # idle heartbeat keeps proxies open and detects dead clients
                yield b"--frame\r\nContent-Length: 0\r\n\r\n\r\n"
                continue
            yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                   + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
    resp = Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


def _save_snapshot(image, alert_id):
    """Write the full snapshot and a small thumbnail (for lists and notifications)."""
    name, thumb = f"attempt_{alert_id}.jpg", f"attempt_{alert_id}_t.jpg"
    cv2.imwrite(os.path.join(CAPTURE_DIR, name), image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    h, w = image.shape[:2]
    s = 240 / max(h, w)
    small = cv2.resize(image, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else image
    cv2.imwrite(os.path.join(CAPTURE_DIR, thumb), small, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return name, thumb


event_lock = threading.Lock()


@app.post("/event")
def event():
    started = time.perf_counter()
    data = request.get_json(silent=True) or {}
    device, etype = data.get("device", "esp32"), data.get("type", "access_attempt")
    with event_lock:
        jpeg = grab_frame()
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR) if jpeg else None
        base = dict(time=datetime.now().strftime("%d %b %Y, %I:%M:%S %p"), created_at=time.time(),
                    device=device, type=etype, authorized=False, image=None, thumb=None)
        alert = store.add(person="Identifying…", status="processing", **base)  # phone is notified now
        alert_id = alert["id"]
        if image is None:
            status, name, authorized, person = "no_image", None, False, "No camera image"
        else:
            name_img, thumb = _save_snapshot(image, alert_id)
            store.update(alert_id, image=name_img, thumb=thumb)
            status, name, authorized = identify_image(image)
            person = name or {"no_face": "No face detected", "engine_not_ready": "Starting up…"}.get(status, "Unknown")
        store.update(alert_id, person=person, status=status, authorized=bool(authorized))
    ms = round((time.perf_counter() - started) * 1000)
    decision = "authorized" if authorized else "unauthorized"
    print(f"[event] #{alert_id} -> {decision} ({person}) in {ms} ms")
    return jsonify(decision=decision, person=person, alert_id=alert_id, recognition_ms=ms)


@app.get("/alerts")
def get_alerts():
    return no_store(jsonify(store.snapshot(100)))


@app.get("/alerts/changes")
def alerts_changes():
    since = request.args.get("since", 0, type=int)
    wait = min(max(request.args.get("wait", 0, type=float), 0), 25)
    rev, items = store.changes(since, wait)
    return no_store(jsonify(rev=rev, alerts=items, now=time.time()))


@app.get("/captures/<path:name>")
def captures(name):
    resp = send_from_directory(CAPTURE_DIR, name, max_age=86400)  # snapshots never change
    return resp


@app.post("/buzzer")
def trigger_buzzer():
    data = request.get_json(silent=True) or {}
    try:
        ms = max(100, min(1500, int(data.get("duration_ms", 350))))
    except (TypeError, ValueError):
        ms = 350
    with commands.cond:
        commands.buzz_id += 1
        commands.buzz_ms = ms
        bid = commands.buzz_id
        commands.cond.notify_all()
    return jsonify(ok=True, id=bid)


@app.get("/device/poll")
def device_poll():
    lb = request.args.get("buzz", -1, type=int)
    lv = request.args.get("voice", -1, type=int)
    wait = min(max(request.args.get("wait", 0, type=float), 0), 25)
    return no_store(jsonify(commands.poll(lb, lv, wait)))


def wav_to_pcm_u8(raw):
    """Any PCM WAV -> mono, 8 kHz, unsigned 8-bit, peak-normalised, <= VOICE_MAX_SECONDS."""
    with wave.open(io.BytesIO(raw)) as w:
        ch, sw, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        data = w.readframes(n)
    if sw == 1:
        x = (np.frombuffer(data, np.uint8).astype(np.float32) - 128) * 256
    elif sw == 2:
        x = np.frombuffer(data, "<i2").astype(np.float32)
    elif sw == 4:
        x = np.frombuffer(data, "<i4").astype(np.float32) / 65536
    else:
        raise ValueError("unsupported sample width")
    x = x[: len(x) // ch * ch].reshape(-1, ch).mean(axis=1)
    if rate > VOICE_RATE:  # box filter before decimating to avoid harsh aliasing
        k = max(1, int(round(rate / VOICE_RATE)))
        x = np.convolve(x, np.ones(k, np.float32) / k, mode="same")
    out_n = int(len(x) * VOICE_RATE / rate)
    x = np.interp(np.linspace(0, len(x) - 1, out_n), np.arange(len(x)), x)[: VOICE_RATE * VOICE_MAX_SECONDS]
    peak = float(np.max(np.abs(x))) if len(x) else 0
    if peak > 0:
        x = x * (30000 / peak)
    return np.clip(x / 256 + 128, 0, 255).astype(np.uint8).tobytes()


@app.post("/voice")
def voice():
    f = request.files.get("audio")
    if not f:
        return jsonify(error="send multipart field 'audio' (WAV)"), 400
    try:
        pcm = wav_to_pcm_u8(f.read())
    except Exception as e:
        return jsonify(error=f"could not read WAV: {e}"), 400
    if len(pcm) < 400:
        return jsonify(error="audio too short"), 400
    with commands.cond:
        commands.voice_id += 1
        vid = commands.voice_id
        commands.voice_bytes = len(pcm)
        with open(os.path.join(VOICE_DIR, f"voice_{vid}.pcm"), "wb") as out:
            out.write(pcm)
        commands.cond.notify_all()
    print(f"[voice] #{vid}: {len(pcm)} bytes ({len(pcm) / VOICE_RATE:.1f} s) queued for the box")
    return jsonify(ok=True, id=vid, seconds=round(len(pcm) / VOICE_RATE, 1))


@app.get("/voice/pcm/<int:vid>")
def voice_pcm(vid):
    path = os.path.join(VOICE_DIR, f"voice_{vid}.pcm")
    if not os.path.isfile(path):
        abort(404)
    return send_from_directory(VOICE_DIR, f"voice_{vid}.pcm", mimetype="application/octet-stream")


# ------------------------------------------------------------------- people
def _slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "person"


@app.get("/people")
def people():
    out = []
    for root, authorized in ((AUTH_DIR, True), (OTHERS_DIR, False)):
        for d in sorted(glob.glob(os.path.join(root, "*"))):
            if os.path.isdir(d):
                n = sum(1 for f in os.listdir(d) if f.lower().endswith((".jpg", ".jpeg", ".png")))
                out.append(dict(slug=os.path.basename(d), name=_person_name(d), photos=n, authorized=authorized))
    return no_store(jsonify(out))


@app.delete("/people/<slug>")
def delete_person(slug):
    if not re.fullmatch(r"[a-z0-9_]+", slug):
        return jsonify(error="bad name"), 400
    d = os.path.join(AUTH_DIR, slug)
    if not os.path.isdir(d):
        return jsonify(error="not found"), 404
    shutil.rmtree(d)
    threading.Thread(target=load_faces, daemon=True).start()
    return jsonify(ok=True)


@app.post("/enroll")
def enroll():
    raw_name = (request.form.get("name") or "").strip()
    photos = request.files.getlist("photos")
    if not raw_name or len(raw_name) > 60 or not re.fullmatch(r"[\w .'-]+", raw_name, flags=re.UNICODE):
        return jsonify(error="Enter a valid name (letters, numbers, spaces, apostrophes, dots, hyphens)."), 400
    if not 1 <= len(photos) <= 5:
        return jsonify(error="Upload between 1 and 5 photos."), 400
    if not FACE_ENGINE_READY:
        return jsonify(error="Face recognition is still starting up. Try again in a few seconds."), 503
    images = []
    for p in photos:
        img = cv2.imdecode(np.frombuffer(p.read(), np.uint8), cv2.IMREAD_COLOR)
        if len(_face_embeddings(img)) != 1:
            return jsonify(error=f"Photo {len(images) + 1} must contain exactly one clear face."), 400
        images.append(img)
    person_dir = os.path.join(AUTH_DIR, _slug(raw_name))
    os.makedirs(person_dir, exist_ok=True)
    with open(os.path.join(person_dir, "person.json"), "w", encoding="utf-8") as f:
        json.dump({"name": raw_name}, f)
    start = sum(1 for f in os.listdir(person_dir) if f.lower().endswith((".jpg", ".jpeg", ".png")))
    for i, img in enumerate(images, 1):
        cv2.imwrite(os.path.join(person_dir, f"enrolled_{start + i}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    load_faces()  # cache makes this instant for the existing photos
    return jsonify(ok=True, name=raw_name, photos=len(images), faces=len(known_meta))


@app.post("/reload")
def reload_faces():
    load_faces()
    return jsonify(ok=True, faces=len(known_meta))


def main():
    from werkzeug.serving import WSGIRequestHandler
    WSGIRequestHandler.protocol_version = "HTTP/1.1"  # keep-alive for the ESP32 and phone
    threading.Thread(target=load_faces, daemon=True).start()  # server is up immediately
    if CAMERA_MODE == "webcam":
        threading.Thread(target=_webcam_thread, daemon=True).start()
    print(f"[info] BiteGuard on http://0.0.0.0:5000 | camera mode: {CAMERA_MODE} | models loading in background")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)


if __name__ == "__main__":
    main()
