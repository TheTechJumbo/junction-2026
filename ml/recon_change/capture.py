#!/usr/bin/env python3
"""Where the glasses went: the relay's day log turned into the walk the family sees in 3D.

The relay already keeps every position fix in its day log (day.py: one YYYY-MM-DD.jsonl per day in
<evidence dir>/day): when, which zone, whether the stabiliser was sure ("found"), and where on the
room map (east, north, in the map's metres), plus where the named places are and the sensor
events. This reads a window of it (by default the night: 20:00 the evening before to 10:00 on the
day), keeps the found fixes of one zone, and writes what night.py makes of them for the backend
(GET /night?day= serves <LOCAL_DATA_DIR>/mesh/night-YYYY-MM-DD.json):

    python capture.py <relay evidence dir>/day <LOCAL_DATA_DIR>/mesh [YYYY-MM-DD] [--zone kitchen] [--watch]

--watch writes it again every minute, so the night view follows the night as it happens.

Coordinates are the room map's (east, north, up; z up, floor at 0), the frame the relay's places and
fixes use. A mesh drawn in the same frame (splat_mesh.py --frame) puts the walk inside it.
Only "found" fixes are used: a lost or unsure fix says nothing about where the wearer was.
"""
import argparse
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import night

HOME = ZoneInfo("Europe/Helsinki")
EVENING, MORNING = "20:00", "10:00"
DOOR_TYPES = ("door_open", "door_closed")


def records(folder, days):
    """The day log's records of the given days, in time order."""
    out = []
    for d in days:
        path = Path(folder) / f"{d}.jsonl"
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return sorted(out, key=lambda r: r.get("t", 0))


def window(day, start=EVENING, end=MORNING):
    """(from, to) epoch seconds: `start` the evening before `day` to `end` on `day`, Helsinki time."""
    d = date.fromisoformat(day)
    h0, m0 = map(int, start.split(":")); h1, m1 = map(int, end.split(":"))
    t0 = datetime(d.year, d.month, d.day, h0, m0, tzinfo=HOME) - timedelta(days=1)
    t1 = datetime(d.year, d.month, d.day, h1, m1, tzinfo=HOME)
    return t0.timestamp(), t1.timestamp()


def capture(folder, day, zone=None, start=EVENING, end=MORNING):
    """The night before `day` from the day log: night.py's answer, in the room map's frame (up z)."""
    t0, t1 = window(day, start, end)
    prev = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    rows = [r for r in records(folder, [prev, day]) if t0 <= r.get("t", 0) <= t1]
    fixes = [r for r in rows if r.get("k") == "fix" and r.get("s") == "found" and isinstance(r.get("p"), list)]
    if zone is None and fixes:  # the zone the wearer was seen in most
        counts = {}
        for r in fixes:
            counts[r.get("z", "")] = counts.get(r.get("z", ""), 0) + 1
        zone = max(counts, key=counts.get)
    fixes = [r for r in fixes if r.get("z", "") == (zone or "")]
    places = {}
    for r in records(folder, [prev, day]):  # the newest named places of the zone, from before the window too
        if r.get("k") == "fix" and r.get("z", "") == (zone or "") and isinstance(r.get("wp"), dict) and r.get("t", 0) <= t1:
            places = {str(k).lower(): [float(v[0]), float(v[1]), 0.0] for k, v in r["wp"].items()}
    iso = lambda t: datetime.fromtimestamp(t, HOME).isoformat(timespec="seconds")  # noqa: E731
    track = [{"t": iso(r["t"]), "p": [float(r["p"][0]), float(r["p"][1]), 0.0]} for r in fixes]
    door = next((n for n in places if "door" in n and ("front" in n or "entr" in n)), None) or next((n for n in places if "door" in n), None)
    events = [{"t": iso(r["t"]), "type": r["type"], "place": door if r["type"] in DOOR_TYPES else None}
              for r in rows if r.get("k") == "event" and r.get("source") == "sensor"]
    out = night.analyse(track, places, events, up="z", day=day)
    out.update(places=places, zone=zone, window=[iso(t0), iso(t1)], source="relay day log")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("day_log"), ap.add_argument("out_dir"), ap.add_argument("day", nargs="?")
    ap.add_argument("--zone"), ap.add_argument("--from", dest="start", default=EVENING), ap.add_argument("--to", dest="end", default=MORNING)
    ap.add_argument("--watch", action="store_true", help="write it again every minute")
    a = ap.parse_args()
    Path(a.out_dir).mkdir(parents=True, exist_ok=True)
    while True:
        day = a.day or datetime.now(HOME).date().isoformat()
        out = capture(a.day_log, day, a.zone, a.start, a.end)
        target = Path(a.out_dir) / f"night-{day}.json"
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(out, indent=1), encoding="utf-8")
        tmp.replace(target)  # the backend never reads half a file
        print(target, out["summary"], f"{len(out['points'])} points in {out['zone']!r}", flush=True)
        if not a.watch:
            break
        time.sleep(60)


if __name__ == "__main__":
    main()
