#!/usr/bin/env python3
"""Offline test: a room map built again keeps the zone's trained splat (with its views and labels), and a zone with a
trained splat does not grow from position-fix photos unless one asks (?grow=1). Needs OpenCV; no cluster.

    python live/test_room_keeps_training.py
"""
import io
import json
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import relay  # noqa: E402
from test_view_gate import FIX, jpeg, room  # noqa: E402

KEY = "test-key"
TRAINED = {"splat": "/evidence/train-kitchen-s1-1/trained.splat", "session": "s1", "psnr": 24.6,
           "views": "/evidence/train-kitchen-s1-1/views.json", "labels": "/evidence/train-kitchen-s1-1/labels.json"}


def splat_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("room.splat", bytes(32))
    return buffer.getvalue()


class Gateway:
    """Stands in for the cluster: every fix sees a part of the room not sampled before (picture_added), and records
    whether the relay let it grow; room maps are counted."""

    def __init__(self):
        self.maps, self.grow = 0, []

    def call(self, header, blob=b""):
        if header["op"] == "locate":
            self.grow.append(header.get("grow"))
            result = json.loads(json.dumps(FIX))
            if header.get("grow", True):
                result["picture_added"] = {"name": "09.jpg", "frame": 9, "pictures": 10}
            else:
                result["picture_not_added"] = "growth is off for this zone"
            return {"ok": True, "job": f"fix{len(self.grow)}", "result": result}, b""
        if header["op"] == "map":
            self.maps += 1
            return {"ok": True, "job": f"map{self.maps}", "result": {"frames": 9 + self.maps, "cameras": [], "places": []}}, splat_zip()
        return {"ok": True, "zones": ["kitchen", "hall"], "workers": {}}, b""


def main():
    gateway = Gateway()
    evidence = Path(tempfile.mkdtemp(prefix="beacon-room-test-"))
    (evidence / "map0").mkdir()
    (evidence / "map0" / "room.splat").write_bytes(bytes(32))
    (evidence / "rooms.json").write_text(json.dumps({"kitchen": {
        "zone": "kitchen", "job": "map0", "splat": "/evidence/map0/room.splat", "session": "s1", "at": time.time(),
        "frames": 9, "trained": TRAINED}}))
    handler = relay.make_handler(gateway, evidence, KEY, None, pick=None)
    handler.log_message = lambda *args: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path, raw=None):
        request = urllib.request.Request(url + path, data=raw, headers={"X-Key": KEY}, method="POST" if raw is not None else "GET")
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read())

    def maps_reach(n):
        for _ in range(100):
            if gateway.maps >= n and not call("/room?zone=kitchen").get("building"):
                return True
            time.sleep(0.05)
        return False

    # 1. POST /map builds the room map again from its pictures (a result with no session): the trained splat stays.
    built = call("/map?zone=kitchen", b"")
    assert gateway.maps == 1 and built["trained"] == TRAINED, built.get("trained")
    assert call("/room?zone=kitchen")["trained"] == TRAINED

    # 2. A position fix in the trained zone: the worker is told not to grow it, and no map is built.
    call("/locate?zone=kitchen", jpeg(room(1)))
    time.sleep(0.3)
    assert gateway.grow == [False] and gateway.maps == 1, (gateway.grow, gateway.maps)

    # 3. Asked to grow (?grow=1): the picture is added, the map is built again in the background, the splat stays.
    call("/locate?zone=kitchen&grow=1", jpeg(room(2)))
    assert gateway.grow[-1] is True and maps_reach(2), (gateway.grow, gateway.maps)
    got = call("/room?zone=kitchen")
    assert got["job"] == "map2" and got["trained"] == TRAINED, got

    # 4. A zone without a trained splat grows as before.
    call("/locate?zone=hall", jpeg(room(3)))
    assert gateway.grow[-1] is True, gateway.grow
    saved = json.loads((evidence / "rooms.json").read_text())
    assert saved["kitchen"]["trained"] == TRAINED, saved["kitchen"].get("trained")
    server.shutdown()
    print("ok: a rebuilt room map keeps the trained splat; a trained zone grows only with ?grow=1")


if __name__ == "__main__":
    main()
