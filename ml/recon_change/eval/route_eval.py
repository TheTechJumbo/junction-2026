#!/usr/bin/env python3
"""Measure route.py against the true obstacles of real rooms (TUM RGB-D). CPU only.

It reuses the walking test (locate_walk_eval.py): every position fix of that test left one line in
results.jsonl (the wearer's position, the places, every camera) and the baseline's 3D points in
fix/*.npz. From those this script rebuilds the floor plan the fix carried (the same rule as
locate.py: points between the 40th and 90th percentile of height, 1500 of them), asks route.py
for the way to each place, and lays the answer on the TRUE room:

    truly blocked   the Kinect measured something between ankle and head height over that spot
    truly free      the Kinect saw the floor there and nothing above it
    not measured    neither

The truth frame comes from motion capture; a route is moved into it with the same fit the walking
test uses (the model's baseline cameras onto their true positions, one fit per fix).

    python route_eval.py --walk WALK --out OUT                 # WALK holds out/plan.json, out/results.jsonl, out/fix, data/tum
    python route_eval.py --walk WALK --out OUT --pictures 4    # also a few pictures per room
"""
import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import locate_walk_eval as lw  # noqa: E402  (reading TUM, the rooms)
import locate_walk_tables as lt  # noqa: E402  (finding the floor in the model's points)
import route  # noqa: E402

TRUTH_CELL = 0.05           # metres
ANKLE_M, HEAD_M = 0.12, 1.9  # something between these heights above the floor is in a walker's way
FLOOR_BAND_M = 0.06         # a Kinect point this close to the floor plane is floor
FAR_M = 3.5                 # Kinect depth beyond this is too noisy to trust
SKIP_START_M = 0.3          # the first steps are not judged: the Kinect is held out in front of the person
HALF_BODY_M = 0.25          # true metres: does a body fit along the route?
WORTH_M = 1.0               # ways to places truly nearer than this are not judged
# Height of the floor in each room's truth frame (as in locate_walk_tables.py): where the plane fit starts.
FLOOR_Z = {"fr1_office": 0.05, "fr2_desk": -0.03, "fr3_office": -0.02}

# What is tried: name -> (which floor plan, settings of route.build). The first is route.py as it is.
SETTINGS = {
    "default settings": ("shipped", {}),
    "lone points count": ("shipped", {"keep_lone": True}),
    "unseen floor costs 2": ("shipped", {"unseen_cost": 2.0}),
    "unseen floor costs 5": ("shipped", {"unseen_cost": 5.0}),
    "only within 1.0 m of the walked line": ("shipped", {"corridor": 1.0}),
    "only within 0.6 m of the walked line": ("shipped", {"corridor": 0.6}),
    "lone points count, unseen floor costs 5": ("shipped", {"keep_lone": True, "unseen_cost": 5.0}),
    "lone points count, only within 1.0 m": ("shipped", {"keep_lone": True, "corridor": 1.0}),
    "lone points count, only within 0.6 m": ("shipped", {"keep_lone": True, "corridor": 0.6}),
    "no extra cost near things": ("shipped", {"near_cost": 0.0}),
    "body 0.4 m": ("shipped", {"body": 0.4}),
    "body 0.8 m": ("shipped", {"body": 0.8}),
    "own floor: 1500 points": ("own floor", {}),
    "own floor: 1500 points, lone points count": ("own floor", {"keep_lone": True}),
    "own floor: 1500 points, lone points count, only within 1.0 m": ("own floor", {"keep_lone": True, "corridor": 1.0}),
    "own floor: every point": ("own floor dense", {}),
    "own floor: every point, only within 1.0 m": ("own floor dense", {"corridor": 1.0}),
    "true floor: 1500 points": ("ideal", {}),
    "true floor: 1500 points, lone points count": ("ideal", {"keep_lone": True}),
    "true floor: every point": ("ideal dense", {}),
    "true floor: every point, only within 1.0 m": ("ideal dense", {"corridor": 1.0}),
}
FROM_CAMERAS = "default settings, from where the baseline camera stood"


# ----------------------------------------------------------------------------- the true room
def clean_depth(d):
    """Kinect depth without the pixels at depth edges (they float between the near and the far thing)."""
    big = np.where(d > 0, d, 0).astype(np.float32)
    small = np.where(d > 0, d, 99).astype(np.float32)
    k = np.ones((3, 3), np.uint8)
    spread = cv2.dilate(big, k) - cv2.erode(small, k)
    return np.where((d > 0) & (d < FAR_M) & (spread < 0.03 + 0.02 * d), d, 0)


def cloud_of(folder, frame, cam, step=3):
    fx, fy, cx, cy = cam
    d = clean_depth(lw.kinect_depth(folder, frame))[::step, ::step]
    v, u = np.mgrid[0:480:step, 0:640:step]
    ok = d > 0
    pts = np.stack([(u[ok] - cx) / fx * d[ok], (v[ok] - cy) / fy * d[ok], d[ok]], 1)
    return pts @ frame["R"].T + frame["C"]


def true_room(data, room, every, log, every_recording=False):
    """The room's truth grid: 0 not measured, 1 free floor, 2 blocked. A cell counts when at least
    three frames agree. From the baseline recording alone (the room as it was when the map was
    made), or from every recording that shares its motion-capture frame (more of the room is
    measured, but a chair that was moved between two recordings is then blocked in both spots)."""
    spec = lw.ROOMS[room]
    recs = [spec["baseline"]] + [r for r in spec["others"] if every_recording and (Path(data) / r).exists()]
    frames = [(Path(data) / rec, f, lw.INTRINSICS[rec.split("_")[0]]) for rec in recs for f in lw.recording(Path(data) / rec)[::every] if f["depth"]]
    C = np.array([f["C"] for _, f, _ in frames])
    lo, hi = C[:, :2].min(0) - FAR_M - 0.5, C[:, :2].max(0) + FAR_M + 0.5
    w, h = (np.ceil((hi - lo) / TRUTH_CELL)).astype(int)

    # The floor: a plane fitted to the lowest flat layer, so a slight slope of the truth frame does
    # not turn far floor into "obstacles".
    low = np.concatenate([cloud_of(folder, f, cam, 8) for folder, f, cam in frames[::10]])
    plane = np.array([0.0, 0.0, FLOOR_Z[room]])
    for band in (0.15, 0.08, 0.04, 0.04):
        on = low[np.abs(low[:, 2] - (low[:, :2] @ plane[:2] + plane[2])) < band]
        plane = np.linalg.lstsq(np.c_[on[:, :2], np.ones(len(on))], on[:, 2], rcond=None)[0]
    tilt = float(np.degrees(np.arctan(np.hypot(plane[0], plane[1]))))
    log(f"{room}: {len(frames)} depth pictures from {len(recs)} recording(s); floor plane z = {plane[2]:.3f} m at the origin, "
        f"sloping {tilt:.2f} degrees, {len(on)} points within 4 cm of it")

    blocked, floor = np.zeros(w * h, np.int32), np.zeros(w * h, np.int32)
    for folder, f, cam in frames:
        pts = cloud_of(folder, f, cam)
        height = pts[:, 2] - (pts[:, :2] @ plane[:2] + plane[2])
        ij = np.floor((pts[:, :2] - lo) / TRUTH_CELL).astype(int)
        ok = (ij[:, 0] >= 0) & (ij[:, 0] < w) & (ij[:, 1] >= 0) & (ij[:, 1] < h)
        idx = ij[:, 1] * w + ij[:, 0]
        for sink, mask in ((blocked, (height > ANKLE_M) & (height < HEAD_M)), (floor, np.abs(height) < FLOOR_BAND_M)):
            cells, count = np.unique(idx[ok & mask], return_counts=True)
            sink[cells[count >= 2]] += 1  # this frame vouches for a cell with two points or more
    cls = np.zeros(w * h, np.uint8)
    cls[floor >= 3] = 1
    cls[blocked >= 3] = 2
    cls = cls.reshape(h, w)
    gap = cv2.distanceTransform((cls != 2).astype(np.uint8), cv2.DIST_L2, 5) * TRUTH_CELL  # metres to the nearest blocked cell
    seen = cls > 0
    log(f"  truth grid {w} x {h} cells of {TRUTH_CELL} m; of the {seen.sum() * TRUTH_CELL ** 2:.0f} square metres the Kinect measured, "
        f"{100 * (cls == 1).sum() / seen.sum():.0f}% are free floor and {100 * (cls == 2).sum() / seen.sum():.0f}% are blocked")
    return {"lo": lo, "cls": cls, "gap": gap, "plane": plane}


def look_up(truth, xy):
    """Class and gap to the nearest blocked cell at true positions (n, 2). Outside the grid: not measured."""
    ij = np.floor((xy - truth["lo"]) / TRUTH_CELL).astype(int)
    h, w = truth["cls"].shape
    ok = (ij[:, 0] >= 0) & (ij[:, 0] < w) & (ij[:, 1] >= 0) & (ij[:, 1] < h)
    cls, gap = np.zeros(len(xy), np.uint8), np.full(len(xy), 9.0, np.float32)
    cls[ok], gap[ok] = truth["cls"][ij[ok, 1], ij[ok, 0]], truth["gap"][ij[ok, 1], ij[ok, 0]]
    return cls, gap


# ----------------------------------------------------------------------------- one fix
class Fix:
    """One fix of the walking test, with the map frame of locate.py (as of commit 48813b7) and the
    fit of that frame to the truth."""

    def __init__(self, row, plan, fix_dir, rng):
        self.row, room = row, plan["rooms"][row["room"]]
        E = np.array(row["E"])
        self.nb = nb = row["n_baseline"]
        self.P = np.array([-e[:, :3].T @ e[:, 3] for e in E])  # camera centres in the pass's own frame
        up = -np.mean([E[q][1, :3] for q in range(nb)], 0)
        self.up = up / np.linalg.norm(up)
        north = E[0][2, :3] - (E[0][2, :3] @ self.up) * self.up
        self.north = north / np.linalg.norm(north)
        self.east = np.cross(self.north, self.up)
        self.metres = 2.0 / row["median_baseline_depth"]
        G = np.array([room["baseline"][i]["C"] for i in row["pick"]])
        self.s, self.R, self.t = lt.similarity(self.P[:nb], G)
        self.true_per_map = self.s / self.metres  # true metres per map metre
        self.places = {p["name"]: np.array(p["world"]) for p in room["places"]}
        self.cloud = np.load(fix_dir / ("_".join(str(v) for v in row["key"]) + ".npz"))["cloud"].astype(np.float64)
        self.cloud_map, self.cloud_true = self.to_map(self.cloud), self.cloud @ (self.s * self.R).T + self.t
        self.cameras = self.to_map(self.P[:nb])
        self.rng = rng

    def to_map(self, points):
        d = (np.atleast_2d(points) - self.P[0]) * self.metres
        return np.stack([d @ self.east, d @ self.north, d @ self.up], 1)

    def to_truth(self, en, up_m):
        """Map positions (east, north) at one map height -> true positions (x, y)."""
        en = np.atleast_2d(np.asarray(en, float))
        X = self.P[0] + (en[:, :1] * self.east + en[:, 1:2] * self.north + up_m * self.up) / self.metres
        return (X @ (self.s * self.R).T + self.t)[:, :2]

    def thinned(self, keep, n):
        if n and len(keep) > n:
            keep = keep[self.rng.choice(len(keep), n, replace=False)]
        return np.round(keep[:, :2], 2).tolist()

    def shipped_map(self):
        """The floor plan as locate.py makes it: heights between the 40th and 90th percentile, 1500 points."""
        low, high = np.percentile(self.cloud_map[:, 2], [40, 90])
        return self.thinned(self.cloud_map[(self.cloud_map[:, 2] >= low) & (self.cloud_map[:, 2] <= high)], 1500)

    def ideal_map(self, plane, n=1500):
        """The same model points with the height band a walker cares about (ankle to head above the
        TRUE floor). Needs the truth, so it is a yardstick for the floor plan, not something to ship.
        n=None keeps every point."""
        height = self.cloud_true[:, 2] - (self.cloud_true[:, :2] @ plane[:2] + plane[2])
        return self.thinned(self.cloud_map[(height > ANKLE_M) & (height < HEAD_M)], n)

    def own_floor(self):
        """The floor found in the model's own points (the lowest large flat surface, as
        locate_walk_tables.floor_plane finds it), and each point's height above it in metres when the
        baseline cameras are taken to be 1.6 m above the floor. None when no floor is found."""
        found = lt.floor_plane(self.cloud, self.up, 0.02 * self.row["median_baseline_depth"], self.rng)
        if found is None:
            return None
        normal, centre, _ = found
        eye = float(np.mean((self.P[:self.nb] - centre) @ normal))
        return (self.cloud - centre) @ normal / eye * 1.6 if eye > 1e-6 else None

    def own_floor_map(self, height, n=1500):
        """The walker's band WITHOUT the truth: something locate.py could send."""
        return None if height is None else self.thinned(self.cloud_map[(height > ANKLE_M) & (height < HEAD_M)], n)


def along(xy, step=0.02):
    """Points every `step` metres along a line of true positions, and how far along each one is."""
    out, far, at = [], [], 0.0
    for a, b in zip(xy, xy[1:]):
        length = float(np.linalg.norm(b - a))
        k = max(1, int(np.ceil(length / step)))
        f = np.arange(k + 1) / k
        out.append(a + (b - a) * f[:, None])
        far.append(at + length * f)
        at += length
    return (np.concatenate(out), np.concatenate(far), at) if out else (np.zeros((0, 2)), np.zeros(0), 0.0)


def judge(truth, xy, skip_end=0.0):
    """Lay a walked line on the true room. The first SKIP_START_M and the last skip_end metres are not judged."""
    pts, far, total = along(xy)
    part = (far >= SKIP_START_M) & (far <= total - skip_end)
    if not part.any():
        return None
    cls, gap = look_up(truth, pts[part])
    return {"blocked": float((cls == 2).mean()), "free": float((cls == 1).mean()), "unknown": float((cls == 0).mean()),
            "blocked_m": round(float((cls == 2).sum() * 0.02), 2), "closest_m": round(float(gap.min()), 2)}


def one_way(fix, truth, way, start, up_m, place_map):
    """Everything measured for one way: the route, and the straight line it replaces."""
    true_per_map = fix.true_per_map
    out = {"way": way["way"], "why": way["why"], "corners": way["corners"], "length_map": way["length_m"], "straight_map": way["straight_m"],
           "reach_map": way["reach_m"], "straight_true": way["straight_m"] * true_per_map}
    # The last stretch, the reach to the place, is judged for neither line: a place is on or behind something.
    reach_true = way["reach_m"] * true_per_map if way["way"] == "route" else 0.5
    out["straight"] = judge(truth, fix.to_truth([start[:2], place_map[:2]], up_m), skip_end=reach_true)
    if way["way"] == "route":
        walked = way["points"][:-1] if way["reach_m"] > 0 else way["points"]
        xy = fix.to_truth(walked, up_m)
        out["route"] = judge(truth, xy) if len(xy) > 1 else None
        out["end_true"] = xy[-1].tolist()
    return out


# ----------------------------------------------------------------------------- pictures
def picture(path, truth, fix, room_map, start, up_m, ways, title):
    """The true room from above (light: blocked, grey: free floor, dark: not measured), the floor plan's
    points (green), the baseline cameras (rings), the wearer (white) and the way to each place
    (thick: a route, thin: the straight arrow; ring: where the map puts the place, cross: where it truly is)."""
    pts = fix.to_truth(room_map["points"], up_m) if room_map["points"] else np.zeros((0, 2))
    box = np.concatenate([pts, fix.to_truth([start[:2]], up_m)] + [fix.to_truth(w["points"], up_m) for w in ways.values()])
    lo, hi = np.percentile(box, 1, axis=0) - 0.8, np.percentile(box, 99, axis=0) + 0.8
    px = 110  # pixels per metre

    def at(xy):
        d = (np.atleast_2d(xy) - lo) * px
        return np.stack([d[:, 0], (hi[1] - lo[1]) * px - d[:, 1]], 1).astype(np.int32)

    size = ((hi - lo) * px).astype(int)
    ys, xs = np.mgrid[0:size[1], 0:size[0]]
    c, _ = look_up(truth, np.stack([xs.ravel() / px + lo[0], (size[1] - ys.ravel()) / px + lo[1]], 1))
    img = np.array([(25, 25, 25), (85, 85, 85), (215, 215, 215)], np.uint8)[c].reshape(size[1], size[0], 3).copy()
    for p in at(pts):
        cv2.circle(img, tuple(int(v) for v in p), 2, (60, 200, 60), -1)
    cams = at(fix.to_truth(room_map["cameras"], up_m))
    cv2.polylines(img, [cams.reshape(-1, 1, 2)], False, (200, 120, 40), 1, cv2.LINE_AA)
    for p in cams:
        cv2.circle(img, tuple(int(v) for v in p), 4, (200, 120, 40), 1)
    colours = [(0, 220, 255), (255, 160, 0), (255, 0, 255), (0, 140, 255), (255, 255, 0)]
    for k, (name, w) in enumerate(ways.items()):
        colour = colours[k % len(colours)]
        line = at(fix.to_truth(w["points"], up_m))
        cv2.polylines(img, [line.reshape(-1, 1, 2)], False, colour, 2 if w["way"] == "route" else 1, cv2.LINE_AA)
        cv2.circle(img, tuple(int(v) for v in line[-1]), 7, colour, 2)
        cv2.drawMarker(img, tuple(int(v) for v in at(fix.places[name][:2])[0]), colour, cv2.MARKER_TILTED_CROSS, 12, 2)
    cv2.circle(img, tuple(int(v) for v in at(fix.to_truth([start[:2]], up_m))[0]), 6, (255, 255, 255), -1)
    cv2.putText(img, title, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.imwrite(str(path), img)


# ----------------------------------------------------------------------------- tables
def pct(k, n):
    return f"{k} of {n} ({100 * k / n:.0f}%)" if n else "-"


def q(values, digits=2):
    values = [v for v in values if v is not None]
    return " / ".join(f"{np.percentile(values, p):.{digits}f}" for p in (50, 90, 100)) if values else "-"


def mean_pct(values):
    return f"{100 * np.mean(values):.0f}%" if len(values) else "-"


def shown(r):
    """What the display would lead the wearer along: the route, or the straight line when there is none."""
    return r["route"] if r["way"] == "route" and r.get("route") else r["straight"]


def clear(m):
    return m["blocked_m"] <= 0.1


def worth(rows, free_start=False):
    """The ways that are judged: the place is truly WORTH_M away or more, and the straight line could be laid on the truth."""
    return [r for r in rows if r["straight_true"] >= WORTH_M and r.get("straight") and
            (not free_start or (r["start_class"] == 1 and r["start_gap"] >= 0.2))]


def summary(results, rooms, free_start=False):
    """One line per setting. Everything is about what the wearer is shown: the route, else the straight line."""
    head = ["Setting", "Ways judged", "A route was found", "What is shown is clear: all rooms"] + rooms + \
           ["Truly blocked share of what is shown, mean", "Never measured share, mean", "Route length / straight line: median / 90th / longest",
            "Corners per route: median / 90th / most"]
    L = ["| " + " | ".join(head) + " |", "| --- |" + " --- |" * (len(head) - 1)]
    first = worth(results["default settings"], free_start)
    cells = ["today: the straight arrow", str(len(first)), "-", pct(sum(clear(r["straight"]) for r in first), len(first))]
    cells += [pct(sum(clear(r["straight"]) for r in first if r["room"] == room), sum(r["room"] == room for r in first)) for room in rooms]
    cells += [mean_pct([r["straight"]["blocked"] for r in first]), mean_pct([r["straight"]["unknown"] for r in first]), "1.00", "-"]
    L.append("| " + " | ".join(cells) + " |")
    for name, rows in results.items():
        g = worth(rows, free_start)
        if not g:
            continue
        routes = [r for r in g if r["way"] == "route" and r.get("route")]
        cells = [name, str(len(g)), pct(len(routes), len(g)), pct(sum(clear(shown(r)) for r in g), len(g))]
        cells += [pct(sum(clear(shown(r)) for r in g if r["room"] == room), sum(r["room"] == room for r in g)) for room in rooms]
        cells += [mean_pct([shown(r)["blocked"] for r in g]), mean_pct([shown(r)["unknown"] for r in g]),
                  q([r["length_map"] / r["straight_map"] for r in routes]), q([r["corners"] for r in routes], 0)]
        L.append("| " + " | ".join(cells) + " |")
    return L + [""]


def table(rows, rooms, name, free_start=False):
    """The full block for one setting: per room and for all rooms together."""
    L = [f"### {name}", "", "| | " + " | ".join(rooms + ["All rooms"]) + " |", "| --- |" + " --- |" * (len(rooms) + 1)]
    groups = [[r for r in rows if r["room"] == room] for room in rooms] + [rows]

    def line(label, f, of=groups):
        L.append(f"| {label} | " + " | ".join(f(g) for g in of) + " |")

    line("Ways asked for", lambda g: str(len(g)))
    line("A route was found", lambda g: pct(sum(r["way"] == "route" for r in g), len(g)))
    for why in sorted({r["why"] for r in rows if r["why"]}):
        line(f"Straight arrow: {why}", lambda g, why=why: pct(sum(r["why"] == why for r in g), len(g)))
    far = [worth(g, free_start) for g in groups]
    line(f"Ways judged (place truly {WORTH_M:.0f} m away or more)", lambda g: str(len(g)), far)
    line("What is shown is clear (at most 0.1 m of it truly blocked)", lambda g: pct(sum(clear(shown(r)) for r in g), len(g)), far)
    line("Today's straight arrow is clear", lambda g: pct(sum(clear(r["straight"]) for r in g), len(g)), far)
    line("What is shown never enters a truly blocked cell", lambda g: pct(sum(shown(r)["blocked_m"] == 0 for r in g), len(g)), far)
    line("Today's straight arrow never does", lambda g: pct(sum(r["straight"]["blocked_m"] == 0 for r in g), len(g)), far)
    line(f"A body fits all the way along what is shown ({HALF_BODY_M} m clear each side)", lambda g: pct(sum(shown(r)["closest_m"] >= HALF_BODY_M for r in g), len(g)), far)
    line("A body fits all the way along the straight arrow", lambda g: pct(sum(r["straight"]["closest_m"] >= HALF_BODY_M for r in g), len(g)), far)
    line("Truly blocked share of what is shown, mean", lambda g: mean_pct([shown(r)["blocked"] for r in g]), far)
    line("Truly blocked share of the straight arrow, mean", lambda g: mean_pct([r["straight"]["blocked"] for r in g]), far)
    line("Share of what is shown over floor the Kinect saw, mean", lambda g: mean_pct([shown(r)["free"] for r in g]), far)
    line("Share of what is shown never measured, mean", lambda g: mean_pct([shown(r)["unknown"] for r in g]), far)
    routes = [[r for r in g if r["way"] == "route" and r.get("route")] for g in far]
    line("Routes among the ways judged", lambda g: str(len(g)), routes)
    line("Route is clear", lambda g: pct(sum(clear(r["route"]) for r in g), len(g)), routes)
    line("Route length / straight line: median / 90th / longest", lambda g: q([r["length_map"] / r["straight_map"] for r in g]), routes)
    line("Extra metres walked (true): median / 90th / most", lambda g: q([(r["length_map"] - r["straight_map"]) * r["true_per_map"] for r in g]), routes)
    line("Routes with at least one corner", lambda g: pct(sum(r["corners"] >= 1 for r in g), len(g)), routes)
    line("Corners per route: median / 90th / most", lambda g: q([r["corners"] for r in g], 0), routes)
    line("Route's end to the true place, true m: median / 90th / farthest", lambda g: q([r.get("end_to_place") for r in g]), routes)
    hard = [[r for r in g if not clear(r["straight"])] for g in far]
    easy = [[r for r in g if clear(r["straight"])] for g in far]
    line("Straight arrow truly blocked: what is shown is clear", lambda g: pct(sum(clear(shown(r)) for r in g), len(g)), hard)
    line("Straight arrow truly clear: what is shown is clear too", lambda g: pct(sum(clear(shown(r)) for r in g), len(g)), easy)
    return L + [""]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--walk", required=True, help="the walking test's folder: out/plan.json, out/results.jsonl, out/fix/, data/tum/")
    ap.add_argument("--out", required=True)
    ap.add_argument("--every", type=int, default=2, help="use every n-th depth picture for the truth")
    ap.add_argument("--every-recording", action="store_true", help="truth from every recording of the room, not the baseline recording alone")
    ap.add_argument("--pictures", type=int, default=0, help="pictures per room and drawn setting")
    ap.add_argument("--draw", default="", help="with --pictures: more settings to draw, by name, separated by ';'")
    ap.add_argument("--limit", type=int, default=0, help="only this many fixes per room (a quick try)")
    ap.add_argument("--detail", default="", help="more settings to print the full table for, by name, separated by ';'")
    args = ap.parse_args()
    walk, out = Path(args.walk), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    lines = []

    def log(text):
        print(text, flush=True)
        lines.append(text)

    plan = json.loads((walk / "out" / "plan.json").read_text())
    rows = [json.loads(line) for line in (walk / "out" / "results.jsonl").read_text().splitlines() if line.strip()]
    rows = [r for r in rows if r.get("kind") == "same" and r["key"][1] == lw.N_USED and r["n_new"] == 1 and "error" not in r]
    rooms = list(plan["rooms"])
    log(f"{len(rows)} same-room fixes with 12 baseline frames + 1 query frame; found {sum(r['state'] == 'found' for r in rows)}, "
        f"unsure {sum(r['state'] == 'unsure' for r in rows)}, lost {sum(r['state'] == 'lost' for r in rows)} (a lost fix gets no route)")
    log("truth: " + ("every recording of each room" if args.every_recording else "the baseline recording of each room"))
    truths = {room: true_room(walk / "data" / "tum", room, args.every, log, args.every_recording) for room in rooms}

    results = {name: [] for name in list(SETTINGS) + [FROM_CAMERAS]}
    draw = ["default settings"] + [d for d in args.draw.split(";") if d]
    times, sizes, drawn, per_room, no_floor = [], defaultdict(list), Counter(), Counter(), 0
    rng = np.random.default_rng(0)
    t_start = time.time()
    for row in rows:
        if row["state"] == "lost" or (args.limit and per_room[row["room"]] >= args.limit):
            continue
        per_room[row["room"]] += 1
        truth = truths[row["room"]]
        fix = Fix(row, plan, walk / "out" / "fix", rng)
        base = {"state": row["state"], "heading_deg": row["heading_deg"], "waypoints": row["waypoints"]}
        cameras = np.round(fix.cameras[:, :2], 2).tolist()
        height = fix.own_floor()
        no_floor += height is None
        maps = {"shipped": fix.shipped_map(), "ideal": fix.ideal_map(truth["plane"]), "ideal dense": fix.ideal_map(truth["plane"], None),
                "own floor": fix.own_floor_map(height), "own floor dense": fix.own_floor_map(height, None)}
        for kind, pts in maps.items():
            if pts is not None:
                sizes[kind].append(len(pts))
        up_m = row["position"][2]
        def run(name, kind, kw, start, at_height):
            points = maps[kind] if maps[kind] is not None else []  # no floor found: no floor plan, so the straight arrow
            start_cls, start_gap = look_up(truth, fix.to_truth([start[:2]], at_height))
            common = {"room": row["room"], "query": row["query"], "state": row["state"], "true_per_map": fix.true_per_map,
                      "start_class": int(start_cls[0]), "start_gap": float(start_gap[0])}
            got = route.plan({**base, "position": list(start), "map": {"points": points, "cameras": cameras}}, **kw)
            for w in base["waypoints"]:
                way = got["to"].get(w["name"])
                if way is None:
                    continue
                m = one_way(fix, truth, way, start, at_height, w["position"])
                if "end_true" in m:
                    m["end_to_place"] = float(np.linalg.norm(np.array(m.pop("end_true")) - fix.places[w["name"]][:2]))
                results[name].append({**common, "place": w["name"], **m})
            return got, points

        for name, (kind, kw) in SETTINGS.items():
            got, points = run(name, kind, kw, row["position"], up_m)
            if name == "default settings":
                times.append(got["ms"])
            if name in draw and drawn[(row["room"], name)] < args.pictures and per_room[row["room"]] % 7 == 1:
                drawn[(row["room"], name)] += 1
                tag = "".join(c if c.isalnum() else "_" for c in name)[:44]
                picture(out / f"route_{row['room']}_{row['query']}_{tag}.png", truth, fix, {"points": points, "cameras": cameras},
                        row["position"], up_m, got["to"], f"{row['room']} query {row['query']}: {name}")
        if per_room[row["room"]] % 8 == 1:  # longer walks: start where each baseline camera stood (a spot someone truly stood at)
            for cam in fix.cameras:
                run(FROM_CAMERAS, "shipped", {}, [float(v) for v in cam], float(cam[2]))
    log(f"routes worked out in {time.time() - t_start:.0f} s; route.plan per fix (five places, default settings): median {np.median(times):.0f} ms, "
        f"90th percentile {np.percentile(times, 90):.0f} ms, slowest {max(times)} ms")
    log("points per floor plan, median: " + ", ".join(f"{kind} {int(np.median(v))}" for kind, v in sizes.items()) + f"; no floor found in {no_floor} fixes")
    starts = list({(r["room"], r["query"]): r for r in results["default settings"]}.values())
    log("where the wearer truly stood: " + ", ".join(f"{label} {sum(s['start_class'] == c for s in starts)}" for c, label in
                                                    ((1, "free floor"), (2, "over something (the Kinect is held out over a desk)"), (0, "not measured"))))
    scale = defaultdict(list)
    for s in starts:
        scale[s["room"]].append(1 / s["true_per_map"])
    log("map metres per true metre, median: " + ", ".join(f"{room} {np.median(v):.2f}" for room, v in scale.items()))
    md = ["## Every setting, every start", ""] + summary(results, rooms)
    md += ["## Every setting, only starts that truly stand on free floor with 0.2 m to spare", ""] + summary(results, rooms, True)
    for name in ["default settings"] + [d for d in args.detail.split(";") if d] + [FROM_CAMERAS]:
        if name in results:
            md += table(results[name], rooms, name)
            md += table(results[name], rooms, name + ", only starts on free floor", True)
    (out / "route_eval.md").write_text("\n".join(lines + [""] + md))
    (out / "route_eval.json").write_text(json.dumps(results))
    print("\n".join(md))
    print(f"wrote {out / 'route_eval.md'} and route_eval.json")


if __name__ == "__main__":
    main()
