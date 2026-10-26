"""
Simulates the laptop camera / ESP32-CAM without any hardware.

  python simulate_camera.py photo.jpg --trigger   # send one photo, then fire an access event
  python simulate_camera.py photo.jpg --stream    # act as a live camera (~12 fps) until Ctrl+C
                                                  #   -> watch it on the phone or http://localhost:5000
"""
import argparse
import time

import cv2
import numpy as np
import requests

p = argparse.ArgumentParser()
p.add_argument("image")
p.add_argument("--server", default="http://localhost:5000")
p.add_argument("--trigger", action="store_true", help="fire an access event after sending the photo")
p.add_argument("--stream", action="store_true", help="keep sending frames as a live camera")
a = p.parse_args()

img = cv2.imread(a.image)
if img is None:
    raise SystemExit(f"could not read {a.image}")
scale = min(1.0, 480 / img.shape[1])
img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)


def jpeg(frame):
    return cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()


def post(frame):
    requests.post(f"{a.server}/camera/frame", data=jpeg(frame),
                  headers={"Content-Type": "image/jpeg"}, timeout=5).raise_for_status()


post(img)
print("sent photo")
if a.trigger:
    print("event :", requests.post(f"{a.server}/event", json={"device": "simulator"}, timeout=20).json())
if a.stream:
    print("streaming; Ctrl+C to stop")
    n = 0
    try:
        with requests.Session() as s:
            while True:
                t = time.time()
                frame = img.copy()
                cv2.putText(frame, f"#{n}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
                s.post(f"{a.server}/camera/frame", data=jpeg(frame),
                       headers={"Content-Type": "image/jpeg"}, timeout=5)
                n += 1
                time.sleep(max(0, 1 / 12 - (time.time() - t)))
    except KeyboardInterrupt:
        pass
