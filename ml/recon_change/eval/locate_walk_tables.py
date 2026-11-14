#!/usr/bin/env python3
"""Tables for locate_walk_eval.py: reads plan.json, results.jsonl and fix/*.npz, needs no GPU.

    python locate_walk_tables.py --plan out/plan.json --out out            # prints and writes out/results.md

Truth comes from TUM RGB-D: the motion-capture pose of every frame (metres, z up, floor at z = 0
within a few centimetres) and the Kinect depth at each place's pixel.

Position and heading error: the model's baseline cameras are fitted to their true positions
with one similarity transform per fix (scale, rotation, shift), then the query camera is compared.
What the display shows: locate.py's own distance_m and bearing_deg, untouched, against the true
horizontal distance and the true bearing (0 ahead, + to the right, around the true vertical).
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
# Height of the floor in each room's ground-truth frame, read off the histogram of Kinect point
# heights that `locate_walk_eval.py --check` prints.
FLOOR_Z = {"fr1_office": 0.05, "fr2_desk": -0.03, "fr3_office": -0.02}
UP = np.array([0.0, 0.0, 1.0])
EYE_M = 1.6


def similarity(src, dst):
    """Least-squares scale s, rotation R and shift t with dst ~ s * R @ src + t (Umeyama)."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    a, b = src - mu_s, dst - mu_d
    U, S, Vt = np.linalg.svd(b.T @ a / len(src))
    D = np.eye(3)
    D[2, 2] = np.sign(np.linalg.det(U @ Vt)) or 1.0
    R = U @ D @ Vt
    s = float((S * np.diag(D)).sum() / ((a ** 2).sum() / len(src)))
    return s, R, mu_d - s * R @ mu_s


def angle(a, b):
    return float(np.degrees(np.arccos(np.clip(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)), -1, 1))))


def wrap(deg):
    return (deg + 180) % 360 - 180


def floor_plane(cloud, up, tol, rng, tries=400):
    """The lowest large flat surface under the cameras: a plane within 35 degrees of level with
    next to nothing below it. Returns (normal pointing up, a point on it, share of points on it)."""
    h = cloud @ up
    low = cloud[h < np.percentile(h, 40)]
    thin = cloud[::3]  # a third of the points is enough to compare candidate planes
    best = None
    for _ in range(tries):
        a, b, c = low[rng.integers(0, len(low), 3)]
        n = np.cross(b - a, c - a)
        length = np.linalg.norm(n)
        if length < 1e-9:
            continue
        n = n / length * (1 if n @ up > 0 else -1)
        if n @ up < np.cos(np.radians(35)):
            continue
        dist = thin @ n - a @ n
        if (dist < -3 * tol).mean() > 0.03:
            continue
        score = int((np.abs(dist) < tol).sum())
        if best is None or score > best[0]:
            best = (score, n, a)
    if best is None:
        return None
    _, n, a = best
    on = cloud[np.abs(cloud @ n - a @ n) < tol]  # least-squares plane through the points on it
    score = len(on)
    centre = on.mean(0)
    normal = np.linalg.svd(on - centre, full_matrices=False)[2][-1]
    normal *= 1 if normal @ up > 0 else -1
    return normal, centre, score / len(cloud)


def show(E, base, places_model, up, metres):
    """locate.py's distance and bearing to each place, recomputed for another up axis or scale."""
    import locate

    north = E[base[0]][2, :3] - (E[base[0]][2, :3] @ up) * up
    north /= np.linalg.norm(north)
    east = np.cross(north, up)
    position, forward = locate.camera(E[-1])
    heading = np.degrees(np.arctan2(forward @ east, forward @ north))
    out = {}
    for name, X in places_model.items():
        d = (X - position) * metres
        e, n = d @ east, d @ north
        out[name] = (float(np.hypot(e, n)), float(wrap(np.degrees(np.arctan2(e, n)) - heading)))
    return out


def derive(row, plan, out_dir, rng, with_floor):
    """Everything measured for one same-room fix."""
    import locate

    room = plan["rooms"][row["room"]]
    q = room["queries"][row["query"]]
    E = np.array(row["E"])
    nb = row["n_baseline"]
    base = list(range(nb))
    P = np.array([locate.camera(e)[0] for e in E])
    G = np.array([room["baseline"][i]["C"] for i in row["pick"]])
    s, R, t = similarity(P[:nb], G)
    qC, qR = np.array(q["C"]), np.array(q["R"])
    fwd, true_fwd = R @ E[-1][2, :3], qR[:, 2]
    up_m, north_m, east_m = locate.axes(E, base)
    metres = row["metres_per_unit"]
    m = {
        "pos_err": float(np.linalg.norm(s * R @ P[-1] + t - qC)),
        "head_err": angle(fwd, true_fwd),
        "fit_rms": float(np.sqrt((((s * P[:nb] @ R.T + t) - G) ** 2).sum(1).mean())),
        "ratio": metres / s,  # the code's metres per true metre
        "nearest": float(np.linalg.norm(G - qC, axis=1).min()),
        "view_gap": min(angle(true_fwd, np.array(room["baseline"][i]["R"])[:, 2]) for i in row["pick"]),
        "tilt": angle(R @ up_m, UP),
        "pitch": float(np.degrees(np.arcsin(np.clip(-true_fwd[2], -1, 1)))),
        "source": q["source"], "speed": q["speed"], "turn": q["turn"], "ways": [],
    }
    fh = true_fwd - (true_fwd @ UP) * UP
    fh /= np.linalg.norm(fh)
    right = np.cross(fh, UP)
    truth = {p["name"]: np.array(p["world"]) for p in room["places"]}
    model_places = {}
    for w in row["waypoints"]:
        x, y, z = w["position"]
        X = P[0] + (east_m * x + north_m * y + up_m * z) / metres  # the place in the model's own frame
        model_places[w["name"]] = X
        d = truth[w["name"]] - qC
        true_h = float(np.hypot(d @ right, d @ fh))
        m["ways"].append({"name": w["name"], "true_h": true_h, "true_3d": float(np.linalg.norm(d)),
                          "true_b": float(np.degrees(np.arctan2(d @ right, d @ fh))),
                          "shown_d": w["distance_m"], "shown_b": w["bearing_deg"],
                          "place_err": float(np.linalg.norm(s * R @ X + t - truth[w["name"]]))})
    # Other ways to get metres. (1) One known distance: the two places farthest apart.
    names = [w["name"] for w in row["waypoints"]]
    if len(names) >= 2:
        pairs = [(a, b) for i, a in enumerate(names) for b in names[i + 1:]]
        a, b = max(pairs, key=lambda ab: np.linalg.norm(truth[ab[0]] - truth[ab[1]]))
        m["pair"] = [a, b]
        on_pair = float(np.linalg.norm(model_places[a] - model_places[b]) * metres / np.linalg.norm(truth[a] - truth[b]))
        m["ratio_pair"] = m["ratio"] / on_pair  # metres rescaled so that this one distance comes out right
    # (2) The floor: the cameras' height above it is a person's eye height.
    file = out_dir / "fix" / ("_".join(str(v) for v in row["key"]) + ".npz")
    if with_floor and file.exists():
        cloud = np.load(file)["cloud"].astype(np.float64)
        found = floor_plane(cloud, up_m, 0.02 * row["median_baseline_depth"], rng)
        if found is not None:
            normal, centre, on = found
            height = float(np.mean((P[:nb] - centre) @ normal))  # mean baseline camera height, model units
            true_height = float(np.mean(G[:, 2]) - FLOOR_Z[row["room"]])
            if height > 1e-6:
                m.update(floor_share=on, floor_tilt=angle(R @ normal, UP), true_cam_height=true_height,
                         ratio_floor_true_height=(true_height / height) / s, ratio_floor_eye=(EYE_M / height) / s,
                         shown_floor_up=show(E, base, model_places, normal, EYE_M / height),
                         shown_floor_up_true_height=show(E, base, model_places, normal, true_height / height))
    return m


# ----------------------------------------------------------------------------- tables
def stat(values, how="median", digits=2):
    values = [v for v in values if v is not None and np.isfinite(v)]
    if not values:
        return "-"
    f = {"median": np.median, "p90": lambda x: np.percentile(x, 90), "p10": lambda x: np.percentile(x, 10), "max": np.max, "min": np.min,
         "mean": np.mean}[how]
    return f"{f(values):.{digits}f}"


def share(k, n):
    return f"{k}/{n} ({100 * k / n:.0f}%)" if n else "-"


def three(values, digits=2):
    return f"{stat(values, 'median', digits)} | {stat(values, 'p90', digits)} | {stat(values, 'max', digits)}"


def ways(rows, key, far=0.0):
    out = []
    for r in rows:
        ratio = r["m"]["ratio"]
        for w in r["m"]["ways"]:
            if w["true_h"] < far:
                continue
            out.append({"d_abs": abs(w["shown_d"] - w["true_h"]), "d_signed": w["shown_d"] - w["true_h"],
                        "d_rel": 100 * abs(w["shown_d"] - w["true_h"]) / max(w["true_h"], 1e-6), "d_ratio": w["shown_d"] / max(w["true_h"], 1e-6),
                        "d_fixed_scale": abs(w["shown_d"] / ratio - w["true_h"]),
                        "b_abs": abs(wrap(w["shown_b"] - w["true_b"])), "place_err": w["place_err"], "true_h": w["true_h"]}[key])
    return out


def tables(rows, plan, meta):
    L = []
    rooms = list(plan["rooms"])
    ok = [r for r in rows if "error" not in r]
    failed = len(rows) - len(ok)

    def pick(kind, nb=12, nn=1, room=None):
        return [r for r in ok if r["kind"] == kind and r["key"][1] == nb and r["key"][2] == nn and (room is None or r["room"] == room)]

    def groups(kind, nb=12, nn=1):
        return [(room, pick(kind, nb, nn, room)) for room in rooms] + [("**All rooms**", pick(kind, nb, nn))]

    L += [f"Fixes: {len(ok)}" + (f" ({failed} failed)" if failed else "") + f". Thresholds: found at match >= {meta.get('found')}, lost below {meta.get('unsure')}. "
          f"GPU: {meta.get('gpu')}. Code under test: commit {meta.get('detector_commit')}.", ""]

    L += ["### 1. Is the wearer found? (12 baseline frames, 1 query frame)", "",
          "| Room | Same-room queries | Found | Unsure | Lost | Match median | Match min | Wrong-room queries | Wrong room accepted (found or unsure) | Wrong-room match max |",
          "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for label, got in groups("same"):
        c = Counter(r["state"] for r in got)
        wrong = pick("wrong", room=None if label.startswith("**") else label)
        wc = Counter(r["state"] for r in wrong)
        L.append(f"| {label} | {len(got)} | {share(c['found'], len(got))} | {share(c['unsure'], len(got))} | {share(c['lost'], len(got))} | "
                 f"{stat([r['confidence'] for r in got])} | {stat([r['confidence'] for r in got], 'min')} | {len(wrong)} | "
                 f"{share(wc['found'] + wc['unsure'], len(wrong))} | {stat([r['confidence'] for r in wrong], 'max')} |")

    # The match score taken apart (views_shares in locate_walk_eval.py): would another rule do better?
    def rule(r, i):
        if "views_query" not in r:
            return None
        trusted = max(r["trusted"], 1e-6)
        usual = float(np.median([p[i] * min(1.0, p[4] / trusted) for p in r["views_probes"]])) or 1.0
        return min(1.0, r["views_query"][i] * min(1.0, r["views_query"][4] / trusted) / usual)

    same, wrong = pick("same"), pick("wrong")
    L += ["", "Match scores, and what other rules would have given on the same fixes (12 + 1). The shipped rule counts a pixel of the query when at "
          "least two baseline frames see it at the same depth and looking alike. Found is a score of at least 0.5, accepted at least 0.3.", "",
          "| Rule | Same room: score median | p10 | Found | Accepted | Wrong room: score median | max | Accepted | Same-room queries scoring above every wrong-room query | "
          "Its found fixes: position error m p90 | max | heading error deg max | off by more than 0.2 m or 10 deg |",
          "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for label, score in (("shipped (`confidence`)", lambda r: r["confidence"]),
                         ("shipped, recomputed from its parts (check)", lambda r: rule(r, 3)),
                         ("one baseline frame is enough (same depth and looking alike)", lambda r: rule(r, 2)),
                         ("depth only, two baseline frames (`match_by_depth`)", lambda r: r["match_by_depth"]),
                         ("depth only, one baseline frame", lambda r: rule(r, 0))):
        s, w = [score(r) for r in same], [score(r) for r in wrong]
        good = [r for r, v in zip(same, s) if v is not None and v >= 0.5]
        s, w = [v for v in s if v is not None], [v for v in w if v is not None]
        top = max(w) if w else 0.0
        L.append(f"| {label} | {stat(s)} | {stat(s, 'p10')} | {share(sum(v >= 0.5 for v in s), len(s))} | {share(sum(v >= 0.3 for v in s), len(s))} | "
                 f"{stat(w)} | {stat(w, 'max')} | {share(sum(v >= 0.3 for v in w), len(w))} | {share(sum(v > top for v in s), len(s))} | "
                 f"{stat([r['m']['pos_err'] for r in good], 'p90')} | {stat([r['m']['pos_err'] for r in good], 'max')} | "
                 f"{stat([r['m']['head_err'] for r in good], 'max', 1)} | "
                 f"{share(sum(r['m']['pos_err'] > 0.2 or r['m']['head_err'] > 10 for r in good), len(good))} |")
    turned_away = [r for r in same if r["state"] != "found"]
    right = [r for r in turned_away if r["m"]["pos_err"] < 0.2 and r["m"]["head_err"] < 10]
    L += ["", f"Of the {len(turned_away)} same-room queries that were not found, {len(right)} had the right position anyway (within 0.2 m and 10 degrees): "
          "turned away for nothing. Per room: " + ", ".join(
              f"{room} {sum(r['room'] == room for r in right)} of {sum(r['room'] == room for r in turned_away)}" for room in rooms) + "."]
    L += ["", "Parts of the score, medians: share of the frame's trusted pixels that at least 1 / at least 2 baseline frames see at the same depth, and "
          "where at least 1 / at least 2 also look alike.", "",
          "| Room | Frame | Depth, 1 frame | Depth, 2 frames | Alike, 1 frame | Alike, 2 frames |", "| --- | --- | --- | --- | --- | --- |"]
    for room in rooms:
        for label, rows_, part in (("same-room query", pick("same", room=room), lambda r: [r["views_query"]]),
                                   ("baseline frame against the other baseline frames (the yardstick)", pick("same", room=room), lambda r: r["views_probes"]),
                                   ("wrong-room query", pick("wrong", room=room), lambda r: [r["views_query"]])):
            parts = [p for r in rows_ if "views_query" in r for p in part(r)]
            L.append(f"| {room} | {label} | " + " | ".join(stat([p[i] for p in parts]) for i in range(4)) + " |")

    L += ["", "By where the query frame comes from:", "",
          "| Room | Query frames from | Queries | Found | Unsure | Lost | Position error m median | p90 | max | Heading error deg median | p90 | max |",
          "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for room in rooms:
        got = pick("same", room=room)
        for source in dict.fromkeys(r["m"]["source"] for r in got):
            g = [r for r in got if r["m"]["source"] == source]
            c = Counter(r["state"] for r in g)
            label = "the baseline recording, held out" if source == "held_out" else f"another recording ({source})"
            L.append(f"| {room} | {label} | {len(g)} | {share(c['found'], len(g))} | {c['unsure']} | {c['lost']} | "
                     f"{three([r['m']['pos_err'] for r in g])} | {three([r['m']['head_err'] for r in g], 1)} |")

    L += ["", "### 2. Is the position right? (same-room queries, 12 + 1, in true metres and degrees)", "",
          "| Room | Fixes counted | Position error m median | p90 | max | Heading error deg median | p90 | max | Baseline fit m (RMS) median | "
          "Query to nearest baseline camera m median |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for which, test in (("all", lambda r: True), ("found only", lambda r: r["state"] == "found"), ("unsure or lost", lambda r: r["state"] != "found")):
        for label, got in groups("same"):
            g = [r for r in got if test(r)]
            L.append(f"| {label}, {which} | {len(g)} | {three([r['m']['pos_err'] for r in g])} | {three([r['m']['head_err'] for r in g], 1)} | "
                     f"{stat([r['m']['fit_rms'] for r in g], 'median', 3)} | {stat([r['m']['nearest'] for r in g])} |")

    L += ["", "### 3. What the display shows against the truth (found fixes, 12 + 1, the code's own metres)", "",
          "Each fix gives a distance and a bearing to every place of the room. Bearing rows count only places at least 0.5 m away (true horizontal distance).", "",
          "| Room | Fix-place pairs | True distance m median | Shown / true distance median | Distance error m median | p90 | max | "
          "Distance error % median | Bearing error deg median | p90 | max | Bearing off by more than 30 deg | Place position error m median |",
          "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for which, test in (("found", lambda r: r["state"] == "found"), ("every same-room fix, also unsure and lost", lambda r: True)):
        for label, got in groups("same"):
            g = [r for r in got if test(r)]
            b = ways(g, "b_abs", 0.5)
            L.append(f"| {label}, {which} | {len(ways(g, 'd_abs'))} | {stat(ways(g, 'true_h'))} | {stat(ways(g, 'd_ratio'))} | {three(ways(g, 'd_abs'))} | "
                     f"{stat(ways(g, 'd_rel'), 'median', 0)} | {three(b, 0)} | {share(sum(v > 30 for v in b), len(b))} | {stat(ways(g, 'place_err'))} |")

    L += ["", "### 4. How wrong are the metres?", "",
          "The code assumes the baseline's median viewing distance is 2 m. Ratio = the code's metres / true metres (1.00 would be right; 1.50 means "
          "a true 2 m is shown as 3 m).", "",
          "| Room | Fixes | Ratio median | p10 | p90 | min | max | typical_m that would have been right | Kinect median depth of the baseline frames m | "
          "Tilt of the code's up axis from true vertical deg median |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for label, got in groups("same"):
        ratio = [r["m"]["ratio"] for r in got]
        kinect = "-" if label.startswith("**") else f"{plan['rooms'][label]['kinect_median_depth_m']:.2f}"
        L.append(f"| {label} | {len(got)} | {stat(ratio)} | {stat(ratio, 'p10')} | {stat(ratio, 'p90')} | {stat(ratio, 'min')} | {stat(ratio, 'max')} | "
                 f"{stat([2.0 / v for v in ratio])} | {kinect} | {stat([r['m']['tilt'] for r in got], 'median', 1)} |")

    def split(title, first, bands, value):
        L.extend(["", title, "", f"| {first} | Queries | Found | Unsure | Lost | Position error m median | p90 | max | Heading error deg median | p90 | max | "
                  "Bearing error deg median (found) | Distance error m median, metres corrected (found) |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"])
        got = pick("same")
        for label, test in bands:
            g = [r for r in got if value(r) is not None and test(value(r))]
            c = Counter(r["state"] for r in g)
            f = [r for r in g if r["state"] == "found"]
            L.append(f"| {label} | {len(g)} | {share(c['found'], len(g))} | {c['unsure']} | {c['lost']} | {three([r['m']['pos_err'] for r in g])} | "
                     f"{three([r['m']['head_err'] for r in g], 1)} | {stat(ways(f, 'b_abs', 0.5), 'median', 0)} | {stat(ways(f, 'd_fixed_scale'))} |")

    # Blur: thirds of the sharpness of the query frame within its own room (rooms differ in texture).
    edges = {room: np.percentile([r["sharpness"] for r in pick("same", room=room)] or [0], [33.3, 66.7]) for room in rooms}
    for r in pick("same"):
        lo, hi = edges[r["room"]]
        r["m"]["blur_band"] = 0 if r["sharpness"] < lo else 1 if r["sharpness"] < hi else 2
    L += ["", "### 5. Where it breaks (same-room queries, 12 + 1, all rooms pooled)"]
    split("By blur. Sharpness is `recon_change.sharpness` of the query frame (variance of the Laplacian); thirds within each room. Edges: "
          + "; ".join(f"{room} {edges[room][0]:.0f} and {edges[room][1]:.0f}" for room in rooms) + ".", "Sharpness of the query frame",
          [("blurriest third", lambda v: v == 0), ("middle third", lambda v: v == 1), ("sharpest third", lambda v: v == 2)], lambda r: r["m"]["blur_band"])
    split("By sharpness in absolute numbers, all rooms on one scale (a frame under 150 is visibly smeared; a plain close-up also scores low):",
          "Sharpness of the query frame", [("under 150", lambda v: v < 150), ("150 to 400", lambda v: 150 <= v < 400),
                                           ("400 to 800", lambda v: 400 <= v < 800), ("800 and more", lambda v: v >= 800)], lambda r: r["sharpness"])
    split("By how fast the camera was turning when the frame was taken (from the motion capture):", "Turning speed deg/s",
          [("under 15", lambda v: v < 15), ("15 to 40", lambda v: 15 <= v < 40), ("40 to 80", lambda v: 40 <= v < 80), ("80 and more", lambda v: v >= 80)],
          lambda r: r["m"]["turn"])
    split("By true distance from the query camera to the nearest baseline camera used in the fix:", "Distance to nearest baseline camera m",
          [("under 0.2", lambda v: v < 0.2), ("0.2 to 0.4", lambda v: 0.2 <= v < 0.4), ("0.4 to 0.8", lambda v: 0.4 <= v < 0.8), ("0.8 and more", lambda v: v >= 0.8)],
          lambda r: r["m"]["nearest"])
    split("By the angle between the query's viewing direction and the closest viewing direction among the baseline cameras:", "Angle to closest baseline view deg",
          [("under 15", lambda v: v < 15), ("15 to 30", lambda v: 15 <= v < 30), ("30 to 50", lambda v: 30 <= v < 50), ("50 and more", lambda v: v >= 50)],
          lambda r: r["m"]["view_gap"])
    split("By how far down the camera looked (0 = level, 90 = straight down):", "Pitch deg",
          [("under 15", lambda v: v < 15), ("15 to 30", lambda v: 15 <= v < 30), ("30 to 45", lambda v: 30 <= v < 45), ("45 and more", lambda v: v >= 45)],
          lambda r: r["m"]["pitch"])
    def by_room(title, bands, value):
        L.extend(["", title, "", "| Band | " + " | ".join(f"{room}: found" for room in rooms) + " |", "| --- |" + " --- |" * len(rooms)])
        for label, test in bands:
            cells = []
            for room in rooms:
                g = [r for r in pick("same", room=room) if value(r) is not None and test(value(r))]
                cells.append(share(sum(r["state"] == "found" for r in g), len(g)))
            L.append(f"| {label} | " + " | ".join(cells) + " |")

    by_room("Found rate per room, by blur (thirds within the room):",
            [("blurriest third", lambda v: v == 0), ("middle third", lambda v: v == 1), ("sharpest third", lambda v: v == 2)], lambda r: r["m"]["blur_band"])
    L += ["", "What blur does to the score (medians; `match_by_depth` is the score without the appearance check, `looks_alike` the share of "
          "depth-matched pixels that also look alike):", "", "| Room | Blur band | Queries | match_by_depth | looks_alike | Match (shipped) | "
          "Position error m median | Not found although within 0.2 m and 10 deg |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for room in rooms:
        for band, label in enumerate(("blurriest third", "middle third", "sharpest third")):
            g = [r for r in pick("same", room=room) if r["m"]["blur_band"] == band]
            lost_right = sum(r["state"] != "found" and r["m"]["pos_err"] < 0.2 and r["m"]["head_err"] < 10 for r in g)
            L.append(f"| {room} | {label} | {len(g)} | {stat([r['match_by_depth'] for r in g])} | {stat([r['looks_alike'] for r in g])} | "
                     f"{stat([r['confidence'] for r in g])} | {stat([r['m']['pos_err'] for r in g])} | {share(lost_right, len(g))} |")
    by_room("Found rate per room, by turning speed (deg/s):",
            [("under 15", lambda v: v < 15), ("15 to 40", lambda v: 15 <= v < 40), ("40 and more", lambda v: v >= 40)], lambda r: r["m"]["turn"])
    by_room("Found rate per room, by the angle to the closest baseline view (deg):",
            [("under 15", lambda v: v < 15), ("15 to 30", lambda v: 15 <= v < 30), ("30 and more", lambda v: v >= 30)], lambda r: r["m"]["view_gap"])
    L += ["", "Blur with coverage held fixed: only queries that look within 20 degrees of a baseline view.", "",
          "| Band | Queries | Found | Unsure | Lost | Position error m median | max | Heading error deg median | max |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    covered = [r for r in pick("same") if r["m"]["view_gap"] < 20]
    for label, test in (("turning under 15 deg/s", lambda r: r["m"]["turn"] is not None and r["m"]["turn"] < 15),
                        ("turning 15 to 40 deg/s", lambda r: r["m"]["turn"] is not None and 15 <= r["m"]["turn"] < 40),
                        ("turning 40 deg/s and more", lambda r: r["m"]["turn"] is not None and r["m"]["turn"] >= 40),
                        ("sharpness under 150", lambda r: r["sharpness"] < 150), ("sharpness 150 to 400", lambda r: 150 <= r["sharpness"] < 400),
                        ("sharpness 400 and more", lambda r: r["sharpness"] >= 400)):
        g = [r for r in covered if test(r)]
        c = Counter(r["state"] for r in g)
        L.append(f"| {label} | {len(g)} | {share(c['found'], len(g))} | {c['unsure']} | {c['lost']} | {stat([r['m']['pos_err'] for r in g])} | "
                 f"{stat([r['m']['pos_err'] for r in g], 'max')} | {stat([r['m']['head_err'] for r in g], 'median', 1)} | {stat([r['m']['head_err'] for r in g], 'max', 1)} |")
    near_all = [r["m"]["nearest"] for r in pick("same")]
    L += ["", f"Range tested: the query camera was {stat(near_all, 'min')} to {stat(near_all, 'max')} m from the nearest baseline camera "
          f"(median {stat(near_all)}), turning at up to {stat([r['m']['turn'] for r in pick('same')], 'max', 0)} deg/s and moving at up to "
          f"{stat([r['m']['speed'] for r in pick('same')], 'max')} m/s."]
    split("By match score:", "Match", [("under 0.3 (lost)", lambda v: v < 0.3), ("0.3 to 0.5 (unsure)", lambda v: 0.3 <= v < 0.5),
                                       ("0.5 to 0.8", lambda v: 0.5 <= v < 0.8), ("0.8 and more", lambda v: v >= 0.8)], lambda r: r["confidence"])

    worst = sorted(pick("same"), key=lambda r: -r["m"]["pos_err"])[:12]
    L += ["", "The twelve worst position errors (12 + 1):", "",
          "| Room | Query frame | State | Match | Position error m | Heading error deg | Nearest baseline camera m | Angle to closest view deg | Sharpness | Turning deg/s |",
          "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in worst:
        q = plan["rooms"][r["room"]]["queries"][r["query"]]
        L.append(f"| {r['room']} | {q['rec']}/{q['rgb']} | {r['state']} | {r['confidence']:.2f} | {r['m']['pos_err']:.2f} | {r['m']['head_err']:.1f} | "
                 f"{r['m']['nearest']:.2f} | {r['m']['view_gap']:.0f} | {r['sharpness']:.0f} | {stat([r['m']['turn']], 'median', 0)} |")

    def paired(title, a, b, la, lb):
        A = {(r["room"], r["query"]): r for r in pick("same", *a)}
        B = {(r["room"], r["query"]): r for r in pick("same", *b)}
        both = sorted(set(A) & set(B))
        L.extend(["", title + f" The same {len(both)} query frames both ways.", "",
                  "| Setting | Found | Unsure | Lost | Match median | Position error m median | p90 | max | Heading error deg median | p90 | max | "
                  "Ratio code metres / true metres median | Model s median | Locate s median | Whole fix s median |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"])
        for label, D in ((la, A), (lb, B)):
            g = [D[k] for k in both]
            c = Counter(r["state"] for r in g)
            L.append(f"| {label} | {share(c['found'], len(g))} | {c['unsure']} | {c['lost']} | {stat([r['confidence'] for r in g])} | "
                     f"{three([r['m']['pos_err'] for r in g])} | {three([r['m']['head_err'] for r in g], 1)} | {stat([r['m']['ratio'] for r in g])} | "
                     f"{stat([r['inference_s'] for r in g])} | {stat([r['locate_s'] for r in g])} | "
                     f"{stat([r['reconstruct_s'] + r['locate_s'] for r in g])} |")

    L += ["", "### 6. 12 or 24 baseline frames, 1 or 3 query frames"]
    paired("Baseline frames.", (12, 1), (24, 1), "12 baseline frames", "24 baseline frames")
    paired("Query frames (3 = the frames 1.0 s and 0.5 s earlier plus the query frame; the newest is the wearer).", (12, 1), (12, 3), "1 query frame", "3 query frames")
    w12, w24 = pick("wrong", 12, 1), pick("wrong", 24, 1)
    L += ["", f"Wrong-room queries accepted: {share(sum(r['state'] != 'lost' for r in w12), len(w12))} with 12 baseline frames, "
          f"{share(sum(r['state'] != 'lost' for r in w24), len(w24))} with 24. Highest wrong-room match: {stat([r['confidence'] for r in w12], 'max')} and "
          f"{stat([r['confidence'] for r in w24], 'max')}."]

    L += ["", "### 7. Seconds per fix on the GPU", "",
          "| Frames (baseline + query) | Fixes | Model s median | p90 | Locate s median | p90 | Model + locate s median | Peak GPU GB |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for nb, nn in ((12, 1), (12, 3), (24, 1)):
        g = [r for r in ok if r["key"][1] == nb and r["key"][2] == nn]
        L.append(f"| {nb} + {nn} | {len(g)} | {stat([r['inference_s'] for r in g])} | {stat([r['inference_s'] for r in g], 'p90')} | "
                 f"{stat([r['locate_s'] for r in g])} | {stat([r['locate_s'] for r in g], 'p90')} | "
                 f"{stat([r['reconstruct_s'] + r['locate_s'] for r in g])} | {stat([r['peak_gpu_gb'] for r in g], 'max', 1)} |")
    L += ["", "Model s is VGGT inference as printed by `reconstruct`; locate s is `locate.locate`. Loading the model (once) and reading the pictures are not included."]

    L += ["", "### 8. Other ways to get metres (found fixes, 12 + 1)", "",
          "Nothing here is in `locate.py`; each row is what the same fixes would have shown with another rule for metres. "
          "\"Ratio\" is that rule's metres / true metres.", "",
          "| Room | Rule | Fixes | Ratio median | p10 | p90 | Fixes with metres within 15% of the truth | Distance error m median | p90 | max | "
          "Distance error % median | Bearing error deg median | p90 |",
          "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for label, got in groups("same"):
        g = [r for r in got if r["state"] == "found"]

        def line(rule, rows_, ratio_of, shown=None):
            d, rel, b, ratios = [], [], [], []
            for r in rows_:
                k = ratio_of(r)
                if k is None:
                    continue
                ratios.append(k)
                for w in r["m"]["ways"]:
                    sd, sb = (w["shown_d"] * k / r["m"]["ratio"], w["shown_b"]) if shown is None else r["m"][shown][w["name"]]
                    d.append(abs(sd - w["true_h"]))
                    rel.append(100 * abs(sd - w["true_h"]) / max(w["true_h"], 1e-6))
                    if w["true_h"] >= 0.5:
                        b.append(abs(wrap(sb - w["true_b"])))
            L.append(f"| {label} | {rule} | {len(ratios)} | {stat(ratios)} | {stat(ratios, 'p10')} | {stat(ratios, 'p90')} | "
                     f"{share(sum(abs(k - 1) <= 0.15 for k in ratios), len(ratios))} | {three(d)} | {stat(rel, 'median', 0)} | "
                     f"{stat(b, 'median', 0)} | {stat(b, 'p90', 0)} |")

        line("as shipped: median viewing distance = 2 m", g, lambda r: r["m"]["ratio"])
        line("median viewing distance = 1.5 m (`typical_m=1.5`)", g, lambda r: r["m"]["ratio"] * 0.75)
        line("one known distance (the two places farthest apart)", g, lambda r: r["m"].get("ratio_pair"))
        line(f"floor found, cameras assumed {EYE_M} m above it (scale only)", g, lambda r: r["m"].get("ratio_floor_eye"))
        line("floor found, the true camera height given (scale only)", g, lambda r: r["m"].get("ratio_floor_true_height"))
        line(f"floor found, {EYE_M} m, and the floor's normal as the up axis", [r for r in g if "shown_floor_up" in r["m"]],
             lambda r: r["m"].get("ratio_floor_eye"), "shown_floor_up")
        line("floor found, true camera height, floor's normal as up", [r for r in g if "shown_floor_up_true_height" in r["m"]],
             lambda r: r["m"].get("ratio_floor_true_height"), "shown_floor_up_true_height")
        line("perfect metres (the true scale of each fix)", g, lambda r: 1.0)
    got = pick("same")
    have = [r for r in got if "floor_tilt" in r["m"]]
    L += ["", f"Floor found in {share(len(have), len(got))} of the same-room fixes. Tilt of the up axis from the true vertical, degrees: the code's "
          f"(mean of the baseline cameras' up) median {stat([r['m']['tilt'] for r in got], 'median', 1)}, p90 {stat([r['m']['tilt'] for r in got], 'p90', 1)}; "
          f"the found floor's normal median {stat([r['m']['floor_tilt'] for r in have], 'median', 1)}, p90 {stat([r['m']['floor_tilt'] for r in have], 'p90', 1)}. "
          f"True mean height of the baseline cameras above the floor: "
          + ", ".join(f"{room} {stat([r['m'].get('true_cam_height') for r in pick('same', room=room)])} m" for room in rooms) + "."]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", default="out/plan.json")
    ap.add_argument("--out", default="out")
    ap.add_argument("--detector-dir", help="folder with locate.py and recon_change.py (default: next to this script, else its parent)")
    ap.add_argument("--no-floor", action="store_true", help="skip the floor search (faster)")
    args = ap.parse_args()
    detector_dir = Path(args.detector_dir) if args.detector_dir else HERE if (HERE / "locate.py").exists() else HERE.parent
    sys.path[:0] = [str(HERE), str(detector_dir)]
    out = Path(args.out)
    plan = json.loads(Path(args.plan).read_text())
    meta = json.loads((out / "meta.json").read_text()) if (out / "meta.json").exists() else {}
    rows = [json.loads(line) for line in (out / "results.jsonl").read_text().splitlines() if line.strip()]
    rng = np.random.default_rng(0)
    for r in rows:
        if "error" not in r and r["kind"] == "same":
            r["m"] = derive(r, plan, out, rng, with_floor=not args.no_floor and r["key"][1] == 12 and r["key"][2] == 1)
    text = tables(rows, plan, meta)
    (out / "results.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
