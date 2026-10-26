"""
Measure BiteGuard's real-time paths against a running server (local or tunnel):

  python bench.py                       # http://localhost:5000
  python bench.py https://xxx.trycloudflare.com

Reports: live-feed fps and end-to-end latency (WebSocket in -> MJPEG out),
how fast an alert / buzzer command reaches a waiting client, and /event latency.
"""
import argparse
import hashlib
import json
import statistics
import threading
import time

import cv2
import numpy as np
import requests
from simple_websocket import Client


def make_frames(n, w=480, h=360):
    rng = np.random.default_rng(1)
    base = cv2.GaussianBlur(rng.integers(0, 255, (h, w, 3), dtype=np.uint8), (0, 0), 6)
    frames = []
    for i in range(n):
        img = base.copy()
        cv2.putText(img, str(i), (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 4, (255, 255, 255), 6)
        frames.append(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 60])[1].tobytes())
    return frames


def read_stream(url, on_frame, stop):
    buf = b""
    with requests.get(url + "/camera/stream", stream=True, timeout=(10, 30)) as r:
        for chunk in r.iter_content(8192):
            if stop.is_set():
                return
            buf += chunk
            while True:
                i = buf.find(b"\r\n\r\n")
                if i < 0:
                    break
                head = buf[:i].lower()
                k = head.find(b"content-length:")
                n = int(head[k + 15:].split(b"\r\n")[0]) if k >= 0 else 0
                if len(buf) < i + 4 + n:
                    break
                if n:
                    on_frame(buf[i + 4:i + 4 + n], time.perf_counter())
                buf = buf[i + 4 + n:]


def live_feed(url, seconds, fps):
    frames = make_frames(int(seconds * fps) + 5)
    index = {hashlib.md5(f).hexdigest(): i for i, f in enumerate(frames)}
    sent, got = {}, {}
    stop = threading.Event()

    def on_frame(data, t):
        i = index.get(hashlib.md5(data).hexdigest())
        if i is not None and i not in got:
            got[i] = t

    threading.Thread(target=read_stream, args=(url, on_frame, stop), daemon=True).start()
    time.sleep(1.0)
    ws = Client.connect(url.replace("http", "ws", 1) + "/camera/ws")
    t0 = time.perf_counter()
    for i in range(int(seconds * fps)):
        sent[i] = time.perf_counter()
        ws.send(frames[i])
        time.sleep(max(0, t0 + (i + 1) / fps - time.perf_counter()))
    time.sleep(1.0)
    stop.set()
    ws.close()
    lat = sorted((got[i] - sent[i]) * 1000 for i in got if i in sent)
    print(f"live feed   : sent {len(sent)} frames @ {fps} fps target, received {len(got)} "
          f"({len(got) / seconds:.1f} fps)")
    if lat:
        print(f"              latency median {statistics.median(lat):.0f} ms, p95 {lat[int(len(lat) * .95) - 1]:.0f} ms")


def wake_latency(url, label, wait_url, trigger):
    out = {}

    def waiter():
        requests.get(wait_url, timeout=30)
        out["t"] = time.perf_counter()

    th = threading.Thread(target=waiter)
    th.start()
    time.sleep(0.5)
    t = time.perf_counter()
    trigger()
    th.join()
    print(f"{label:<12}: waiting client woke {(out['t'] - t) * 1000:.0f} ms after trigger")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("url", nargs="?", default="http://localhost:5000")
    p.add_argument("--seconds", type=int, default=8)
    p.add_argument("--fps", type=int, default=15)
    a = p.parse_args()
    url = a.url.rstrip("/")
    h = requests.get(url + "/health", timeout=10).json()
    print(f"server      : ok, face engine ready={h['face_recognition']}, {h['faces']} samples")
    live_feed(url, a.seconds, a.fps)
    rev = requests.get(url + "/alerts/changes?since=0", timeout=10).json()["rev"]
    wake_latency(url, "alert push", f"{url}/alerts/changes?since={rev}&wait=20",
                 lambda: requests.post(url + "/event", json={"device": "bench"}, timeout=20))
    cur = requests.get(url + "/device/poll?buzz=-1&voice=-1", timeout=10).json()  # how the ESP32 syncs at boot
    wake_latency(url, "buzzer push", f"{url}/device/poll?buzz={cur['b']}&voice={cur['v']}&wait=20",
                 lambda: requests.post(url + "/buzzer", json={"duration_ms": 200}, timeout=10))
    s = requests.Session()
    ts = []
    for _ in range(5):
        t = time.perf_counter()
        s.post(url + "/event", json={"device": "bench"}, timeout=20)
        ts.append((time.perf_counter() - t) * 1000)
    print(f"/event      : median {statistics.median(ts):.0f} ms over 5 requests (keep-alive session)")


if __name__ == "__main__":
    main()
