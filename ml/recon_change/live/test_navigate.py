#!/usr/bin/env python3
"""Offline test of GET /navigate (the way from the wearer to a named place) on a test zone with made-up fixes: loose
names, an unknown name listing the places, the turn in words, arriving, a stale or unsure fix, and a route around a
wall from route.py. No cluster.

    python live/test_navigate.py
"""
import json
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import relay  # noqa: E402
from test_view_gate import FIX, jpeg, room  # noqa: E402

KEY = "test-key"
ZONE = "navtest"
# A room 6 m by 7 m (its outline, a point every 10 cm) with a wall from east -1.5 to 1.5 m, 1.5 m north of the wearer
OUTLINE = ([[x / 10, -2.0] for x in range(-30, 31)] + [[x / 10, 5.0] for x in range(-30, 31)]
           + [[-3.0, y / 10] for y in range(-20, 51)] + [[3.0, y / 10] for y in range(-20, 51)])
WALL = OUTLINE + [[x / 10, 1.5] for x in range(-15, 16)]


class Gateway:
    """The next fix it answers is whatever the test puts in self.next."""

    def __init__(self):
        self.next = None

    def call(self, header, blob=b""):
        if header["op"] == "locate":
            return {"ok": True, "job": f"fix{time.time()}", "result": json.loads(json.dumps(self.next))}, b""
        return {"ok": True, "zones": [ZONE], "workers": {}}, b""


def fix(position, heading=0, state="found", points=()):
    made = json.loads(json.dumps(FIX))
    made.update(state=state, position=position, heading_deg=heading, map={"points": list(points), "cameras": [[0, -1], [0, -2]]},
                waypoints=[{"name": "pill box", "distance_m": 0, "bearing_deg": 0, "position": [0.0, 3.0, 0.0]},
                           {"name": "front door", "distance_m": 0, "bearing_deg": 0, "position": [3.0, 0.0, 0.0]},
                           {"name": "kitchen table", "distance_m": 0, "bearing_deg": 0, "position": [1.0, 1.0, 0.0]}])
    return made


def main():
    gateway = Gateway()
    evidence = Path(tempfile.mkdtemp(prefix="beacon-nav-test-"))
    # Another zone with a room map newer than any fix (made an hour from now): its map is the room map's.
    import numpy as np
    (evidence / "mapnav").mkdir()
    gauss = np.zeros(2000, dtype=[("pos", "<f4", 3), ("rest", "u1", 20)])
    gauss["pos"] = np.random.default_rng(1).uniform([-3, -2, -1.6], [3, 5, 1.5], (2000, 3))
    gauss.tofile(evidence / "mapnav" / "room.splat")
    (evidence / "rooms.json").write_text(json.dumps({"navroom": {
        "zone": "navroom", "job": "mapnav", "splat": "/evidence/mapnav/room.splat", "at": time.time() + 3600,
        "cameras": [{"frame": 0, "position": [0.0, 0.0, 0.0], "heading_deg": 0}], "places": [{"name": "sofa", "position": [1.0, 2.0, 0.0]}]}}))
    handler = relay.make_handler(gateway, evidence, KEY, None, pick=None)
    handler.log_message = lambda *args: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    seed = [10]

    def send(made):  # a new photo each time, so the relay's same-view gate asks the "model"
        gateway.next = made
        seed[0] += 1
        request = urllib.request.Request(f"{url}/locate?zone={ZONE}", data=jpeg(room(seed[0])), headers={"X-Key": KEY}, method="POST")
        urllib.request.urlopen(request, timeout=20).read()

    def nav(to, zone=ZONE):
        request = urllib.request.Request(f"{url}/navigate?zone={zone}&to={urllib.parse.quote(to)}", headers={"X-Key": KEY})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    # Before any fix: a clear 404.
    status, got = nav("pill box")
    assert status == 404 and not got["ok"], got

    # A zone whose newest room map came after its newest fix (or with no fix): the map is the room map's, no position.
    status, got = nav("", zone="navroom")
    assert status == 200 and got["position"] is None and got["stale"] and "room map" in got["stale_reason"], got
    assert got["plan"]["from"] == "room map" and 100 < len(got["plan"]["points"]) <= relay.NAV_PLAN_POINTS, len(got["plan"]["points"])
    assert all(-3.01 <= x <= 3.01 and -2.01 <= y <= 5.01 for x, y in got["plan"]["points"]) and got["places"][0]["name"] == "sofa", got["places"]
    status, got = nav("sofa", zone="navroom")
    assert status == 409 and got["stale"], (status, got)

    # Facing north at the origin, nothing in the way.
    send(fix([0.0, 0.0, 0.0]))
    for asked in ("pill box", "Pill Box", "pillbox", "the pill box", "pill", "pil box"):
        status, got = nav(asked)
        assert status == 200 and got["to"] == "pill box", (asked, status, got)
    assert got["instruction"] == "ahead" and not got["arrived"] and abs(got["distance_m"] - 3.0) < 0.3 and not got["stale"], got
    assert got["route"][0] == [0.0, 0.0] and got["route"][-1] == [0.0, 3.0], got["route"]
    assert {p["name"] for p in got["places"]} == {"pill box", "front door", "kitchen table"} and "points" in got["plan"], got
    status, got = nav("front door")
    assert got["instruction"] == "turn right" and got["bearing_deg"] == 90, got
    status, got = nav("table")
    assert got["to"] == "kitchen table" and got["instruction"] == "slightly right", got

    # No place asked: the wearer's map alone (no route, no instruction).
    status, got = nav("")
    assert status == 200 and got["ok"] and got["position"] == [0.0, 0.0, 0.0] and got["heading_deg"] == 0, got
    assert len(got["places"]) == 3 and "points" in got["plan"] and "route" not in got and "instruction" not in got and not got["stale"], got

    # While someone is being guided, /where says to where (for the caretaker's page); stop ends it.
    nav("pill box")
    where = json.loads(urllib.request.urlopen(urllib.request.Request(f"{url}/where?zone={ZONE}", headers={"X-Key": KEY}), timeout=20).read())
    assert where.get("navigate", {}).get("name") == "pill box" and where["navigate"]["active"] and where["navigate"]["distance_m"] > 2, where.get("navigate")
    assert where["stable"] is None or where["stable"].get("navigate", {}).get("name") == "pill box", where["stable"]
    urllib.request.urlopen(urllib.request.Request(f"{url}/navigate?zone={ZONE}&stop=1", headers={"X-Key": KEY}), timeout=20).read()
    where = json.loads(urllib.request.urlopen(urllib.request.Request(f"{url}/where?zone={ZONE}", headers={"X-Key": KEY}), timeout=20).read())
    assert "navigate" not in where, where.get("navigate")

    # An unknown place: the error names the places there are.
    status, got = nav("bathroom")
    assert status == 404 and "pill box" in got["error"] and "front door" in got["error"] and len(got["places"]) == 3, got

    # Facing east: the pill box (north) is to the left.
    send(fix([0.0, 0.0, 0.0], heading=90))
    status, got = nav("pill box")
    assert got["instruction"] == "turn left" and got["bearing_deg"] == -90, got

    # Standing next to it: there, once the steady position has moved there (a jump of 2.6 m in one photo is not
    # believed at once: the steady pose waits for agreeing fixes, as the glasses' display does).
    for _ in range(8):
        send(fix([0.2, 2.6, 0.0]))
        status, got = nav("pill box")
        if got["arrived"]:
            break
    assert got["arrived"] and got["instruction"] == "you're there", got

    # A wall between the wearer and the pill box: the route goes around it, longer than the straight line.
    for _ in range(8):  # back at the start (the steady position follows the fixes)
        send(fix([0.0, 0.0, 0.0], points=WALL))
        status, got = nav("pill box")
        if got["position"][:2] == [0.0, 0.0]:
            break
    assert len(got["route"]) > 2 and got["distance_m"] > got["straight_m"] + 0.3, got

    # Unsure, then old: stale, with the reason; lost: no guess at all.
    send(fix([0.0, 0.0, 0.0], state="unsure"))
    status, got = nav("pill box")
    assert got["stale"] and "unsure" in got["stale_reason"], got
    send(fix([0.0, 0.0, 0.0]))
    relay.NAV_STALE_S = 0.2
    time.sleep(0.4)
    status, got = nav("pill box")
    assert got["stale"] and "old" in got["stale_reason"], got
    relay.NAV_STALE_S = 10.0
    send(fix([0.0, 0.0, 0.0], state="lost"))
    status, got = nav("pill box")
    assert status in (200, 409) and got["stale"], (status, got)
    status, got = nav("")  # the map still comes, without a pose
    assert status == 200 and got["position"] is None and got["stale"] and len(got["places"]) == 3, got

    # A fix in the zone with the newer room map: still the room map's, until a fix comes after it.
    gateway.next = fix([0.0, 0.0, 0.0])
    seed[0] += 1
    urllib.request.urlopen(urllib.request.Request(f"{url}/locate?zone=navroom", data=jpeg(room(seed[0])), headers={"X-Key": KEY}, method="POST"), timeout=20).read()
    status, got = nav("", zone="navroom")
    assert status == 200 and got["position"] is None and got["plan"]["from"] == "room map", got

    # Many plan points: cut to NAV_PLAN_POINTS.
    send(fix([0.0, 0.0, 0.0], points=[[i / 1000, -2.0] for i in range(1500)]))
    status, got = nav("pill box")
    assert len(got["plan"]["points"]) <= relay.NAV_PLAN_POINTS, len(got["plan"]["points"])
    server.shutdown()
    print("ok: /navigate matches loose names, gives the turn in words, arrives, routes around a wall and says when stale")


if __name__ == "__main__":
    main()
