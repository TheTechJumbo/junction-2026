"""locate() itself on reconstructions saved again (a new run of the model on the same frames)
against the replay on the walking test's saved fixes. Usage: compare.py REPLAY.jsonl ROWS.jsonl"""
import json
import sys
from collections import Counter

import numpy as np

replay = {tuple(f["key"]): f for f in (json.loads(line) for line in open(sys.argv[1]))}
rows = [json.loads(line) for line in open(sys.argv[2]) if line.strip()]
rows = [r for r in rows if "error" not in r]
n = same_state = pairs = d_ok = b_ok = up_ok = hint_ok = 0
gap, tilt, secs = [], [], []
moved = Counter()
for r in rows:
    f = replay.get(tuple(r["key"]))
    if f is None:
        continue
    v = f["v"][3]
    n += 1
    same_state += v["state"] == r["state"]
    if v["state"] != r["state"]:
        moved[(r["kind"], v["state"], r["state"])] += 1
    up_ok += v["up_from"] == r["up_from"]
    tilt.append(abs(v["up_tilt_deg"] - r["up_tilt_deg"]))
    gap.append(abs(v["view_gap_deg"] - r["view_gap_deg"]))
    hint_ok += (v["hint"] or {}).get("say") == (r["hint"] or {}).get("say")
    secs.append(r["locate_s"])
    if r["kind"] != "same":
        continue
    theirs = {w["name"]: w for w in v["waypoints"]}
    for w in r["waypoints"]:
        pairs += 1
        d_ok += abs(w["distance_m"] - theirs[w["name"]]["distance_m"]) <= 0.1001
        b_ok += abs((w["bearing_deg"] - theirs[w["name"]]["bearing_deg"] + 180) % 360 - 180) <= 2
kinds = Counter(r["kind"] for r in rows)
print(f"{n} fixes ({kinds['same']} same room, {kinds['wrong']} wrong room): same state {same_state}/{n}; moved {dict(moved)}")
print(f"up from the same source {up_ok}/{n}; surface normal against the cameras differs by median {np.median(tilt):.2f} deg, max {max(tilt):.2f}")
print(f"view gap differs by median {np.median(gap):.2f} deg, max {max(gap):.1f}; same hint (or none) {hint_ok}/{n}")
print(f"fix-place pairs {pairs}: distance within 0.1 m {d_ok}/{pairs}, bearing within 2 deg {b_ok}/{pairs}")
print(f"metres_from: {dict(Counter(r['metres_from'] for r in rows))}; up_from: {dict(Counter(r['up_from'] for r in rows))}")
print(f"states now: same room {dict(Counter(r['state'] for r in rows if r['kind'] == 'same'))}, wrong room {dict(Counter(r['state'] for r in rows if r['kind'] == 'wrong'))}; "
      f"one_frame_only {sum(r['one_frame_only'] for r in rows)}; hints: same {sum(bool(r['hint']) for r in rows if r['kind'] == 'same')}, wrong {sum(bool(r['hint']) for r in rows if r['kind'] == 'wrong')}")
print(f"locate() seconds on the shared login node, median {np.median(secs):.2f}")
