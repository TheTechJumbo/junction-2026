#!/usr/bin/env python3
"""The night, walked: where the wearer went after going to bed, for the family's night view.

Input is the position track the relay keeps for the glasses (one fix every few seconds, in the
map's coordinates) between bedtime and morning, the named places, and the sensor events of the
night (door, pill box). Out comes what the carer wants to know at a glance:

    trips   each time out of bed: from leaving the bed (farther than BED_M from it) to coming
            back, with the places stopped at
    stops   staying within STOP_M for at least STOP_S, with the nearest named place
    summary how many times up, minutes out of bed, metres walked, places visited

and the track itself, thinned to at most MAX_POINTS, for the trail and the time slider.
Nothing here judges: wandering, a fall or a missed toilet visit are for people to read from it.

    python night.py track.json places.json night.json [up]

track.json: [{"t": ISO time, "p": [x, y, z]}, ...]; places.json as for hazards.py, plus optional
"events": [{"t", "type", "place"}].
"""
import json
import sys
from datetime import datetime

import numpy as np

from hazards import split_up

BED_M = 0.8       # farther than this from the bed counts as up
STOP_M = 0.5      # staying within this radius ...
STOP_S = 60       # ... for this long is a stop
PLACE_M = 1.2     # a stop this close to a named place is "at" it
MAX_POINTS = 1500


def when(t):
    return datetime.fromisoformat(t)


def analyse(track, places, events=(), up="-y", day=None, base="bed"):
    """The night from a time-ordered track. Returns the dict the dashboard reads (see module doc).
    base: the place trips start from and end at ("bed" at home; a desk where there is no bed)."""
    base = base.lower()
    track = sorted(track, key=lambda f: when(f["t"]))
    if not track:
        return {"day": day, "up": up, "base": base, "points": [], "trips": [], "stops": [], "events": list(events),
                "summary": {"trips": 0, "minutes_up": 0, "metres": 0, "places": []}}
    p = np.array([f["p"] for f in track], float)
    t = np.array([when(f["t"]).timestamp() for f in track])
    flat, _ = split_up(p, up)
    names = {k.lower(): split_up(np.asarray(v, float)[None], up)[0][0] for k, v in places.items()}
    bed = names.get(base)

    def place_at(xy):
        if not names:
            return None
        name = min(names, key=lambda k: np.linalg.norm(names[k] - xy))
        return name if np.linalg.norm(names[name] - xy) <= PLACE_M else None

    # Stops: a run of fixes that stays within STOP_M of where it started, long enough.
    stops, i = [], 0
    while i < len(t):
        j = i
        while j + 1 < len(t) and np.linalg.norm(flat[j + 1] - flat[i]) <= STOP_M:
            j += 1
        if t[j] - t[i] >= STOP_S:
            centre = flat[i:j + 1].mean(0)
            stops.append({"t": track[i]["t"], "until": track[j]["t"], "minutes": round((t[j] - t[i]) / 60, 1),
                          "p": [round(float(v), 3) for v in p[i:j + 1].mean(0)], "place": place_at(centre)})
            i = j + 1
        else:
            i += 1

    # Trips: out of bed and back. Without a bed place, the whole track is one trip.
    up_now = np.ones(len(t), bool) if bed is None else np.linalg.norm(flat - bed, axis=1) > BED_M
    trips, k = [], 0
    while k < len(t):
        if not up_now[k]:
            k += 1; continue
        e = k
        while e + 1 < len(t) and up_now[e + 1]:
            e += 1
        start, end = track[max(k - 1, 0)]["t"], track[min(e + 1, len(t) - 1)]["t"]
        visited = [s["place"] for s in stops if s["place"] and s["place"] != base and when(start) <= when(s["t"]) <= when(end)]
        trips.append({"start": start, "end": end, "minutes": round((when(end) - when(start)).total_seconds() / 60, 1),
                      "visits": list(dict.fromkeys(visited))})
        k = e + 1

    metres = float(np.linalg.norm(np.diff(flat, axis=0), axis=1).sum())
    keep = np.unique(np.linspace(0, len(track) - 1, min(len(track), MAX_POINTS)).round().astype(int))
    summary = {"trips": len(trips), "minutes_up": round(sum(x["minutes"] for x in trips)),
               "metres": round(metres), "places": list(dict.fromkeys(v for x in trips for v in x["visits"]))}
    return {"day": day, "up": up, "base": base, "from": track[0]["t"], "to": track[-1]["t"],
            "points": [{"t": track[n]["t"], "p": [round(float(v), 3) for v in p[n]]} for n in keep],
            "trips": trips, "stops": stops, "events": list(events), "summary": summary}


def main(argv):
    track = json.load(open(argv[1]))
    spec = json.load(open(argv[2]))
    out = analyse(track, spec["places"], spec.get("events", []), argv[4] if len(argv) > 4 else "-y")
    json.dump(out, open(argv[3], "w"), indent=1)
    print(argv[3], out["summary"])


if __name__ == "__main__":
    main(sys.argv)
