#!/usr/bin/env python3
"""Offline test of named places that need no new picture: a spot named on the newest photo (POST /waypoint?live=1),
places named in a picture placed from the pictures as they are (worker op "spots", no map built), and things the item
finder sees in two fixes that agree named by themselves ("auto"), never over a place someone named. No cluster.

    python live/test_places.py
"""
import io
import json
import math
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import relay  # noqa: E402
from test_view_gate import FIX, jpeg, room  # noqa: E402

KEY = "test-key"
ZONE = "placetest"


class Gateway:
    """Stands in for the cluster: keeps the zone's named places as the gateway does, places a picked point at a fixed
    spot, and lets the test choose what the item finder sees in each fix."""

    def __init__(self):
        self.places, self.calls, self.items = [], [], []

    def call(self, header, blob=b""):
        self.calls.append(header)
        op = header["op"]
        if op == "waypoints":
            return {"ok": True, "zone": header["zone"], "frames": 5, "waypoints": list(self.places)}, b""
        if op == "waypoint":
            self.places = [p for p in self.places if p["name"] != header["name"]]
            if header.get("position") is not None:
                self.places.append({"name": header["name"], "position": header["position"],
                                    "source": "auto" if header.get("auto") else "live"})
            elif not header.get("delete"):
                self.places.append({k: header[k] for k in ("name", "frame", "u", "v")})
            return {"ok": True, "zone": header["zone"], "frames": 5, "waypoints": list(self.places)}, b""
        if op == "spots":  # a place in a picture lands at (frame, u, v) in metres; one with a position stays there
            placed = [{"name": p["name"], "position": [float(p["frame"]), p["u"], p["v"]]} if "frame" in p
                      else {"name": p["name"], "position": p["position"]} for p in self.places]
            return {"ok": True, "job": "spots1", "result": {"places": placed, "spots": []}}, b""
        if op == "locate":
            result = json.loads(json.dumps(FIX))
            if header.get("point"):
                result["point"] = [1.0, 2.0, 0.5]
            result["items"] = self.items.pop(0) if self.items else []
            return {"ok": True, "job": f"fix{len(self.calls)}", "result": result}, b""
        return {"ok": True, "zones": [ZONE], "workers": {}}, b""


def main():
    gateway = Gateway()
    evidence = Path(tempfile.mkdtemp(prefix="beacon-places-test-"))
    (evidence / "map0").mkdir()
    (evidence / "map0" / "room.splat").write_bytes(bytes(32))
    (evidence / "rooms.json").write_text(json.dumps({ZONE: {"zone": ZONE, "job": "map0", "splat": "/evidence/map0/room.splat",
                                                            "at": time.time(), "frames": 5, "places": []}}))
    handler = relay.make_handler(gateway, evidence, KEY, None, pick=None)
    handler.log_message = lambda *args: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path, raw=None):
        request = urllib.request.Request(url + path, data=raw, headers={"X-Key": KEY}, method="POST" if raw is not None else "GET")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def places():
        return {p["name"]: p for p in call(f"/room?zone={ZONE}")[1].get("places") or []}

    # 1. No photo yet: a live place cannot be named.
    status, reply = call(f"/waypoint?zone={ZONE}&name=front+door&live=1&u=0.5&v=0.5", b"")
    assert status == 404, (status, reply)

    # 2. A fix: every fix also looks for the AUTO_PLACES things.
    call(f"/locate?zone={ZONE}", jpeg(room(1)))
    asked = [c for c in gateway.calls if c["op"] == "locate"][-1]
    assert asked["look_for"][:len(relay.AUTO_PLACES)] == relay.AUTO_PLACES and asked["grow"] is True, asked  # + find me X's

    # 3. A spot on the newest photo: its own fix places it (no growing), it is kept with its position, and listed.
    status, reply = call(f"/waypoint?zone={ZONE}&name=front+door&live=1&u=0.25&v=0.75", b"")
    assert status == 200 and reply["position"] == [1.0, 2.0, 0.5], (status, reply)
    asked = [c for c in gateway.calls if c["op"] == "locate"][-1]
    assert asked["point"] == {"u": 0.25, "v": 0.75} and asked["grow"] is False, asked
    assert gateway.places[-1] == {"name": "front door", "position": [1.0, 2.0, 0.5], "source": "live"}, gateway.places
    assert places()["front door"]["position"] == [1.0, 2.0, 0.5]

    # 3b. The photo the page showed, sent with the request, is the one placed (not a newer one).
    call(f"/locate?zone={ZONE}", jpeg(room(9)))
    sent = len(gateway.calls)
    status, reply = call(f"/waypoint?zone={ZONE}&name=sofa&live=1&u=0.5&v=0.5", jpeg(room(8)))
    asked = [c for c in gateway.calls[sent:] if c["op"] == "locate"]
    assert status == 200 and len(asked) == 1 and "sofa" in places(), (status, reply)
    call(f"/waypoint?zone={ZONE}&name=sofa&delete=1", b"")
    assert "sofa" not in places()

    # 3c. A place picked on the map (the 3D page's view from above): stored at that map position.
    status, reply = call(f"/waypoint?zone={ZONE}&name=exit&east=1.5&north=-2&up=0.9", b"")
    assert status == 200 and places()["exit"]["position"] == [1.5, -2.0, 0.9] and places()["exit"]["source"] == "map", (status, reply)
    assert gateway.calls[-1]["source"] == "map" and gateway.calls[-1]["position"] == [1.5, -2.0, 0.9], gateway.calls[-1]
    call(f"/waypoint?zone={ZONE}&name=exit&delete=1", b"")

    # 4. A place named in a picture: placed from the pictures as they are (op "spots"), no map built.
    status, reply = call(f"/waypoint?zone={ZONE}&name=our+table&frame=3&u=0.6&v=0.86", b"")
    assert status == 200, reply
    for _ in range(100):
        if "our table" in places():
            break
        time.sleep(0.05)
    assert places()["our table"]["position"] == [3.0, 0.6, 0.86] and "front door" in places(), places()
    assert not any(c["op"] in ("map", "recording_map") for c in gateway.calls), [c["op"] for c in gateway.calls]
    status, reply = call(f"/places?zone={ZONE}", b"")
    assert status == 200 and {p["name"] for p in reply["places"]} == {"front door", "our table"}, reply

    # 5. Automatic: one sighting is not enough; two fixes that agree name the place; a far one does not move it;
    #    a weak one is not counted; the place someone named ("front door") is never moved by sightings.
    pill = lambda x, s=0.6: {"name": "pill box", "position": [x, 1.0, 0.8], "score": s}  # noqa: E731
    door = {"name": "front door", "position": [5.0, 5.0, 1.0], "score": 0.9}
    gateway.items = [[pill(0.0), door], [pill(0.1, 0.2)], [pill(0.15), door], [pill(3.0)]]
    call(f"/locate?zone={ZONE}", jpeg(room(2)))
    assert "pill box" not in places()
    call(f"/locate?zone={ZONE}", jpeg(room(3)))  # too weak to count
    assert "pill box" not in places()
    status, fix = call(f"/locate?zone={ZONE}", jpeg(room(4)))
    got = places()["pill box"]
    assert got["source"] == "auto" and got["views"] == 2 and math.isclose(got["position"][0], 0.15), got
    assert fix["places_named"][0]["name"] == "pill box", fix.get("places_named")
    assert places()["front door"]["position"] == [1.0, 2.0, 0.5] and gateway.places[0]["source"] == "live", gateway.places
    call(f"/locate?zone={ZONE}", jpeg(room(5)))  # one sighting far away: the place stays
    assert math.isclose(places()["pill box"]["position"][0], 0.15), places()["pill box"]
    server.shutdown()
    print("ok: live places, places from the pictures as they are, and auto places from agreeing fixes")


if __name__ == "__main__":
    main()
