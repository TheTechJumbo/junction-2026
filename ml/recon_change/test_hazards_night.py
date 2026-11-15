#!/usr/bin/env python3
"""Check hazards.py and night.py without a model or GPU, on the example home (demo_room.py).

  hazards   the bag on the way to the bathroom is in the way, named by that walk; the moved chair is
            reported but not in the way; nothing static (bed, table, counter) is ever a hazard;
            moving the bag off the walk takes it out of the way; the map's up axis is respected
  night     the example night has two trips (bathroom; front door and kitchen), the stops are at the
            right places, metres and minutes are plausible; a track with no bed is one trip; an
            empty track gives an empty night

    python test_hazards_night.py
"""
import sys

import numpy as np

import demo_room as dr
import hazards
import night


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    return ok


def test_hazards():
    ok = True
    m = dr.make_map()
    found = hazards.find(m, dr.PLACES, [tuple(w) for w in dr.WALKS], up="z")
    in_way = [z for z in found["hazards"] if z["in_the_way"]]
    ok &= check("one thing in the way", len(in_way) == 1)
    bag = in_way[0] if in_way else {}
    ok &= check("it is the bag, between the bed and the bathroom", bag.get("walk") == ["bed", "bathroom"] and 1.5 < bag["centre"][0] < 1.9)
    ok &= check("its size is about right", 0.3 <= bag.get("across_m", 0) <= 0.45 and 0.2 <= bag.get("height_m", 0) <= 0.35)
    ok &= check("the moved chair is reported, not in the way", any(not z["in_the_way"] and z["near"] == "table" for z in found["hazards"]))
    static_spots = [(0.65, 3.3), (2.2, 1.8), (3.9, 3.6), (4.1, 0.8)]  # bed, table, counter, sofa
    ok &= check("nothing static is a hazard", all(min(np.hypot(z["centre"][0] - x, z["centre"][1] - y) for x, y in static_spots) > 0.3
                                                for z in found["hazards"] if z["near"] != "table"))
    # The same map with the up axis declared as VGGT's: z becomes "forward", so nothing is at knee height in the same way.
    rotated = dr.make_map()
    p = rotated.splats["pos"].copy()
    rotated.splats["pos"] = np.stack([p[:, 0], -p[:, 2], p[:, 1]], 1)  # z-up -> y-down
    places = {k: [v[0], -v[2], v[1]] for k, v in dr.PLACES.items()}
    again = hazards.find(rotated, places, [tuple(w) for w in dr.WALKS], up="-y")
    ok &= check("a y-down map finds the same bag", [z["walk"] for z in again["hazards"] if z["in_the_way"]] == [["bed", "bathroom"]])
    # Move the bag well clear of every walk: nothing is in the way any more.
    moved = dr.make_map()
    bag_gaussians = (moved.splats["rgba"][:, :3] == dr.BAG).all(1)
    moved.splats["pos"][bag_gaussians] += np.array([2.6, -2.6, 0], np.float32)  # to (4.3, 0.5): by the sofa, on no walk
    moved.owner[bag_gaussians] = hazards.sr.cell_ids(moved.splats["pos"][bag_gaussians].astype(np.float64), moved.cell)
    moved.keys = np.unique(np.concatenate([moved.keys, moved.owner[bag_gaussians]]))
    lvl = {k: (l, mv) for k, l, mv in zip(dr.make_map().keys, dr.make_map().level, dr.make_map().movable)}
    moved.level = np.array([lvl.get(k, (0, True))[0] for k in moved.keys], np.int8)
    moved.movable = np.array([lvl.get(k, (0, True))[1] for k in moved.keys])
    clear = hazards.find(moved, dr.PLACES, [tuple(w) for w in dr.WALKS], up="z")
    ok &= check("a bag off the walks is not in the way", not any(z["in_the_way"] for z in clear["hazards"]))
    return ok


def test_night():
    ok = True
    track, events = dr.night_track()
    out = night.analyse(track, dr.PLACES, events, up="z", day="2026-11-15")
    s = out["summary"]
    ok &= check("two trips out of bed", s["trips"] == 2)
    ok &= check("first trip: the bathroom", out["trips"][0]["visits"] == ["bathroom"])
    ok &= check("second trip: front door, then kitchen", out["trips"][1]["visits"] == ["front door", "kitchen"])
    ok &= check("minutes up and metres plausible", 12 <= s["minutes_up"] <= 25 and 8 <= s["metres"] <= 25)
    ok &= check("stops know their places", {st["place"] for st in out["stops"]} >= {"bed", "bathroom", "front door", "kitchen"})
    ok &= check("the track is kept in time order, thinned", len(out["points"]) <= night.MAX_POINTS and
                [p["t"] for p in out["points"]] == sorted(p["t"] for p in out["points"]))
    ok &= check("events pass through", out["events"] == events)
    no_bed = night.analyse(track, {k: v for k, v in dr.PLACES.items() if k != "bed"}, up="z")
    ok &= check("without a bed place the whole night is one trip", no_bed["summary"]["trips"] == 1)
    empty = night.analyse([], dr.PLACES, up="z")
    ok &= check("an empty night is empty", empty["summary"]["trips"] == 0 and empty["points"] == [])
    return ok


if __name__ == "__main__":
    results = [test_hazards(), test_night()]
    sys.exit(0 if all(results) else 1)
