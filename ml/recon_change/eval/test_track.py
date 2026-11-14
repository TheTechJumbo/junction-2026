#!/usr/bin/env python3
"""Offline test of track.py: the steady position made from a stream of position fixes.

No cluster, no data, no numpy: made-up fixes with known answers.

    python eval/test_track.py
"""
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import track  # noqa: E402

PLACES = [{"name": "pill box", "position": [0.0, 2.0, 0.0]}, {"name": "front door", "position": [3.0, 0.0, 0.0]}]


def fix(x=0.0, y=0.0, heading=0.0, state="found", score=0.9, zone="kitchen"):
    return {"zone": zone, "state": state, "confidence": score, "position": [x, y, 0.0], "heading_deg": heading,
            "waypoints": [dict(p, distance_m=0.0, bearing_deg=0) for p in PLACES]}


def lost():
    return fix(4.0, -3.0, 140.0, state="lost", score=0.05)


def steady_tracker(t=0.0, step=1.0, n=3):
    tr = track.Tracker(zone="kitchen")
    for i in range(n):
        out = tr.update(fix(), t + i * step)
    assert out["state"] == "steady" and out["show"], out
    return tr, t + (n - 1) * step


def main():
    # The shape of an answer, and the way to each place recomputed from the steady pose.
    tr = track.Tracker(zone="kitchen")
    out = tr.update(fix(0.0, 0.0, 90.0), 0.0)
    assert set(out) >= {"state", "position", "heading_deg", "confidence_pct", "show", "agree", "of", "age_s", "waypoints"}, out
    assert out["state"] == "settling" and not out["show"], "one fix alone is not steady yet"
    ways = {w["name"]: w for w in out["waypoints"]}
    assert ways["pill box"]["distance_m"] == 2.0 and ways["pill box"]["bearing_deg"] == -90, ways  # facing east, it is to the left
    assert ways["front door"]["distance_m"] == 3.0 and ways["front door"]["bearing_deg"] == 0, ways
    assert tr.update(fix(0.0, 0.0, 90.0), 1.0)["state"] == "steady"

    # Smoothing: fixes that jitter around one pose give an answer that jitters less.
    rng = random.Random(1)
    tr, raw, smooth = track.Tracker(zone="kitchen"), [], []
    for i in range(60):
        x, y, h = rng.gauss(1.0, 0.05), rng.gauss(2.0, 0.05), rng.gauss(30.0, 3.0)
        out = tr.update(fix(x, y, h), i * 1.0)
        if i >= 5:
            raw.append((math.hypot(x - 1.0, y - 2.0), abs(h - 30.0)))
            smooth.append((math.hypot(out["position"][0] - 1.0, out["position"][1] - 2.0), abs(out["heading_deg"] - 30.0)))
    mean = lambda rows, k: sum(r[k] for r in rows) / len(rows)  # noqa: E731
    assert mean(smooth, 0) < 0.9 * mean(raw, 0) and mean(smooth, 1) < 0.9 * mean(raw, 1), (mean(raw, 0), mean(smooth, 0), mean(raw, 1), mean(smooth, 1))
    assert out["state"] == "steady" and out["agree"] == track.KEEP

    # One outlier: a found fix six metres away one second later does not move the answer. The next
    # fix is back where the wearer was, and the outlier is forgotten.
    tr, t = steady_tracker()
    out = tr.update(fix(6.0, 0.0, 170.0), t + 1)
    assert out["position"][:2] == [0.0, 0.0] and out["heading_deg"] == 0 and out["waiting"], out
    assert out["state"] == "steady", "one odd fix must not drop the answer"
    out = tr.update(fix(0.02, 0.0, 1.0), t + 2)
    assert math.hypot(*out["position"][:2]) < 0.05 and not out["waiting"] and out["state"] == "steady", out

    # A real move, walking: 0.6 m/s with a fix every second. The answer is on the newest fix every time.
    tr, t = steady_tracker()
    for i in range(1, 8):
        out = tr.update(fix(0.0, 0.6 * i, 0.0), t + i)
        assert abs(out["position"][1] - 0.6 * i) < 0.01 and out["state"] == "steady", (i, out)
    # A real turn of 90 degrees between two fixes six seconds apart is followed at once.
    out = tr.update(fix(0.0, 4.2, 90.0), t + 13)
    assert out["heading_deg"] == 90, out
    # A jump no person makes (5 m in a second) that is real, say after the tracker held a wrong pose:
    # the first fix there waits, the second one that agrees moves the answer. That is the delay: one fix.
    out = tr.update(fix(5.0, 4.2, 90.0), t + 14)
    assert out["position"][0] == 0.0 and out["waiting"], out
    out = tr.update(fix(5.1, 4.2, 92.0), t + 15)
    assert abs(out["position"][0] - 5.1) < 0.01 and out["state"] == "steady" and not out["waiting"], out

    # Lost fixes: one among good ones changes nothing but the confidence; LOST_AFTER in a row drop the answer.
    tr, t = steady_tracker()
    before = tr.current(t)
    out = tr.update(lost(), t + 1)
    assert out["state"] == "steady" and out["position"] == before["position"], out
    assert out["confidence_pct"] < before["confidence_pct"], (before, out)
    out = tr.update(fix(0.0, 0.0, 0.0), t + 2)
    assert out["state"] == "steady" and out["show"], "back to a good fix: still steady"
    for i in range(track.LOST_AFTER):
        assert out["state"] == "steady"
        out = tr.update(lost(), t + 3 + i)
    assert out["state"] == "lost" and out["position"] is None and not out["show"] and out["confidence_pct"] == 0, out
    assert [w["name"] for w in out["waypoints"]] == ["pill box", "front door"] and out["waypoints"][0]["distance_m"] is None

    # Recovery needs more than one good fix: the first gives a pose but is "settling", no arrow yet.
    out = tr.update(fix(1.0, 1.0, 45.0), t + 10)
    assert out["state"] == "settling" and out["position"][:2] == [1.0, 1.0] and not out["show"], out
    out = tr.update(fix(1.0, 1.05, 45.0), t + 11)
    assert out["state"] == "steady", out
    # An unsure fix cannot start an answer, and an unsure fix somewhere else is a miss, not a move.
    fresh = track.Tracker(zone="kitchen")
    assert fresh.update(fix(state="unsure", score=0.4), 0.0)["state"] == "lost"
    out = tr.update(fix(2.5, 1.0, 45.0, state="unsure", score=0.4), t + 12)
    assert out["position"][:2] == [1.0, 1.02] or abs(out["position"][0] - 1.0) < 0.01, out
    # An unsure fix that shows the same pose keeps the answer alive.
    out = tr.update(fix(1.05, 1.0, 47.0, state="unsure", score=0.4), t + 13)
    assert out["state"] == "steady" and out["age_s"] == 0.0, out

    # No photos: the pose never moves, the confidence only falls, and after SILENCE_S the answer is dropped.
    tr, t = steady_tracker()
    last = tr.current(t)
    for age in (1, 3, 6, 12, 20, track.SILENCE_S - 1):
        out = tr.current(t + age)
        assert out["position"] == last["position"] and out["heading_deg"] == last["heading_deg"], "no photo, no motion"
        assert out["confidence_pct"] <= last["confidence_pct"] and out["age_s"] == age, (age, out)
        last = out
    assert last["confidence_pct"] < tr.current(t)["confidence_pct"]
    assert tr.current(t + track.SILENCE_S + 1)["state"] == "lost"
    assert tr.dump()["position"] is not None, "current() only looks: it must not change the state"

    # Another room. A fix marked for another room is not heard at all; a photo of another room sent as
    # this room comes back lost (0 of 178 accepted in eval/LOCATE_WALK_RESULTS.md) and moves nothing;
    # and if one were ever called found, far from the wearer, it still could not move the answer alone.
    tr, t = steady_tracker()
    before = tr.dump()
    tr.update(fix(3.0, 3.0, 90.0, zone="bedroom"), t + 1)
    assert tr.dump() == before
    for i in range(2):
        out = tr.update(fix(3.0 + i, 3.0, 90.0, state="lost", score=0.1), t + 1.5 + i / 2)
        assert out["position"][:2] == [0.0, 0.0] and out["heading_deg"] == 0, out
    tr.update(fix(), t + 3)
    out = tr.update(fix(7.0, 5.0, -120.0, score=0.55), t + 4)
    assert out["position"][:2] == [0.0, 0.0] and out["heading_deg"] == 0, out

    # The state survives a save and a load: the same fixes then give the same answers.
    tr, t = steady_tracker()
    tr.update(fix(0.3, 0.2, 10.0), t + 1)
    tr.update(lost(), t + 2)
    tr.update(fix(6.0, 0.0, 0.0), t + 3)  # leaves a fix waiting
    copy = track.Tracker(json.loads(json.dumps(tr.dump())))
    assert copy.current(t + 4) == tr.current(t + 4)
    for i, f in enumerate([fix(6.1, 0.0, 0.0), lost(), fix(6.2, 0.1, 5.0)]):
        assert copy.update(f, t + 5 + i) == tr.update(f, t + 5 + i)
    assert copy.dump() == tr.dump()

    # One fix on its own: a percentage from 0 to 100 that rises with the match score.
    scores = [track.fix_confidence_pct({"position": [0, 0, 0], "confidence": c}) for c in (0.0, 0.2, 0.4, 0.6, 0.9)]
    assert scores == sorted(scores) and 0 <= scores[0] < scores[-1] <= 100, scores
    assert track.fix_confidence_pct({"position": None, "confidence": 0.9}) == 0

    # A place the carer removed disappears; a new one appears.
    tr, t = steady_tracker()
    f = fix()
    f["waypoints"] = [{"name": "kettle", "position": [1.0, 1.0, 0.0]}]
    out = tr.update(f, t + 1)
    assert [w["name"] for w in out["waypoints"]] == ["kettle"], out
    print("track: all checks passed")


if __name__ == "__main__":
    main()
