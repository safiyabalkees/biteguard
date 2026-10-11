"""
Simulates the ESP32-CAM (and optionally the lid sensor) from your laptop.

  python simulate_camera.py photo.jpg            # upload a photo as 'camera image'
  python simulate_camera.py photo.jpg --trigger  # upload, then fire an access event
                                                 # (use this to demo without Wokwi)
"""
import argparse
import requests

p = argparse.ArgumentParser()
p.add_argument("image")
p.add_argument("--server", default="http://localhost:5000")
p.add_argument("--trigger", action="store_true")
a = p.parse_args()

with open(a.image, "rb") as f:
    print("upload:", requests.post(f"{a.server}/image", files={"image": f}).json())
if a.trigger:
    print("event :", requests.post(f"{a.server}/event",
          json={"device": "simulator", "type": "access_attempt"}).json())
