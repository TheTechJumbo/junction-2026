#!/usr/bin/env python3
"""A made-up day in the day log, served by the real relay handler. No cluster, no event API.

    python live/day_demo.py              # a free port; prints the link to open
    python live/day_demo.py --port 8137

For looking at live/day.html while working on it. The day: a morning in the kitchen, the pill box
opened while next to it, a walk to the front door, a stretch with no information, the keys
question asked three times, a reminder nobody marked as done, and one uncertain visit.
Everything is written to a temporary folder; nothing here touches the real relay's files.
"""
import argparse
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


class NoCluster:
    """Stands in for the SSH link: there is no vision worker in this demo."""

    def call(self, header, blob=b""):
        return {"ok": False, "error": "This is the day log demo: no vision worker."}, b""


def fill(log, date):
    """Write the made-up day into `log`. date: a datetime.date in the home's time zone."""
    midnight = log.window(date.isoformat(), None)[0]
    at = lambda clock: midnight + sum(int(v) * s for v, s in zip(clock.split(":"), (3600, 60, 1)))  # noqa: E731

    def fixes(start, end, pill, door, state="found", every=6):
        t = at(start)
        while t <= at(end):
            ways = [{"name": "pill box", "distance_m": pill}, {"name": "front door", "distance_m": door}]
            log.fix({"state": state, "confidence": 0.8, "zone": "kitchen", "position": [1.0, 2.0, 0.0], "waypoints": ways}, at=t)
            t += every

    stamp = lambda clock: relay.home_time(at(clock)).isoformat(timespec="seconds")  # noqa: E731
    fixes("08:10:00", "08:13:54", 4.0, 5.0)
    fixes("08:14:00", "08:16:00", 0.8, 3.2)
    fixes("08:16:06", "08:16:54", 2.6, 2.4)
    fixes("08:17:00", "08:19:00", 3.4, 0.9)
    fixes("08:19:06", "08:26:00", 4.0, 5.0)
    fixes("08:26:06", "08:28:00", 4.0, 5.0, state="lost")  # looking out of the window: photos, no position
    fixes("08:28:06", "08:40:00", 4.0, 5.0)
    fixes("10:00:00", "10:11:54", 4.0, 5.0)
    fixes("10:12:00", "10:12:00", 1.45, 5.0)  # one fix at the very edge of the pill box: an uncertain visit
    fixes("10:12:06", "10:20:00", 4.0, 5.0)
    log.event({"id": "demo1", "type": "pillbox_open", "source": "sensor", "ts": stamp("08:15:00"), "zone": "kitchen", "object": "pillbox", "confidence": 1})
    log.event({"id": "demo2", "type": "door_open", "source": "sensor", "ts": stamp("09:10:00"), "zone": "entrance", "object": "front_door", "confidence": 1})
    log.event({"id": "demo3", "type": "object_moved", "source": "vision", "ts": stamp("10:05:00"), "zone": "kitchen", "object": "keys", "confidence": 0.6})
    for clock in ("10:00:10", "10:09:00", "10:19:30"):
        log.question("Where are my keys?", at=at(clock), topic="keys")
    log.question("Did I take my pills?", at=at("10:12:30"))
    log.item("keys", at=at("08:14:10"), near="pill box", zone="kitchen", position=[1.0, 2.0, 0.0])
    log.item("keys", at=at("10:06:00"), near="front door", zone="kitchen", position=[1.0, 5.0, 0.0])
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
    handler = relay.make_handler(NoCluster(), evidence, KEY, None, pick=None)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"day log demo: http://127.0.0.1:{server.server_address[1]}/dayview?k={KEY}&date={date.isoformat()}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
