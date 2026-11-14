#!/usr/bin/env python3
"""A made-up day in the day log, served by the real relay handler. No cluster, no event API.

    python live/day_demo.py              # a free port; prints the link to open
    python live/day_demo.py --port 8137

For looking at live/day.html while working on it. The day: a morning in a 5 by 4 m kitchen, the
pill box opened while next to it, a walk to the front door, a stretch with no information, the
keys question asked three times, a reminder nobody marked as done, and one uncertain visit.
Everything is written to a temporary folder; nothing here touches the real relay's files.
"""
import argparse
import json
import math
import sys
import tempfile
import time
from datetime import timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import day  # noqa: E402
import relay  # noqa: E402

KEY = "demo"
PLACES = {"pill box": (0.6, 3.5), "front door": (4.7, 0.3)}
# Where the wearer stands at which time (east, north in metres); in between they walk in a straight line.
WALK = [("08:10:00", 2.4, 1.4), ("08:12:00", 3.2, 2.4), ("08:13:54", 2.0, 2.6), ("08:14:00", 1.0, 3.0), ("08:16:00", 1.1, 2.9),
        ("08:16:30", 2.6, 1.9), ("08:17:00", 4.2, 0.9), ("08:19:00", 4.1, 1.0), ("08:21:00", 3.0, 2.3), ("08:26:00", 3.3, 2.9),
        ("08:28:06", 2.9, 2.6), ("08:33:00", 2.2, 1.3), ("08:40:00", 2.5, 1.5),
        ("10:00:00", 2.4, 1.5), ("10:04:00", 3.4, 2.8), ("10:08:00", 2.0, 2.2), ("10:11:54", 1.7, 2.6), ("10:12:00", 1.65, 2.5),
        ("10:12:06", 1.9, 2.3), ("10:16:00", 3.0, 1.2), ("10:20:00", 2.5, 1.5)]
SILENT = [("08:40:01", "09:59:59")]  # no photos at all
LOST = [("08:26:06", "08:28:00")]  # photos, but looking out of the window: no position


class NoCluster:
    """Stands in for the SSH link: there is no vision worker in this demo."""

    def call(self, header, blob=b""):
        return {"ok": False, "error": "This is the day log demo: no vision worker."}, b""


def seconds(clock):
    return sum(int(v) * s for v, s in zip(clock.split(":"), (3600, 60, 1)))


def stand(t):
    """Where the made-up wearer is t seconds after midnight, with a little sway."""
    for (a, ax, ay), (b, bx, by) in zip(WALK, WALK[1:]):
        if seconds(a) <= t <= seconds(b):
            f = (t - seconds(a)) / max(1, seconds(b) - seconds(a))
            return ax + f * (bx - ax) + 0.12 * math.sin(t / 17), ay + f * (by - ay) + 0.12 * math.cos(t / 23)
    return WALK[-1][1:]


def room_map():
    """Dots for the walls, a counter along the north wall and a table: what a fix's floor plan looks like."""
    dots = [[x / 10, y] for x in range(0, 53, 2) for y in (0.0, 4.0)] + [[x, y / 10] for y in range(0, 41, 2) for x in (0.0, 5.2)]
    dots += [[x / 10, 3.7] for x in range(2, 30, 2)] + [[x / 10, y / 10] for x in range(20, 31, 2) for y in (12, 18)]
    return dots


def fill(log, date):
    """Write the made-up day into `log`. date: a datetime.date in the home's time zone."""
    midnight = log.window(date.isoformat(), None)[0]
    at = lambda clock: midnight + seconds(clock)  # noqa: E731
    inside = lambda t, spans: any(seconds(a) <= t <= seconds(b) for a, b in spans)  # noqa: E731
    for t in range(seconds(WALK[0][0]), seconds(WALK[-1][0]) + 1, 6):
        if inside(t, SILENT):
            continue
        x, y = stand(t)
        ways = [{"name": n, "distance_m": round(math.dist((x, y), p), 2), "position": [p[0], p[1], 0.0]} for n, p in PLACES.items()]
        fix = {"state": "lost" if inside(t, LOST) else "found", "confidence": 0.8, "zone": "kitchen", "position": [x, y, 0.0], "waypoints": ways}
        if t == seconds("10:12:00"):  # as when the relay adds the stabiliser's answer (track.py), here only half sure
            fix["stable"] = {"state": "steady", "position": [x, y, 0.0], "heading_deg": 0, "confidence_pct": 55, "waypoints": ways}
        log.fix(fix, at=midnight + t)
    stamp = lambda clock: relay.home_time(at(clock)).isoformat(timespec="seconds")  # noqa: E731
    log.event({"id": "demo1", "type": "pillbox_open", "source": "sensor", "ts": stamp("08:15:00"), "zone": "kitchen", "object": "pillbox", "confidence": 1})
    log.event({"id": "demo2", "type": "door_open", "source": "sensor", "ts": stamp("09:10:00"), "zone": "entrance", "object": "front_door", "confidence": 1})
    log.event({"id": "demo3", "type": "object_moved", "source": "vision", "ts": stamp("10:05:00"), "zone": "kitchen", "object": "keys", "confidence": 0.6})
    for clock in ("10:00:10", "10:09:00", "10:19:30"):
        log.question("Where are my keys?", at=at(clock), topic="keys")
    log.question("Did I take my pills?", at=at("10:12:30"))
    log.item("keys", at=at("08:14:10"), near="pill box", zone="kitchen", position=[0.9, 3.6, 0.0])
    log.item("keys", at=at("10:06:00"), near="front door", zone="kitchen", position=[4.4, 0.7, 0.0])
    pills = {"id": "demo-r1", "text": "Time to take your pills", "place": "pill box", "due_at": at("08:00:00")}
    water = {"id": "demo-r2", "text": "Drink some water", "place": "", "due_at": at("09:00:00")}
    log.reminders([{**pills, "state": "due"}], at=at("08:00:05"))
    log.reminders([{**pills, "state": "done", "done_note": "The pill box was opened at 08:15."}], at=at("08:15:10"))
    log.reminders([{**water, "state": "due"}], at=at("09:00:05"))
    log.reminders([{**water, "state": "missed"}], at=at("11:00:05"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=0, help="0: any free port")
    args = ap.parse_args()
    evidence = Path(tempfile.mkdtemp(prefix="beacon-day-demo-"))
    now = relay.home_time(time.time())
    # The made-up day ends at 11:00. Before noon it is written as yesterday, so nothing lies in the future.
    date = now.date() if now.hour >= 12 else now.date() - timedelta(days=1)
    fill(day.DayLog(evidence / "day", clock=relay.home_time), date)
    # The latest position fix, as the relay keeps it: the day page takes the room's floor plan from it (GET /where).
    ways = [{"name": n, "distance_m": 2.0, "bearing_deg": 0, "position": [p[0], p[1], 0.0]} for n, p in PLACES.items()]
    (evidence / "where.json").write_text(json.dumps({"kitchen": {
        "ok": True, "zone": "kitchen", "state": "found", "confidence": 0.8, "position": [2.5, 1.5, 0.0], "heading_deg": 0, "waypoints": ways,
        "map": {"points": room_map(), "cameras": []}, "at": time.time() - 3 * 3600, "ts": "demo"}}))
    handler = relay.make_handler(NoCluster(), evidence, KEY, None, pick=None)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"day log demo: http://127.0.0.1:{server.server_address[1]}/dayview?k={KEY}&date={date.isoformat()}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
