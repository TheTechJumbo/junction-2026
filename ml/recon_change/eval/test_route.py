#!/usr/bin/env python3
"""Offline test of route.py: a made-up room with a table between the wearer and the pill box.

No cluster, no model, no numpy. The room is drawn as a position fix would carry it: points of the
walls and the furniture seen from above, and the places the baseline camera stood. The last part
runs the relay's real handler on a stand-in gateway and checks that /locate, /where and /route
carry the route.

    python eval/test_route.py
"""
import json
import math
import random
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "live")]
import route  # noqa: E402

# The room: 5 m east to west, 6 m south to north. The wearer stands near the south wall and looks
# north; the pill box hangs on the north wall straight ahead; a table stands in between.
WEST, EAST, SOUTH, NORTH = -2.5, 2.5, -1.0, 5.0
TABLE = (-0.8, 1.6, 0.8, 2.4)  # west, south, east, north
ME, PILL_BOX = [0.0, 0.0, 0.0], [0.0, 4.95, 0.0]
LOOP = [[-1.6, 0.0], [-1.6, 2.0], [-1.6, 4.0], [0.0, 4.0], [1.6, 4.0], [1.6, 2.0], [1.6, 0.0], [0.0, -0.2]]  # the baseline camera


def walls(step=0.05):
    out = []
    for i in range(int(round((EAST - WEST) / step)) + 1):
        out += [[WEST + i * step, SOUTH], [WEST + i * step, NORTH]]
    for i in range(int(round((NORTH - SOUTH) / step)) + 1):
        out += [[WEST, SOUTH + i * step], [EAST, SOUTH + i * step]]
    return out


def block(box, step=0.05):
    """A piece of furniture seen from above: its whole top."""
    w, s, e, n = box
    return [[w + i * step, s + j * step] for i in range(int(round((e - w) / step)) + 1) for j in range(int(round((n - s) / step)) + 1)]


def fix_of(points, places, position=ME, heading=0, cameras=LOOP, state="found"):
    return {"state": state, "position": list(position), "heading_deg": heading,
            "waypoints": [{"name": name, "position": list(at)} for name, at in places.items()],
            "map": {"points": [[round(x, 2), round(y, 2)] for x, y in points], "cameras": cameras}}


def gap_to_box(p, box):
    """Distance from a point to a rectangle (0 inside it)."""
    w, s, e, n = box
    return math.hypot(max(w - p[0], 0, p[0] - e), max(s - p[1], 0, p[1] - n))


def walk(points, step=0.02):
    """Every 2 cm along a line of points."""
    out = []
    for a, b in zip(points, points[1:]):
        k = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / step))
        out += [(a[0] + (b[0] - a[0]) * i / k, a[1] + (b[1] - a[1]) * i / k) for i in range(k + 1)]
    return out


def turned(p, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [p[0] * c - p[1] * s, p[0] * s + p[1] * c] + list(p[2:])


def check_room():
    # 1. The table is in the way: the route goes around it and says how far that is.
    room = walls() + block(TABLE)
    out = route.plan(fix_of(room, {"pill box": PILL_BOX}))
    way = out["to"]["pill box"]
    assert out["ok"] and way["way"] == "route" and way["why"] == "", out
    assert way["points"][0] == ME[:2] and way["points"][-1] == PILL_BOX[:2], way
    walked = way["points"][:-1]  # without the last leg, the reach from where the wearer stands to the place
    closest = min(gap_to_box(p, TABLE) for p in walk(walked))
    assert closest >= route.BODY_M / 2 - route.CELL_M, f"the route passes {closest:.2f} m from the table"
    assert 1 <= way["corners"] <= 4, way
    assert way["straight_m"] == 4.95 and way["straight_m"] * 1.05 < way["length_m"] < way["straight_m"] * 1.5, way
    assert abs(way["next"]["bearing_deg"]) >= 15, "the first leg must lead off to one side of the table"
    assert way["reach_m"] <= route.REACH_M and gap_to_box(walked[-1], (WEST, NORTH, EAST, NORTH)) <= route.REACH_M
    length = sum(math.dist(a, b) for a, b in zip(way["points"], way["points"][1:]))
    assert abs(length - way["length_m"]) < 0.05, (length, way["length_m"])
    print(f"table in the way: {way['length_m']:.2f} m on foot for {way['straight_m']:.2f} m straight, {way['corners']} corner(s), "
          f"never closer than {closest:.2f} m to the table, first leg {way['next']['bearing_deg']} degrees, {out['ms']} ms")

    # 2. Without the table the route is the straight line.
    out = route.plan(fix_of(walls(), {"pill box": PILL_BOX}))
    way = out["to"]["pill box"]
    assert way["way"] == "route" and way["corners"] == 0 and way["length_m"] <= way["straight_m"] * 1.02, way

    # 3. A place ON the table: the route ends beside the table, within reach of the place.
    out = route.plan(fix_of(room, {"cup": [0.0, 2.0, 0.0]}))
    way = out["to"]["cup"]
    stand = way["points"][-2]
    assert way["way"] == "route" and 0 < way["reach_m"] <= route.REACH_M, way
    assert gap_to_box(stand, TABLE) >= route.BODY_M / 2 - route.CELL_M and stand[1] < TABLE[1], way  # on the wearer's side

    # 4. The room turned by 30 degrees gives the same walk (the grid does not favour its own axes much).
    spun = fix_of([turned(p, 30) for p in room], {"pill box": turned(PILL_BOX, 30)}, heading=-30, cameras=[turned(c, 30) for c in LOOP])
    other = route.plan(spun)["to"]["pill box"]
    straight = route.plan(fix_of(room, {"pill box": PILL_BOX}))["to"]["pill box"]
    assert other["way"] == "route" and abs(other["length_m"] - straight["length_m"]) < 0.08 * straight["length_m"], (other, straight)
    assert abs(abs(other["next"]["bearing_deg"]) - abs(straight["next"]["bearing_deg"])) <= 12, (other["next"], straight["next"])

    # 4b. A floor plan that misses the table (as today's can) leads straight through it. If the fix
    # also carries "obstacles", the plan cut for walking, the route is made on that one.
    blind = fix_of(walls(), {"pill box": PILL_BOX})
    assert route.plan(blind)["to"]["pill box"]["corners"] == 0
    blind["map"]["obstacles"] = [[round(x, 2), round(y, 2)] for x, y in room]
    assert route.plan(blind)["to"]["pill box"]["corners"] >= 1

    # 5. Single points count: the plan has few points, so one may be all there is of a chair leg.
    # A few of them on the open floor are walked around, not through and not given up on.
    rng = random.Random(1)
    strays = [[rng.uniform(-2.2, 2.2), rng.uniform(-0.7, 4.7)] for _ in range(12)]
    strays = [p for p in strays if gap_to_box(p, TABLE) > 0.9 and math.dist(p, ME[:2]) > 0.9 and math.dist(p, PILL_BOX[:2]) > 1.5]
    dotted = route.plan(fix_of(room + strays, {"pill box": PILL_BOX}))["to"]["pill box"]
    assert dotted["way"] == "route", dotted
    nearest = min(math.dist(p, q) for p in walk(dotted["points"][:-1]) for q in strays)
    assert nearest >= route.BODY_M / 2 - route.CELL_M, f"the route passes {nearest:.2f} m from a single point"
    assert route.plan(fix_of(room + strays, {"pill box": PILL_BOX}), keep_lone=False)["to"]["pill box"]["length_m"] <= dotted["length_m"] + 0.05


def check_width():
    # A row of furniture across the room with one gap. One metre is wide enough for a person
    # (taken as 0.6 m); half a metre is not, and nothing else leads past.
    def room(gap):
        return walls() + block((WEST, 1.8, 1.0 - gap / 2, 2.2)) + block((1.0 + gap / 2, 1.8, EAST, 2.2))

    wide = route.plan(fix_of(room(1.0), {"pill box": PILL_BOX}, cameras=[[1.0, 0.5], [1.0, 2.0], [1.0, 3.5]]))["to"]["pill box"]
    assert wide["way"] == "route", wide
    crossing = [p for p in walk(wide["points"]) if 1.8 <= p[1] <= 2.2]
    assert crossing and all(0.5 + 0.2 <= p[0] <= 1.5 - 0.2 for p in crossing), "the route must pass through the gap, clear of both sides"
    narrow = route.plan(fix_of(room(0.5), {"pill box": PILL_BOX}))["to"]["pill box"]
    assert narrow["way"] == "straight" and narrow["why"] == "no way through on the map", narrow
    assert narrow["points"] == [ME[:2], PILL_BOX[:2]] and narrow["length_m"] == narrow["straight_m"] == 4.95, narrow
    print(f"a 1.0 m gap is used ({wide['length_m']:.2f} m), a 0.5 m gap is not: straight arrow, '{narrow['why']}'")


def check_fallbacks():
    room = walls() + block(TABLE)
    # Too few points to tell floor from furniture.
    out = route.plan(fix_of(room[::40], {"pill box": PILL_BOX}))
    assert not out["ok"] and out["note"] == "the map has too few points", out
    assert out["to"]["pill box"]["way"] == "straight" and out["to"]["pill box"]["why"] == out["note"]
    assert out["to"]["pill box"]["next"] == {"distance_m": 4.95, "bearing_deg": 0}
    # The empty map of live/test_care_flow.py.
    out = route.plan({"state": "found", "position": [0, 0, 0], "heading_deg": 0, "map": {"points": [], "cameras": []},
                      "waypoints": [{"name": "front door", "position": [0.0, -3.0, 0.0]}]})
    assert out["to"]["front door"]["way"] == "straight" and out["to"]["front door"]["next"]["bearing_deg"] in (180, -180), out
    # A lost fix has no position to start from.
    out = route.plan(fix_of(room, {"pill box": PILL_BOX}, state="lost"))
    assert not out["ok"] and out["note"] == "no position" and out["to"] == {}, out
    # The wearer is placed in the middle of something solid (a wrong fix): no start.
    solid = walls() + block((-1.5, -0.9, 1.5, 1.5))
    out = route.plan(fix_of(solid, {"pill box": PILL_BOX}, position=[0.0, 0.3, 0.0]))
    assert out["to"]["pill box"]["why"] == "no open floor where you stand", out
    # Standing right at a counter is fine: the route starts with the step away from it.
    out = route.plan(fix_of(room, {"pill box": PILL_BOX}, position=[0.0, 1.45, 0.0]))
    assert out["to"]["pill box"]["way"] == "route" and out["to"]["pill box"]["points"][0] == [0.0, 1.45], out
    # A place far outside everything the baseline saw cannot be reached.
    out = route.plan(fix_of(room, {"garden": [0.0, 9.0, 0.0]}))
    assert out["to"]["garden"]["why"] == "no way through on the map", out
    # Rubbish in, no crash out.
    for bad in ({}, {"state": "found"}, {"state": "found", "position": [0, 0, 0], "map": None, "waypoints": [{"name": "x"}]},
                {"state": "found", "position": [0, 0, 0], "map": {"points": [[None, 1], ["a"], [float("nan"), 2]] * 100}, "waypoints": []}):
        assert route.plan(bad)["to"] == {}, bad
    print("fallbacks: too few points, lost, boxed in, out of reach and rubbish all answer with the straight line or nothing")


def check_relay():
    """The relay's own handler on a stand-in gateway: the fix it passes on carries the route."""
    import relay

    relay.cv2 = None  # the canned photo is not a real picture
    fix = {**fix_of(walls() + block(TABLE), {"pill box": PILL_BOX}), "confidence": 0.9, "track": [[0, 0]], "match_per_frame": [0.9],
           "items": [{"name": "keys", "score": 0.4, "position": [1.9, 4.6, 0.0], "image": "item_keys"}], "items_looked_for": ["keys"]}
    for w in fix["waypoints"]:
        w.update(distance_m=4.9, bearing_deg=0)

    class Gateway:
        def call(self, header, blob=b""):
            if header["op"] == "locate":
                return {"ok": True, "job": "job1", "result": json.loads(json.dumps(fix))}, b""
            return {"ok": False, "error": "not in this test"}, b""

    handler = relay.make_handler(Gateway(), Path(tempfile.mkdtemp(prefix="beacon-route-test-")), "test-key", None, pick=None)
    handler.log_message = lambda *args: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path, raw=None):
        request = urllib.request.Request(url + path, data=raw, headers={"X-Key": "test-key"}, method="POST" if raw is not None else "GET")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read() or b"{}")

    jpeg = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")
    code, body = call("/locate?zone=kitchen", jpeg)
    assert code == 200 and body["route"]["ok"], body.get("route")
    way = body["route"]["to"]["pill box"]
    assert way["way"] == "route" and way["corners"] >= 1 and way["length_m"] > way["straight_m"], way
    assert body["route"]["to"]["keys"]["way"] == "route", "a seen item gets a route like a named place"
    assert body["waypoints"][0]["distance_m"] == 4.9, "the straight distance stays as it was"
    code, again = call("/where?zone=kitchen")
    assert code == 200 and again["route"]["to"]["pill box"]["points"] == way["points"], again.get("route")
    assert len(json.dumps(again, separators=(",", ":"))) < 64 * 1024, "the event API reads at most 64 kB from /where"
    # /route: the same from another standpoint, without a new photo (the display's sample room uses it).
    ask = {"position": [-1.6, 4.0], "heading_deg": 90, "map": fix["map"], "waypoints": fix["waypoints"]}
    code, moved = call("/route", json.dumps(ask).encode())
    assert code == 200 and moved["route"]["to"]["pill box"]["corners"] == 0, moved
    assert call("/route", b"not json")[0] == 400
    server.shutdown()
    print("relay: /locate and /where carry the route for places and seen items, /route answers from another standpoint")


def main():
    check_room()
    check_width()
    check_fallbacks()
    check_relay()
    print("route: all checks passed")


if __name__ == "__main__":
    main()
