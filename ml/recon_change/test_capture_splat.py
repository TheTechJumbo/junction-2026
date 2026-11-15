#!/usr/bin/env python3
"""Check capture.py (the walk from the relay's day log) and splat_hazards.py (hazards from one splat),
without a model, GPU or relay, on the example home (demo_room.py).

  capture        a day log written the way day.py writes it (fix records with "p" and "wp", sensor
                 events) over two files around midnight becomes the example night: two trips, the
                 bathroom then the front door and kitchen, the door events placed at the front door;
                 lost and unsure fixes, other zones and anything outside 20:00-10:00 are left out
  splat hazards  the example home as ONE splat (nothing marked as moved): the bag is a possible
                 hazard between the bed and the bathroom, the chair is reported but not in the way,
                 no furniture (bed, sofa, counter, table) and no wall is flagged; faint Gaussians and a
                 floater do not count; a splat in a pass's own frame, moved with a room frame, gives the
                 same answer

    python test_capture_splat.py
"""
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np

import capture
import demo_room as dr
import splat_hazards as shz
import splat_refine as sr

failures = []


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        failures.append(name)


def day_log(folder):
    """The example night as the relay's day log: one line per fix, in the files day.py writes."""
    track, events = dr.night_track("2026-11-15")
    places = {k: v[:2] for k, v in dr.PLACES.items()}
    lines = {}

    def add(record):
        day = datetime.fromtimestamp(record["t"], capture.HOME).date().isoformat()
        lines.setdefault(day, []).append(json.dumps(record))

    t_evening = datetime.fromisoformat("2026-11-14T22:30:00+02:00").timestamp()
    add({"k": "fix", "t": t_evening, "s": "found", "z": "flat", "p": [0.7, 3.3], "wp": places})   # in bed before midnight
    add({"k": "fix", "t": t_evening - 4 * 3600, "s": "found", "z": "flat", "p": [3.0, 3.0]})      # 18:30: outside the window
    for n, f in enumerate(track):
        t = datetime.fromisoformat(f["t"]).timestamp()
        add({"k": "fix", "t": t, "s": "found", "z": "flat", "p": f["p"][:2]})
        if n % 7 == 3:
            add({"k": "fix", "t": t + 1, "s": "lost", "z": "flat"})                                # lost: no position
        if n % 11 == 5:
            add({"k": "fix", "t": t + 2, "s": "found", "z": "hall", "p": [9.0, 9.0]})              # another zone's map
    for e in events:
        add({"k": "event", "t": datetime.fromisoformat(e["t"]).timestamp(), "type": e["type"], "source": "sensor", "z": "hall", "object": None})
    for day, rows in lines.items():
        (Path(folder) / f"{day}.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_capture():
    with tempfile.TemporaryDirectory() as d:
        day_log(d)
        out = capture.capture(d, "2026-11-15")
    s = out["summary"]
    check("the zone seen most is chosen", out["zone"] == "flat")
    check("two trips out of bed", s["trips"] == 2)
    check("bathroom, then front door and kitchen", [t["visits"] for t in out["trips"]] == [["bathroom"], ["front door", "kitchen"]])
    check("door events are placed at the front door", [e["place"] for e in out["events"]] == ["front door", "front door"])
    check("the evening before midnight is in, 18:30 is not", out["from"].startswith("2026-11-14T22:30") and len(out["points"]) > 100)
    check("no other zone's positions", all(p["p"][0] < 6 for p in out["points"]))
    check("up is z, places are the relay's", out["up"] == "z" and out["places"]["bed"][:2] == dr.PLACES["bed"][:2])
    with tempfile.TemporaryDirectory() as d:
        empty = capture.capture(d, "2026-11-15")
    check("no day log: an empty night", empty["summary"]["trips"] == 0 and empty["points"] == [])


def one_splat(extra=None):
    """The example home as one trained splat: every Gaussian alike, nothing marked as moved."""
    rec = dr.make_map().splats.copy()
    faint = np.zeros(300, sr.RECORD)
    faint["pos"], faint["rgba"][:, 3] = np.random.default_rng(1).uniform([0, 0, 0], [5, 4, 0.5], (300, 3)), 20
    floater = np.zeros(1, sr.RECORD)
    floater["pos"], floater["scale"], floater["rgba"][:, 3] = (2.5, 1.0, 0.3), 2.0, 255
    return np.concatenate([rec, faint, floater] + ([extra] if extra is not None else []))


def test_splat_hazards():
    rec = one_splat()
    positions, up = shz.load(rec.tobytes(), up="z")
    out = shz.find(positions, dr.PLACES, [tuple(w) for w in dr.WALKS], up)
    way = [z for z in out["hazards"] if z["in_the_way"]]
    check("one possible hazard in the way", len(way) == 1)
    check("it is the bag, between the bed and the bathroom", bool(way) and way[0]["walk"] == ["bed", "bathroom"] and 1.5 < way[0]["centre"][0] < 1.9)
    check("it says it is a guess", bool(way) and way[0]["words"].startswith("Possible trip hazard") and way[0]["method"] == "shape")
    check("the chair is reported, not in the way", any(not z["in_the_way"] and z["near"] == "table" for z in out["hazards"]))
    furniture = {"bed": (0.65, 3.3), "sofa": (4.1, 0.65), "counter": (3.9, 3.65), "table": (2.2, 1.8)}
    check("no furniture is flagged", all(min(np.hypot(z["centre"][0] - x, z["centre"][1] - y) for x, y in furniture.values()) > 0.35
                                         for z in out["hazards"] if z["near"] != "table"))
    check("no wall is flagged", all(z["centre"][0] > 0.15 and z["centre"][1] < 3.85 for z in out["hazards"]))
    # The same splat in a pass's own frame (y down, half-unit scale, shifted), moved back with a room frame.
    frame = {"origin": np.array([1.0, 2.0, 3.0]), "east": np.array([1.0, 0, 0]), "north": np.array([0, 0, 1.0]),
             "up": np.array([0, -1.0, 0]), "metres": 2.0}
    p = rec["pos"].astype(np.float64)
    passed = rec.copy()
    passed["pos"] = (np.stack([p[:, 0], -p[:, 2], p[:, 1]], 1) / 2.0 + frame["origin"]).astype(np.float32)
    back, up2 = shz.load(passed.tobytes(), frame)
    again = shz.find(back, dr.PLACES, [tuple(w) for w in dr.WALKS], up2)
    check("a splat in its pass's frame, moved with the room frame, gives the same answer",
          [z["walk"] for z in again["hazards"] if z["in_the_way"]] == [["bed", "bathroom"]])


if __name__ == "__main__":
    test_capture()
    test_splat_hazards()
    print("\nAll checks passed." if not failures else f"\n{len(failures)} check(s) failed.")
    sys.exit(1 if failures else 0)
