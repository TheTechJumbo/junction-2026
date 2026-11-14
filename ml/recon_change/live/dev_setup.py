#!/usr/bin/env python3
"""Try the carer's setup page on this machine, with no cluster.

    python live/dev_setup.py              # prints the link to open (a free port, a fresh key)
    python live/dev_setup.py --port 8095

The relay is the real one. Instead of the SSH connection it talks to the real gateway code in
this same process, working in a temporary folder with two made-up rooms of drawn "baseline
pictures". So naming places, the real scale, watched spots and enrolled items all write the
same files as on the cluster, and the script prints where. There is no vision worker: a
position fix comes back as a canned one that has the keys in view, which fills the list of
what the room map knows.
"""
import argparse
import base64
import json
import math
import secrets
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gateway  # noqa: E402
import relay  # noqa: E402

# A real 48 x 32 JPEG, for machines without OpenCV to draw one.
TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABQODxIPDRQSEBIXFRQYHjIhHhwcHj0sLiQySUBMS0dARkVQWnNiUFVtVkVGZIhlbXd7gYKBTmCNl4x9lnN+"
    "gXz/2wBDARUXFx4aHjshITt8U0ZTfHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHz/wAARCAAgADADASIAAhEB"
    "AxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS"
    "0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKz"
    "tLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgEC"
    "BAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpj"
    "ZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6"
    "/9oADAMBAAIRAxEAPwBlFFWbT+P8KipPki5FRjzOxWorRorl+t/3Tb2PmZ1FWbv+D8arV1U588VIxlHldgqzafx/hVaiipDni4hGXK7mjRWdRXL9U/vG"
    "3tvIs3f8H41Woorqpw5IqJjKXM7n/9k=")


class LocalGateway:
    """Stands in for the SSH connection: every request goes through JSON, as on the wire, and then
    to the real gateway code. With canned_fix it also answers a position fix, which needs the worker."""

    def __init__(self, canned_fix=False):
        self.canned_fix, self.calls, self.lock = canned_fix, [], threading.Lock()

    def call(self, header, blob=b""):
        with self.lock:
            self.calls.append(header)
            try:
                req = json.loads(json.dumps({**header, "size": len(blob)}))
                if req["op"] == "locate" and self.canned_fix:
                    reply, data = {"ok": True, "job": "dev-fix", "result": canned_fix(req["zone"])}, b""
                else:
                    reply, data = gateway.handle(req, blob)
            except Exception as err:  # as the gateway's own loop does
                reply, data = {"ok": False, "error": f"{type(err).__name__}: {err}"}, b""
            return json.loads(json.dumps(reply)), data


def canned_fix(zone):
    """A position fix as the worker would report it: the wearer in the middle of the room, every named
    place somewhere around them, and the keys in view near the first place."""
    places = []
    for i, place in enumerate(gateway.places_of(zone)):
        angle = math.radians(40 + 100 * i)
        east, north = round(2.2 * math.sin(angle), 2), round(2.2 * math.cos(angle), 2)
        places.append({"name": place["name"], "position": [east, north, 0.0], "distance_m": 2.2,
                       "bearing_deg": int((math.degrees(angle) + 180) % 360 - 180)})
    near = places[0]["position"] if places else [1.0, 1.5, 0.0]
    walls = [[x / 4 - 3, y, 0.0] for x in range(25) for y in (-3.0, 3.0)] + [[x, y / 4 - 3, 0.0] for y in range(25) for x in (-3.0, 3.0)]
    return {"state": "found", "confidence": 0.9, "position": [0.0, 0.0, 0.0], "heading_deg": 0, "waypoints": places,
            "track": [[0, 0]], "map": {"points": walls, "cameras": []}, "match_per_frame": [0.9], "seconds": {"reconstruct": 0.0},
            "items": [{"name": "keys", "score": 0.4, "box": [0.4, 0.5, 0.5, 0.6], "position": [near[0] - 0.3, near[1] - 0.2, 0.0],
                       "distance_m": 2.0, "bearing_deg": 30, "image": ""}],
            "items_looked_for": ["keys", "glasses", "wallet", "phone", "remote control"], "device": "stand-in, no model"}


def draw_room(zone, i, n):
    """One made-up baseline picture as JPEG bytes: a wall, a floor, a door, a shelf with a pill box and a key hook."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return TINY_JPEG
    w, h, shift = 518, 389, int((i - n / 2) * 30)  # the "camera" pans a little from picture to picture
    img = np.full((h, w, 3), (58, 52, 48), np.uint8)
    img[250:] = (70, 84, 96)
    cv2.rectangle(img, (40 - shift, 60), (140 - shift, 250), (60, 90, 130), -1)   # door
    cv2.circle(img, (125 - shift, 160), 5, (200, 200, 210), -1)
    cv2.rectangle(img, (240 - shift, 150), (430 - shift, 162), (150, 160, 170), -1)  # shelf
    cv2.rectangle(img, (270 - shift, 122), (316 - shift, 150), (80, 200, 240), -1)   # pill box
    cv2.rectangle(img, (460 - shift, 90), (474 - shift, 104), (90, 90, 220), -1)     # key hook
    cv2.line(img, (467 - shift, 104), (467 - shift, 130), (190, 190, 190), 2)
    for text, at in (("door", (55 - shift, 50)), ("pill box", (262 - shift, 112)), ("key hook", (430 - shift, 80))):
        cv2.putText(img, text, at, cv2.FONT_HERSHEY_SIMPLEX, 0.45, (225, 225, 225), 1, cv2.LINE_AA)
    cv2.putText(img, f"{zone}: made-up picture {i + 1}", (12, h - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (160, 160, 160), 1, cv2.LINE_AA)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()


def make_room(root, zone, n=6):
    """A room with n baseline pictures, as the worker leaves them: baselines/<zone>/00.jpg, 01.jpg, ..."""
    folder = Path(root) / "baselines" / zone
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (folder / f"{i:02d}.jpg").write_bytes(draw_room(zone, i, n))
    return folder


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0, help="0 picks a free port")
    ap.add_argument("--root", help="folder to keep the rooms in (default: a new temporary folder)")
    ap.add_argument("--token", default=secrets.token_urlsafe(9), help="access key; a random one is made if not given")
    args = ap.parse_args()
    root = Path(args.root) if args.root else Path(tempfile.mkdtemp(prefix="beacon-dev-"))
    gateway.set_root(root)
    for zone in ("kitchen", "lounge"):
        if not any((root / "baselines" / zone).glob("*.jpg")):
            make_room(root, zone)
    evidence = root / "evidence"  # the relay's own notes (position fixes, reminders): not the live relay's folder
    evidence.mkdir(exist_ok=True)
    handler = relay.make_handler(LocalGateway(canned_fix=True), evidence, args.token, None, pick=None)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print("setup page, no cluster. Open:", flush=True)
    print(f"  http://127.0.0.1:{server.server_address[1]}/places?k={args.token}", flush=True)
    print(f"files are written under {root}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
