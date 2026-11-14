#!/usr/bin/env python3
"""Version 2 of the position fix, measured on the walking test's fixes (TUM RGB-D). No GPU.

Two ways to get the numbers, both end in the same tables:

  replay (default)   The walking test kept, for each of its 938 fixes, the cameras, the places'
                     positions, the parts of the match score and a thinned copy of the baseline's
                     points (results.jsonl, fix/*.npz). The new parts of locate.py are plain
                     functions of exactly those (surface_up, known_metres, state_of, view_gap,
                     hint, coverage), so this script calls them on the saved fixes. The only
                     difference from running locate() is that the saved place positions are
                     rounded to 1 cm.
  --rows FILE ...    Rows written by `locate_walk_eval.py --from-dump` with the new locate.py:
                     locate() itself on saved reconstructions. Used to confirm the replay.

    python locate_v2_eval.py --plan ../walk/out/plan.json --walk ../walk/out --out out_v2
"""
import argparse
import json
import sys
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
UP = np.array([0.0, 0.0, 1.0])
RIGHT_M, RIGHT_DEG = 0.2, 10.0  # a fix is "right" within this, as in the walking test
# The settings compared. Each adds one change to the one before; "v2" is what runs after this change
# without a scale.json, "v2 + known distance" with one.
VARIANTS = [
    ("as shipped (up from the cameras, 2.0 m assumed)", {"up": "cameras", "typical_m": 2.0, "known": False}),
    ("assumed viewing distance 1.5 m", {"up": "cameras", "typical_m": 1.5, "known": False}),
    ("v2: 1.5 m and up from a level surface", {"up": "surface", "typical_m": 1.5, "known": False}),
    ("v2 + one known distance", {"up": "surface", "typical_m": 1.5, "known": True}),
]
PLAN = {}


def similarity(src, dst):
    """Least-squares scale s, rotation R and shift t with dst ~ s * R @ src + t (Umeyama)."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    a, b = src - mu_s, dst - mu_d
    U, S, Vt = np.linalg.svd(b.T @ a / len(src))
    D = np.eye(3)
    D[2, 2] = np.sign(np.linalg.det(U @ Vt)) or 1.0
    R = U @ D @ Vt
    scale = float((S * np.diag(D)).sum() / ((a ** 2).sum() / len(src)))
    return scale, R, mu_d - scale * R @ mu_s


def wrap(deg):
    return (deg + 180) % 360 - 180


def farthest_pair(places):
    return max(((a, b) for i, a in enumerate(places) for b in places[i + 1:]),
               key=lambda ab: np.linalg.norm(np.array(ab[0]["world"]) - np.array(ab[1]["world"])))


def one_frame_score(row):
    """locate()'s score when one baseline frame is enough, from the saved parts (views_shares in
    locate_walk_eval.py): [depth 1, depth 2, alike 1, alike 2, share of trusted pixels]."""
    def share(v):
        return v[2] * min(1.0, v[4] / max(row["trusted"], 1e-6))
    usual = float(np.median([share(v) for v in row["views_probes"]])) or 1.0
    return min(1.0, share(row["views_query"]) / usual)


def replay(row, cloud, places, setting, pair=None, memo=None):
    """What locate() returns for a saved fix under `setting`, from what the walking test kept."""
    memo = {} if memo is None else memo
    import locate

    E = np.array(row["E"])
    base, q = list(range(row["n_baseline"])), len(E) - 1
    unit = row["median_baseline_depth"]
    cam_up, north, east = locate.axes(E, base)
    old = 2.0 / unit  # the metres the saved positions are in
    origin = locate.camera(E[0])[0]
    spots = {w["name"]: origin + (east * w["position"][0] + north * w["position"][1] + cam_up * w["position"][2]) / old for w in row["waypoints"]}
    up, how = cam_up, {"from": "cameras", "tilt_deg": 0.0, "share": 0.0}
    if setting["up"] == "surface":
        if "up" not in memo:  # the same for every setting of one fix
            memo["up"] = locate.surface_up(cloud, cam_up, locate.UP_TOL * unit)
        up, how = memo["up"]
        north, east = locate.level_axes(E[0][2, :3], up, north)
    known = None
    if setting["known"] and len(places) >= 2:
        a, b = pair or farthest_pair(places)
        known = {"a": a["name"], "b": b["name"], "metres": float(np.linalg.norm(np.array(a["world"]) - np.array(b["world"])))}
    metres, metres_from, note = locate.known_metres(spots, known, setting["typical_m"] / unit)
    room = {"origin": origin, "east": east, "north": north, "up": up, "metres": metres}
    position, forward = locate.camera(E[q])
    here = locate.to_map(room, position)[0]
    heading = float(np.degrees(np.arctan2(forward @ east, forward @ north)))
    ways = []
    for name, world in spots.items():
        at = locate.to_map(room, world)[0]
        e, n = at[0] - here[0], at[1] - here[1]
        ways.append({"name": name, "distance_m": round(float(np.hypot(e, n)), 1),
                     "bearing_deg": int(round(float((np.degrees(np.arctan2(e, n)) - heading + 180) % 360 - 180)))})
    one = one_frame_score(row) if "views_query" in row else 0.0
    state = row["state"] if row["state"] != "lost" else locate.state_of(row["confidence"], one, True)
    gap, turn, tilt, frame = locate.view_gap(E, base, q, room)
    return {"state": state, "match_one_frame": one, "waypoints": ways, "metres": metres, "metres_from": metres_from, "metres_note": note,
            "heading_deg": heading,
            "up": up.tolist(), "up_from": how["from"], "up_tilt_deg": how["tilt_deg"], "up_surface_share": how["share"],
            "view_gap_deg": gap, "turn_deg": turn, "tilt_deg": tilt, "nearest_frame": frame,
            "hint": None if state == "found" else locate.hint(gap, turn, tilt),
            "coverage": locate.coverage(E, base, room)}


def truth(row, plan):
    """The true side of one same-room fix: errors that do not depend on up or metres, and what the
    display should have shown."""
    import locate

    room = plan["rooms"][row["room"]]
    q = room["queries"][row["query"]]
    E = np.array(row["E"])
    nb = row["n_baseline"]
    P = np.array([locate.camera(e)[0] for e in E])
    G = np.array([room["baseline"][i]["C"] for i in row["pick"]])
    s, R, t = similarity(P[:nb], G)
    qC, qR = np.array(q["C"]), np.array(q["R"])
    true_fwd = qR[:, 2]
    fh = true_fwd - (true_fwd @ UP) * UP
    fh /= np.linalg.norm(fh)
    right = np.cross(fh, UP)
    ways = {}
    for p in room["places"]:
        d = np.array(p["world"]) - qC
        ways[p["name"]] = (float(np.hypot(d @ right, d @ fh)), float(np.degrees(np.arctan2(d @ right, d @ fh))))
    views = [np.array(room["baseline"][i]["R"])[:, 2] for i in row["pick"]]
    gaps = [float(np.degrees(np.arccos(np.clip(true_fwd @ v, -1, 1)))) for v in views]

    def heading(v):
        return float(np.degrees(np.arctan2(v[0], v[1])))  # clockwise seen from above, like the map's

    return {"s": s, "R": R, "pos_err": float(np.linalg.norm(s * R @ P[-1] + t - qC)),
            "head_err": float(np.degrees(np.arccos(np.clip((R @ E[-1][2, :3]) @ true_fwd, -1, 1)))),
            "ways": ways, "view_gap": min(gaps), "turns": [wrap(heading(v) - heading(true_fwd)) for v in views],
            "source": q["source"], "turn_speed": q["turn"]}


def work(job):
    """One saved fix under every setting."""
    row, walk = job
    plan = PLAN["plan"]
    places = plan["rooms"][row["room"]]["places"]
    file = Path(walk) / "fix" / ("_".join(str(v) for v in row["key"]) + ".npz")
    cloud = np.load(file)["cloud"].astype(np.float64) if file.exists() else np.zeros((0, 3))
    memo = {}
    out = {"key": row["key"], "v": [replay(row, cloud, places, setting, memo=memo) for _, setting in VARIANTS]}
    if row["kind"] == "same":
        t = truth(row, plan)
        for v in out["v"]:
            v["up_world"] = (t["R"] @ np.array(v["up"])).tolist()  # the fix's up in the ground-truth frame
            v["up_off_deg"] = float(np.degrees(np.arccos(np.clip(np.array(v["up_world"]) @ UP, -1, 1))))
            v["ratio"] = v["metres"] / t["s"]
        # The known distance from every pair of places, not only the two farthest apart.
        out["pairs"] = []
        for i, a in enumerate(places):
            for b in places[i + 1:]:
                got = replay(row, cloud[:0], places, {"up": "cameras", "typical_m": 1.5, "known": True}, (a, b))
                out["pairs"].append({"a": a["name"], "b": b["name"], "true_m": float(np.linalg.norm(np.array(a["world"]) - np.array(b["world"]))),
                                     "ratio": got["metres"] / t["s"], "used": got["metres_from"] == "known distance"})
        t.pop("R")
        out["t"] = t
    for v in out["v"]:
        v.pop("up")
    return out


# ----------------------------------------------------------------------------- tables
def pct(values, q):
    values = [v for v in values if v is not None and np.isfinite(v)]
    return float(np.percentile(values, q)) if values else float("nan")


def three(values, digits=2):
    return " / ".join(f"{f:.{digits}f}" for f in (pct(values, 50), pct(values, 90), pct(values, 100))) if len(values) else "-"


def share(k, n):
    return f"{k}/{n} ({100 * k / n:.0f}%)" if n else "-"


def shown(fixes, i, far=0.0):
    """Distance and bearing errors of what variant i shows, over every fix-place pair."""
    d_abs, d_rel, b_abs = [], [], []
    for f in fixes:
        for w in f["v"][i]["waypoints"]:
            true_h, true_b = f["t"]["ways"][w["name"]]
            d_abs.append(abs(w["distance_m"] - true_h))
            d_rel.append(100 * abs(w["distance_m"] - true_h) / max(true_h, 1e-6))
            if true_h >= max(far, 0.5):
                b_abs.append(abs(wrap(w["bearing_deg"] - true_b)))
    return d_abs, d_rel, b_abs


def tables(fixes, rows, plan):
    L = []
    rooms = list(plan["rooms"])
    by_key = {tuple(r["key"]): r for r in rows}
    for f in fixes:
        f["row"] = by_key[tuple(f["key"])]
    plain = [f for f in fixes if f["key"][1] == 12 and f["key"][2] == 1]
    same = [f for f in plain if f["row"]["kind"] == "same"]
    wrong = [f for f in plain if f["row"]["kind"] == "wrong"]
    groups = [(room, [f for f in same if f["row"]["room"] == room], [f for f in wrong if f["row"]["room"] == room]) for room in rooms]
    groups.append(("**All rooms**", same, wrong))

    def right(f):
        return f["t"]["pos_err"] <= RIGHT_M and f["t"]["head_err"] <= RIGHT_DEG

    L.append(f"Fixes replayed: {len(plain)} with 12 baseline frames and 1 query frame ({len(same)} same room, {len(wrong)} wrong room).\n")
    # Does the replay reproduce what locate() returned at the time?
    n = hit = 0
    for f in same:
        saved = {w["name"]: w for w in f["row"]["waypoints"]}
        for w in f["v"][0]["waypoints"]:
            n += 1
            hit += w["distance_m"] == saved[w["name"]]["distance_m"] and abs(wrap(w["bearing_deg"] - saved[w["name"]]["bearing_deg"])) <= 1
    L.append(f"Check of the replay: with the shipped settings it gives the distance (to 0.1 m) and bearing (within 1 degree) that `locate()` "
             f"returned at the time for {share(hit, n)} of the fix-place pairs.\n")

    L.append("### A. Found, unsure, lost\n")
    L.append("\"Found\" is unchanged (two baseline frames must agree). New: a view that reaches the found mark only when one baseline "
             "frame is enough is \"unsure\" instead of \"lost\".\n")
    L.append("| Room | Same-room queries | Found (before = after) | Unsure before | Unsure after | Lost before | Lost after | Rescued (lost to unsure) | "
             "of those: right position (0.2 m, 10 deg) | of those: poor | Wrong room accepted before | after | Wrong room: highest one-frame score |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for name, s_, w_ in groups:
        before, after = Counter(f["row"]["state"] for f in s_), Counter(f["v"][2]["state"] for f in s_)
        rescued = [f for f in s_ if f["row"]["state"] == "lost" and f["v"][2]["state"] == "unsure"]
        L.append(f"| {name} | {len(s_)} | {share(before['found'], len(s_))} | {before['unsure']} | {after['unsure']} | {before['lost']} | {after['lost']} | "
                 f"{len(rescued)} | {sum(right(f) for f in rescued)} | {sum(not right(f) for f in rescued)} | "
                 f"{share(sum(f['row']['state'] != 'lost' for f in w_), len(w_))} | {share(sum(f['v'][2]['state'] != 'lost' for f in w_), len(w_))} | "
                 f"{max([f['v'][2]['match_one_frame'] for f in w_], default=0):.2f} |")
    L.append("")
    L.append("How good is a fix in each state, after the change (position error m and heading error degrees: median / 90th percentile / worst):\n")
    L.append("| State after | Fixes | Position error m | Heading error deg | Right position (0.2 m, 10 deg) | Off by more than 0.5 m or 20 deg |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    for label, pick in (("found", lambda f: f["v"][2]["state"] == "found"),
                        ("unsure, as before (two-frame score 0.3 to 0.5)", lambda f: f["row"]["state"] == "unsure"),
                        ("unsure, new (one baseline frame agrees)", lambda f: f["row"]["state"] == "lost" and f["v"][2]["state"] == "unsure"),
                        ("lost", lambda f: f["v"][2]["state"] == "lost")):
        g = [f for f in same if pick(f)]
        L.append(f"| {label} | {len(g)} | {three([f['t']['pos_err'] for f in g])} | {three([f['t']['head_err'] for f in g], 1)} | "
                 f"{share(sum(right(f) for f in g), len(g))} | {share(sum(f['t']['pos_err'] > 0.5 or f['t']['head_err'] > 20 for f in g), len(g))} |")
    L.append("")
    poor = sorted([f for f in same if f["row"]["state"] == "lost" and f["v"][2]["state"] == "unsure" and not right(f)], key=lambda f: -f["t"]["pos_err"])
    if poor:
        L.append("The poor fixes that now come through as \"unsure\": " + "; ".join(
            f"{f['row']['room']} {f['t']['pos_err']:.2f} m, {f['t']['head_err']:.1f} deg (true angle to the closest baseline view {f['t']['view_gap']:.0f} deg)"
            for f in poor) + ".\n")

    L.append("### B. What the display shows (found fixes)\n")
    L.append("Distance and bearing to each place as the fix returns them, against the truth. Position and heading error of the fix do not "
             "depend on metres or up and are the same in every row. Bearing rows count places at least 0.5 m away.\n")
    L.append("| Room | Setting | Found fixes | Metres / true metres: median (10th to 90th pct) | Fixes with metres within 15% | "
             "Distance error m: median / 90th / worst | Distance error %, median | Bearing error deg: median / 90th / worst | Bearing off by more than 30 deg | "
             "Up off the true vertical deg: median / 90th / worst | Up taken from a surface |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for name, s_, _ in groups:
        g = [f for f in s_ if f["row"]["state"] == "found"]
        for i, (label, _) in enumerate(VARIANTS):
            d_abs, d_rel, b_abs = shown(g, i)
            ratio = [f["v"][i]["ratio"] for f in g]
            L.append(f"| {name} | {label} | {len(g)} | {pct(ratio, 50):.2f} ({pct(ratio, 10):.2f} to {pct(ratio, 90):.2f}) | "
                     f"{share(sum(abs(r - 1) <= 0.15 for r in ratio), len(g))} | {three(d_abs)} | {pct(d_rel, 50):.0f} | {three(b_abs, 0)} | "
                     f"{share(sum(b > 30 for b in b_abs), len(b_abs))} | {three([f['v'][i]['up_off_deg'] for f in g], 1)} | "
                     f"{share(sum(f['v'][i]['up_from'] == 'surface' for f in g), len(g))} |")
    L.append("")
    L.append("The same for every same-room fix that shows an arrow after the change (found and unsure), v2 settings:\n")
    L.append("| Room | Fixes | Position error m: median / 90th / worst | Heading error deg | Distance error m (v2) | (v2 + known distance) | Bearing error deg (v2) |")
    L.append("| --- | --- | --- | --- | --- | --- | --- |")
    for name, s_, _ in groups:
        g = [f for f in s_ if f["v"][2]["state"] != "lost"]
        L.append(f"| {name} | {len(g)} | {three([f['t']['pos_err'] for f in g])} | {three([f['t']['head_err'] for f in g], 1)} | {three(shown(g, 2)[0])} | "
                 f"{three(shown(g, 3)[0])} | {three(shown(g, 2)[2], 0)} |")
    L.append("")

    L.append("### C. Up\n")
    L.append("| Room | Same-room fixes | Up from the cameras, off the true vertical deg: median / 90th / worst | Up from a level surface | Surface used | "
             "Share of the baseline's points on that surface, median | Surface normal against the cameras' average deg, median / worst | "
             "How steady the up is from fix to fix: angle to the room's mean up deg, 90th pct / worst, cameras | surface |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")

    def steady(s_, i):
        off = []
        for room in rooms:
            ups = np.array([f["v"][i]["up_world"] for f in s_ if f["row"]["room"] == room])
            if len(ups):
                mean = ups.mean(0) / np.linalg.norm(ups.mean(0))
                off += np.degrees(np.arccos(np.clip(ups @ mean, -1, 1))).tolist()
        return f"{pct(off, 90):.1f} / {pct(off, 100):.1f}"

    for name, s_, _ in groups:
        used = [f for f in s_ if f["v"][2]["up_from"] == "surface"]
        L.append(f"| {name} | {len(s_)} | {three([f['v'][0]['up_off_deg'] for f in s_], 1)} | {three([f['v'][2]['up_off_deg'] for f in s_], 1)} | "
                 f"{share(len(used), len(s_))} | {pct([f['v'][2]['up_surface_share'] for f in s_], 50):.2f} | "
                 f"{pct([f['v'][2]['up_tilt_deg'] for f in s_], 50):.1f} / {pct([f['v'][2]['up_tilt_deg'] for f in s_], 100):.1f} | "
                 f"{steady(s_, 0)} | {steady(s_, 2)} |")
    L.append("")
    L.append("Wrong-room fixes (the map is the room's own, the query is from elsewhere): up from a surface in "
             f"{share(sum(f['v'][2]['up_from'] == 'surface' for f in wrong), len(wrong))}.\n")

    L.append("### D. One known distance\n")
    L.append("Every pair of the five places as the known distance (found fixes; the table in B uses the two places farthest apart).\n")
    L.append("| Room | Pair | True distance m | Metres / true metres: median (10th to 90th pct) | Fixes within 15% | Fixes within 5% |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    every = []
    for name, s_, _ in groups[:-1]:
        g = [f for f in s_ if f["row"]["state"] == "found"]
        for j in range(len(g[0]["pairs"]) if g else 0):
            p = g[0]["pairs"][j]
            ratio = [f["pairs"][j]["ratio"] for f in g]
            every.append((p["true_m"], ratio))
            L.append(f"| {name} | {p['a']} to {p['b']} | {p['true_m']:.2f} | {pct(ratio, 50):.2f} ({pct(ratio, 10):.2f} to {pct(ratio, 90):.2f}) | "
                     f"{share(sum(abs(r - 1) <= 0.15 for r in ratio), len(ratio))} | {share(sum(abs(r - 1) <= 0.05 for r in ratio), len(ratio))} |")
    L.append("")
    for label, pick in (("under 1 m", lambda d: d < 1.0), ("1 to 2 m", lambda d: 1.0 <= d < 2.0), ("2 m and more", lambda d: d >= 2.0)):
        r = [x for d, ratio in every if pick(d) for x in ratio]
        if r:
            L.append(f"- Known distance {label}: {sum(1 for d, _ in every if pick(d))} pairs, metres within 15% in {share(sum(abs(x - 1) <= 0.15 for x in r), len(r))} "
                     f"of the fixes, within 5% in {share(sum(abs(x - 1) <= 0.05 for x in r), len(r))}, worst {max(abs(x - 1) for x in r) * 100:.0f}% off.")
    L.append("")

    L.append("### E. The hint: looking where the baseline did not look\n")
    L.append("`view_gap_deg` is the angle between the wearer's view and the closest baseline view, from the model's own cameras (no truth). "
             f"A fix that is not found gets a hint when it is above {30} degrees.\n")
    L.append("| Model's angle to the closest baseline view | Same-room queries | Found | Unsure (after) | Lost (after) | Position error m: median / worst |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    for label, lo, hi in (("under 15", 0, 15), ("15 to 30", 15, 30), ("30 to 50", 30, 50), ("50 and more", 50, 999)):
        g = [f for f in same if lo <= f["v"][2]["view_gap_deg"] < hi]
        c = Counter(f["v"][2]["state"] for f in g)
        L.append(f"| {label} | {len(g)} | {share(c['found'], len(g))} | {c['unsure']} | {c['lost']} | "
                 f"{pct([f['t']['pos_err'] for f in g], 50):.2f} / {pct([f['t']['pos_err'] for f in g], 100):.2f} |")
    L.append("")
    far = [f for f in same if f["v"][2]["view_gap_deg"] > 30]
    near = [f for f in same if f["v"][2]["view_gap_deg"] <= 30]
    not_found = [f for f in same if f["v"][2]["state"] != "found"]
    lost = [f for f in same if f["v"][2]["state"] == "lost"]
    hinted = [f for f in same if f["v"][2]["hint"]]
    L.append(f"- As a predictor: beyond 30 degrees {share(sum(f['v'][2]['state'] != 'found' for f in far), len(far))} are not found; within 30 degrees "
             f"{share(sum(f['v'][2]['state'] != 'found' for f in near), len(near))} are not found.")
    L.append(f"- Of the {len(lost)} same-room fixes that are lost after the change, {share(sum(bool(f['v'][2]['hint']) for f in lost), len(lost))} carry a hint; "
             f"of all {len(not_found)} that are not found, {share(len(hinted), len(not_found))}.")
    L.append(f"- The model's angle against the true one: median difference {pct([abs(f['v'][2]['view_gap_deg'] - f['t']['view_gap']) for f in same], 50):.1f} deg, "
             f"90th percentile {pct([abs(f['v'][2]['view_gap_deg'] - f['t']['view_gap']) for f in same], 90):.1f}. "
             f"Of the {len(hinted)} hints, {share(sum(f['t']['view_gap'] > 30 for f in hinted), len(hinted))} are for views truly more than 30 degrees away, "
             f"{share(sum(f['t']['view_gap'] > 20 for f in hinted), len(hinted))} more than 20. Of the "
             f"{sum(f['t']['view_gap'] > 30 for f in not_found)} not-found views truly beyond 30 degrees, "
             f"{sum(bool(f['v'][2]['hint']) for f in not_found if f['t']['view_gap'] > 30)} got a hint.")
    turns = [(f["v"][2]["hint"]["turn_deg"], f["t"]["turns"][f["v"][2]["nearest_frame"]]) for f in hinted]
    big = [(m, t) for m, t in turns if abs(t) >= 15]
    L.append(f"- The turn it gives: against the true turn to the same baseline view, median difference "
             f"{pct([abs(wrap(m - t)) for m, t in turns], 50):.0f} deg, worst {pct([abs(wrap(m - t)) for m, t in turns], 100):.0f}; "
             f"left or right is correct in {share(sum((m > 0) == (t > 0) for m, t in big), len(big))} of the hints whose true turn is at least 15 deg. "
             "What it says: " + ", ".join(f"{k} {v}" for k, v in Counter(f["v"][2]["hint"]["say"] for f in hinted).most_common()) + ".")
    L.append(f"- Wrong-room queries: {share(sum(bool(f['v'][2]['hint']) for f in wrong), len(wrong))} get a hint (the model puts a wrong-room picture "
             "somewhere, so the angle means nothing there).")
    L.append("")
    L.append("What `coverage()` reports for each baseline (12 frames, from one fix of each room; headings are map headings, 0 = where the first baseline frame looks):\n")
    L.append("| Room | Covered of 360 deg | Gaps (from, to, side) | Frames look between (pitch deg, minus = down) | Same-room queries whose heading falls in a gap | of those found |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    for name, s_, _ in groups[:-1]:
        if not s_:
            continue
        cov = s_[0]["v"][2]["coverage"]

        def in_gap(f):
            return any((f["v"][2]["heading_deg"] - g["from_deg"]) % 360 <= g["width_deg"] for g in f["v"][2]["coverage"]["gaps"])
        inside = [f for f in s_ if in_gap(f)]
        L.append(f"| {name} | {cov['covered_deg']} | " + ("; ".join(f"{g['from_deg']} to {g['to_deg']} ({g['side']})" for g in cov["gaps"]) or "none") +
                 f" | {cov['pitch_deg'][0]} to {cov['pitch_deg'][1]} | {len(inside)} | {share(sum(f['v'][2]['state'] == 'found' for f in inside), len(inside))} |")
    L.append("")

    # 24 baseline frames only when 12 are not enough
    both = [(f, g) for f in same for g in fixes if g["key"][1] == 24 and g["key"][2] == 1 and g["row"]["kind"] == "same" and g["key"][3:] == f["key"][3:]]
    if both:
        L.append("### F. 24 baseline frames only when the 12-frame fix is not found\n")
        t12 = pct([f["row"].get("inference_s", np.nan) + f["row"].get("locate_s", np.nan) for f, _ in both], 50)
        t24 = pct([g["row"].get("inference_s", np.nan) + g["row"].get("locate_s", np.nan) for _, g in both], 50)
        L.append("| Rule | Found | Unsure | Lost | Position error of found fixes m: median / 90th / worst | Heading error deg | Seconds per fix, mean |")
        L.append("| --- | --- | --- | --- | --- | --- | --- |")

        def line(label, picks, seconds):
            c = Counter(st for st, _ in picks)
            found = [t for st, t in picks if st == "found"]
            L.append(f"| {label} | {share(c['found'], len(picks))} | {c['unsure']} | {c['lost']} | {three([t['pos_err'] for t in found])} | "
                     f"{three([t['head_err'] for t in found], 1)} | {seconds:.1f} |")
        line("12 baseline frames, always", [(f["v"][2]["state"], f["t"]) for f, _ in both], t12)
        line("24 baseline frames, always", [(g["v"][2]["state"], g["t"]) for _, g in both], t24)
        again = [(f, g) for f, g in both if f["v"][2]["state"] != "found"]
        # the second fix replaces the first only when it is better
        order = {"found": 2, "unsure": 1, "lost": 0}
        esc = [(g["v"][2]["state"], g["t"]) if f["v"][2]["state"] != "found" and order[g["v"][2]["state"]] > order[f["v"][2]["state"]]
               else (f["v"][2]["state"], f["t"]) for f, g in both]
        line("12, then 24 when not found", esc, t12 + len(again) / len(both) * t24)
        L.append("")
        L.append(f"The same {len(both)} query frames each way. A second fix is run for {share(len(again), len(both))} of them and takes "
                 f"{t24:.1f} s on top of the first {t12:.1f} s, so a fix that is not found at first answers after {t12 + t24:.1f} s. "
                 f"Per room, found with 12 / with 24 / with the rule: " + "; ".join(
                     f"{room} {sum(f['v'][2]['state'] == 'found' for f, g in both if f['row']['room'] == room)} / "
                     f"{sum(g['v'][2]['state'] == 'found' for f, g in both if f['row']['room'] == room)} / "
                     f"{sum(s == 'found' for (s, _), (f, g) in zip(esc, both) if f['row']['room'] == room)} of "
                     f"{sum(f['row']['room'] == room for f, g in both)}" for room in rooms) + ".")
        w24 = [g for g in fixes if g["key"][1] == 24 and g["row"]["kind"] == "wrong"]
        L.append(f"Wrong-room queries with 24 frames: {share(sum(g['v'][2]['state'] != 'lost' for g in w24), len(w24))} accepted "
                 f"(highest one-frame score {max([g['v'][2]['match_one_frame'] for g in w24], default=0):.2f}). Every wrong-room view runs the second fix.\n")
    return "\n".join(L)


def rows_table(files, plan):
    """Rows from locate_walk_eval.py --from-dump (locate() itself on saved reconstructions), one file per setting."""
    L = ["| Setting (locate() on saved reconstructions) | Fixes | Found | Unsure | Lost | Wrong room accepted | Metres within 15% (found) | "
         "Distance error m: median / 90th / worst | Bearing error deg | Up from a surface |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for file in files:
        rows = [json.loads(line) for line in Path(file).read_text().splitlines() if line.strip()]
        rows = [r for r in rows if "error" not in r]
        same, wrong = [r for r in rows if r["kind"] == "same"], [r for r in rows if r["kind"] == "wrong"]
        c = Counter(r["state"] for r in same)
        d_abs, b_abs, ratio = [], [], []
        for r in same:
            if r["state"] != "found":
                continue
            t = truth(r, plan)
            ratio.append(r["metres_per_unit"] / t["s"])
            for w in r["waypoints"]:
                true_h, true_b = t["ways"][w["name"]]
                d_abs.append(abs(w["distance_m"] - true_h))
                if true_h >= 0.5:
                    b_abs.append(abs(wrap(w["bearing_deg"] - true_b)))
        L.append(f"| {Path(file).parent.name} | {len(same)} + {len(wrong)} | {share(c['found'], len(same))} | {c['unsure']} | {c['lost']} | "
                 f"{share(sum(r['state'] != 'lost' for r in wrong), len(wrong))} | {share(sum(abs(x - 1) <= 0.15 for x in ratio), len(ratio))} | "
                 f"{three(d_abs)} | {three(b_abs, 0)} | {share(sum(r.get('up_from') == 'surface' for r in same), len(same))} |")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", default="../walk/out/plan.json")
    ap.add_argument("--walk", default="../walk/out", help="folder with results.jsonl and fix/ of the walking test")
    ap.add_argument("--out", default="out_v2")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--rows", nargs="*", default=[], help="results.jsonl files from locate_walk_eval.py --from-dump: tables from locate() itself")
    ap.add_argument("--detector-dir", help="folder with the locate.py to measure (default: the one next to this script's parent)")
    args = ap.parse_args()
    if args.detector_dir:
        sys.path.insert(0, args.detector_dir)
    plan = json.loads(Path(args.plan).read_text())
    PLAN["plan"] = plan
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.rows:
        text = rows_table(args.rows, plan)
        (out / "rows.md").write_text(text + "\n")
        print(text)
        return
    rows = [json.loads(line) for line in (Path(args.walk) / "results.jsonl").read_text().splitlines() if line.strip()]
    rows = [r for r in rows if "error" not in r and r["n_new"] == 1]
    with Pool(args.workers) as pool:
        fixes = pool.map(work, [(r, args.walk) for r in rows], chunksize=8)
    (out / "replay.jsonl").write_text("\n".join(json.dumps(f) for f in fixes) + "\n")
    text = tables(fixes, rows, plan)
    (out / "v2.md").write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
