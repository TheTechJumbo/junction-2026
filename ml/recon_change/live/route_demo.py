#!/usr/bin/env python3
"""See route finding on the glasses display without the cluster.

The relay's real handler on a stand-in gateway, with one made-up position fix: a living room with
a sofa and a table between the wearer and the pill box. The fix is made up; the route on it is
worked out by route.py exactly as for a real fix.

    python live/route_demo.py            # prints two links

    .../hud?k=KEY           the made-up fix as the relay passes it on (GET /where): arrow to the next
                            corner, walking distance, the way on the small map
    .../hud?k=KEY&demo=1    the display's own sample room: walk with the arrow keys and the route
                            follows (the page asks POST /route from each new standpoint)
"""
import argparse
import json
import math
import sys
import tempfile
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import relay  # noqa: E402

ZONE = "living room"


def box(w, s, e, n, step=0.06):
    """A piece of furniture seen from above: its whole top."""
    return [[w + i * step, s + j * step] for i in range(int((e - w) / step) + 1) for j in range(int((n - s) / step) + 1)]


def made_up_fix():
    points = []
    for i in range(int(5.0 / 0.05) + 1):  # walls: 5 m east to west, 6 m south to north
        points += [[-2.5 + i * 0.05, -1.0], [-2.5 + i * 0.05, 5.0]]
    for i in range(int(6.0 / 0.05) + 1):
        points += [[-2.5, -1.0 + i * 0.05], [2.5, -1.0 + i * 0.05]]
    points += box(-0.9, 1.5, 0.9, 2.3) + box(-2.4, 3.2, -1.5, 4.9) + box(1.3, -0.9, 2.4, 0.2)  # table, sofa, cupboard
    position, heading = [0.2, 0.0, 0.0], 0
    places = {"pill box": [0.3, 4.95, 0.0], "front door": [-1.2, -0.95, 0.0], "bathroom door": [2.45, 3.4, 0.0]}
    waypoints = []
    for name, at in places.items():
        east, north = at[0] - position[0], at[1] - position[1]
        waypoints.append({"name": name, "position": at, "distance_m": round(math.hypot(east, north), 1),
                          "bearing_deg": int(round((math.degrees(math.atan2(east, north)) - heading + 180) % 360 - 180))})
    return {"ok": True, "zone": ZONE, "job": "made-up", "headline": "Made-up fix", "detail": "", "state": "found", "confidence": 1.0,
            "position": position, "heading_deg": heading, "waypoints": waypoints, "track": [position[:2]],
            "map": {"points": [[round(x, 2), round(y, 2)] for x, y in points],
                    "cameras": [[-1.6, 0.0], [-1.6, 2.0], [-1.0, 3.6], [0.4, 3.8], [1.7, 3.4], [1.7, 1.6], [0.6, 0.2]]},
            "images": {}, "splat": None, "seconds": {}, "ts": "made-up", "at": time.time()}


class Gateway:
    """Stands in for the SSH link to the cluster: it knows one room and takes no photos."""

    def call(self, header, blob=b""):
        if header["op"] == "status":
            return {"ok": True, "zones": [ZONE], "workers": {}}, b""
        return {"ok": False, "error": "This is the route demo: no cluster is connected."}, b""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=0, help="0 picks a free one")
    ap.add_argument("--token", default="route-demo", help="the access key in the links (this serves nothing but made-up data)")
    args = ap.parse_args()
    evidence = Path(tempfile.mkdtemp(prefix="beacon-route-demo-"))
    (evidence / "where.json").write_text(json.dumps({ZONE: made_up_fix()}))
    handler = relay.make_handler(Gateway(), evidence, args.token, None, pick=None)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"made-up fix:   {base}/hud?k={args.token}", flush=True)
    print(f"sample room:   {base}/hud?k={args.token}&demo=1   (arrow keys walk)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
