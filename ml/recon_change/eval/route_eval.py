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

BRUSH_M = 0.15              # a line that is never deeper than this inside something only brushes its edge

# What is tried: (name, which up, which floor plan, settings of route.build). The first is route.py as it is.
# Up: "" is locate.py's own (the average of the baseline cameras, tilted 8 to 19 degrees in these rooms);
# "upright" is the normal of the floor found in the model's own points, as a stand-in for the better up of
# position fix v2 (pull request 28).
SETTINGS = [
    ("default settings", "", "shipped", {}),
    ("lone points do not count", "", "shipped", {"keep_lone": False}),
    ("unseen floor costs 0.5", "", "shipped", {"unseen_cost": 0.5}),
    ("unseen floor costs 5", "", "shipped", {"unseen_cost": 5.0}),
    ("unseen floor costs 10", "", "shipped", {"unseen_cost": 10.0}),
    ("only within 1.0 m of the walked line", "", "shipped", {"corridor": 1.0}),
    ("only within 0.6 m of the walked line", "", "shipped", {"corridor": 0.6}),
    ("no extra cost near things", "", "shipped", {"near_cost": 0.0}),
    ("body 0.4 m", "", "shipped", {"body": 0.4}),
    ("body 0.8 m", "", "shipped", {"body": 0.8}),
    ("own floor: 1500 points", "", "own floor", {}),
    ("own floor: every point", "", "own floor dense", {}),
    ("true floor: 1500 points", "", "ideal", {}),
    ("true floor: every point", "", "ideal dense", {}),
    ("upright: today's floor plan", "upright", "shipped", {}),
    ("upright, own floor: 1500 points", "upright", "own floor", {}),
    ("upright, own floor: every point", "upright", "own floor dense", {}),
    ("upright, own floor: every point, body 0.8 m", "upright", "own floor dense", {"body": 0.8}),
    ("upright, own floor: every point, only within 1.0 m", "upright", "own floor dense", {"corridor": 1.0}),
    ("upright, true floor: every point", "upright", "ideal dense", {}),
]
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
    deep = cv2.distanceTransform((cls == 2).astype(np.uint8), cv2.DIST_L2, 5) * TRUTH_CELL  # metres inside something blocked
    walked = np.array([f["C"][:2] for f in lw.recording(Path(data) / spec["baseline"])])  # where the Kinect was truly carried
    return {"lo": lo, "cls": cls, "gap": gap, "deep": deep, "plane": plane, "walked": walked}


def look_up(truth, xy):
    """At true positions (n, 2): the class, the gap to the nearest blocked cell, and how deep inside
    something blocked. Outside the grid: not measured."""
    ij = np.floor((xy - truth["lo"]) / TRUTH_CELL).astype(int)
    h, w = truth["cls"].shape
    ok = (ij[:, 0] >= 0) & (ij[:, 0] < w) & (ij[:, 1] >= 0) & (ij[:, 1] < h)
    cls, gap, deep = np.zeros(len(xy), np.uint8), np.full(len(xy), 9.0, np.float32), np.zeros(len(xy), np.float32)
    cls[ok], gap[ok], deep[ok] = truth["cls"][ij[ok, 1], ij[ok, 0]], truth["gap"][ij[ok, 1], ij[ok, 0]], truth["deep"][ij[ok, 1], ij[ok, 0]]
    return cls, gap, deep


# ----------------------------------------------------------------------------- one fix
class Fix:
    """One fix of the walking test: the model's cameras and points, the fit of the model's frame to
    the truth, and the heights of the points above the floor (the true one, and the one found in the
    model's own points)."""

    def __init__(self, row, plan, fix_dir, rng, plane):
        self.row, room = row, plan["rooms"][row["room"]]
        E = np.array(row["E"])
        self.nb = nb = row["n_baseline"]
        self.P = np.array([-e[:, :3].T @ e[:, 3] for e in E])  # camera centres in the pass's own frame
        self.forward0 = E[0][2, :3]
        up = -np.mean([E[q][1, :3] for q in range(nb)], 0)  # locate.py at commit 48813b7: the cameras' average
        self.camera_up = up / np.linalg.norm(up)
        self.metres = 2.0 / row["median_baseline_depth"]
        G = np.array([room["baseline"][i]["C"] for i in row["pick"]])
        self.s, self.R, self.t = lt.similarity(self.P[:nb], G)
        self.true_per_map = self.s / self.metres  # true metres per map metre
        self.places = {p["name"]: np.array(p["world"]) for p in room["places"]}
        self.cloud = np.load(fix_dir / ("_".join(str(v) for v in row["key"]) + ".npz"))["cloud"].astype(np.float64)
        self.rng = rng
        true = self.cloud @ (self.s * self.R).T + self.t
        self.true_height = true[:, 2] - (true[:, :2] @ plane[:2] + plane[2])
        # The floor in the model's own points: the lowest large flat surface (locate_walk_tables.floor_plane).
        # Heights above it in metres when the baseline cameras are taken to be 1.6 m above the floor.
        self.floor_up, self.own_height = None, None
        found = lt.floor_plane(self.cloud, self.camera_up, 0.02 * row["median_baseline_depth"], rng)
        if found is not None:
            normal, centre, _ = found
            eye = float(np.mean((self.P[:nb] - centre) @ normal))
            if eye > 1e-6:
                self.floor_up, self.own_height = normal, (self.cloud - centre) @ normal / eye * 1.6

    def mask(self, kind):
        """Which model points a floor plan of this kind keeps (None: the view's own 40th to 90th percentile)."""
        height = {"shipped": None, "own floor": self.own_height, "own floor dense": self.own_height,
                  "ideal": self.true_height, "ideal dense": self.true_height}[kind]
        return None if height is None else (height > ANKLE_M) & (height < HEAD_M)


class View:
    """A map of one fix: east, north and up as locate.py lays them out for a given up direction."""

    def __init__(self, fix, up):
        self.fix, self.up = fix, up / np.linalg.norm(up)
        north = fix.forward0 - (fix.forward0 @ self.up) * self.up
        self.north = north / np.linalg.norm(north)
        self.east = np.cross(self.north, self.up)
        self.cloud_map = self.to_map(fix.cloud)
        self.cameras = self.to_map(fix.P[:fix.nb])
        self.position = [float(v) for v in self.to_map(fix.P[fix.nb])[0]]  # the wearer: the one query camera

    def to_map(self, points):
        d = (np.atleast_2d(points) - self.fix.P[0]) * self.fix.metres
        return np.stack([d @ self.east, d @ self.north, d @ self.up], 1)

    def from_map(self, positions):
        p = np.atleast_2d(np.asarray(positions, float)) / self.fix.metres
        return self.fix.P[0] + p[:, :1] * self.east + p[:, 1:2] * self.north + p[:, 2:3] * self.up

    def to_truth(self, en, up_m):
        """Map positions (east, north) at one map height -> true positions (x, y)."""
        en = np.atleast_2d(np.asarray(en, float))
        X = self.from_map(np.c_[en[:, :2], np.full(len(en), up_m)])
        return (X @ (self.fix.s * self.fix.R).T + self.fix.t)[:, :2]

    def floor_plan(self, kind):
        """The points a fix would carry: locate.py's rule (heights between the 40th and 90th
        percentile, 1500 points), or a band of real heights, 1500 points or every one ("dense")."""
        keep = self.fix.mask(kind)
        if kind == "shipped":
            low, high = np.percentile(self.cloud_map[:, 2], [40, 90])
            keep = (self.cloud_map[:, 2] >= low) & (self.cloud_map[:, 2] <= high)
        if keep is None:
            return None
        pts = self.cloud_map[keep]
        if not kind.endswith("dense") and len(pts) > 1500:
            pts = pts[self.fix.rng.choice(len(pts), 1500, replace=False)]
        return np.round(pts[:, :2], 2).tolist()


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
    cls, gap, deep = look_up(truth, pts[part])
    return {"blocked": float((cls == 2).mean()), "free": float((cls == 1).mean()), "unknown": float((cls == 0).mean()),
            "blocked_m": round(float((cls == 2).sum() * 0.02), 2), "closest_m": round(float(gap.min()), 2),
            "deep_m": round(float(deep.max()), 2)}


def true_walk(truth, stretch=2.0, step=0.5):
    """The yardstick's own ceiling. The Kinect was truly carried along its recorded path, so a
    person truly got through there. Cut that path into stretches and judge each like a route: what
    is not clear here is the hand-held camera passing over an edge, or the truth being too fat.
    The stretches are `stretch` metres long and start every `step` metres, so they overlap."""
    xy = truth["walked"]
    far = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(xy, axis=0), axis=1))]
    out = []
    for k in range(int(max(0.0, far[-1] - stretch) // step) + 1):
        piece = xy[(far >= k * step) & (far < k * step + stretch)]
        m = judge(truth, piece[::3]) if len(piece) > 6 else None
        if m:
            out.append(m)
    return out


def one_way(view, truth, way, start, up_m, place_map):
    """Everything measured for one way: the route, and the straight line it replaces."""
    true_per_map = view.fix.true_per_map
    out = {"way": way["way"], "why": way["why"], "corners": way["corners"], "length_map": way["length_m"], "straight_map": way["straight_m"],
           "reach_map": way["reach_m"], "straight_true": way["straight_m"] * true_per_map}
    # The last stretch, the reach to the place, is judged for neither line: a place is on or behind something.
    reach_true = way["reach_m"] * true_per_map if way["way"] == "route" else 0.5
    out["straight"] = judge(truth, view.to_truth([start[:2], place_map[:2]], up_m), skip_end=reach_true)
    if way["way"] == "route":
        walked = way["points"][:-1] if way["reach_m"] > 0 else way["points"]
        xy = view.to_truth(walked, up_m)
        out["route"] = judge(truth, xy) if len(xy) > 1 else None
        out["end_true"] = xy[-1].tolist()
    return out


# ----------------------------------------------------------------------------- pictures
def picture(path, truth, view, room_map, start, up_m, ways, title):
    """The true room from above (light: blocked, grey: free floor, dark: not measured), the floor plan's
    points (green), the baseline cameras (rings), the wearer (white) and the way to each place
    (thick: a route, thin: the straight arrow; ring: where the map puts the place, cross: where it truly is)."""
    pts = view.to_truth(room_map["points"], up_m) if room_map["points"] else np.zeros((0, 2))
    box = np.concatenate([pts, view.to_truth([start[:2]], up_m)] + [view.to_truth(w["points"], up_m) for w in ways.values()])
    lo, hi = np.percentile(box, 1, axis=0) - 0.8, np.percentile(box, 99, axis=0) + 0.8
    px = 110  # pixels per metre

    def at(xy):
        d = (np.atleast_2d(xy) - lo) * px
        return np.stack([d[:, 0], (hi[1] - lo[1]) * px - d[:, 1]], 1).astype(np.int32)

    size = ((hi - lo) * px).astype(int)
    ys, xs = np.mgrid[0:size[1], 0:size[0]]
    c = look_up(truth, np.stack([xs.ravel() / px + lo[0], (size[1] - ys.ravel()) / px + lo[1]], 1))[0]
    img = np.array([(25, 25, 25), (85, 85, 85), (215, 215, 215)], np.uint8)[c].reshape(size[1], size[0], 3).copy()
    for p in at(pts):
        cv2.circle(img, tuple(int(v) for v in p), 2, (60, 200, 60), -1)
    cams = at(view.to_truth(room_map["cameras"], up_m))
    cv2.polylines(img, [cams.reshape(-1, 1, 2)], False, (200, 120, 40), 1, cv2.LINE_AA)
    for p in cams:
        cv2.circle(img, tuple(int(v) for v in p), 4, (200, 120, 40), 1)
    colours = [(0, 220, 255), (255, 160, 0), (255, 0, 255), (0, 140, 255), (255, 255, 0)]
    for k, (name, w) in enumerate(ways.items()):
        colour = colours[k % len(colours)]
        line = at(view.to_truth(w["points"], up_m))
        cv2.polylines(img, [line.reshape(-1, 1, 2)], False, colour, 2 if w["way"] == "route" else 1, cv2.LINE_AA)
        cv2.circle(img, tuple(int(v) for v in line[-1]), 7, colour, 2)
        cv2.drawMarker(img, tuple(int(v) for v in at(view.fix.places[name][:2])[0]), colour, cv2.MARKER_TILTED_CROSS, 12, 2)
    cv2.circle(img, tuple(int(v) for v in at(view.to_truth([start[:2]], up_m))[0]), 6, (255, 255, 255), -1)
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


def through(m):
    """Goes through something: deeper than a brush against its edge."""
    return m["deep_m"] > BRUSH_M


def worth(rows, free_start=False):
    """The ways that are judged: the place is truly WORTH_M away or more, and the straight line could be laid on the truth."""
    return [r for r in rows if r["straight_true"] >= WORTH_M and r.get("straight") and
            (not free_start or (r["start_class"] == 1 and r["start_gap"] >= 0.2))]


def summary(results, rooms, free_start=False):
    """One line per setting. Everything is about what the wearer is shown: the route, else the straight line."""
    head = ["Setting", "Ways judged", "A route was found", "What is shown is clear: all rooms"] + rooms + \
           ["It goes through something: all rooms"] + rooms + \
           ["Truly blocked share of what is shown, mean", "Route length / straight line: median / 90th / longest", "Corners per route: median / 90th / most"]
    L = ["| " + " | ".join(head) + " |", "| --- |" + " --- |" * (len(head) - 1)]

    def cells_for(g, pick):
        out = [pct(sum(clear(pick(r)) for r in g), len(g))]
        out += [pct(sum(clear(pick(r)) for r in g if r["room"] == room), sum(r["room"] == room for r in g)) for room in rooms]
        out += [pct(sum(through(pick(r)) for r in g), len(g))]
        out += [pct(sum(through(pick(r)) for r in g if r["room"] == room), sum(r["room"] == room for r in g)) for room in rooms]
        return out + [mean_pct([pick(r)["blocked"] for r in g])]

    first = worth(results["default settings"], free_start)
    L.append("| " + " | ".join(["today: the straight arrow", str(len(first)), "-"] + cells_for(first, lambda r: r["straight"]) + ["1.00", "-"]) + " |")
    for name, rows in results.items():
        g = worth(rows, free_start)
        if not g:
            continue
        routes = [r for r in g if r["way"] == "route" and r.get("route")]
        L.append("| " + " | ".join([name, str(len(g)), pct(len(routes), len(g))] + cells_for(g, shown) +
                                   [q([r["length_map"] / r["straight_map"] for r in routes]), q([r["corners"] for r in routes], 0)]) + " |")
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
    line(f"What is shown brushes an edge (not clear, never more than {BRUSH_M} m inside)", lambda g: pct(sum(not clear(shown(r)) and not through(shown(r)) for r in g), len(g)), far)
    line("Today's straight arrow brushes an edge", lambda g: pct(sum(not clear(r["straight"]) and not through(r["straight"]) for r in g), len(g)), far)
    line(f"What is shown goes through something (more than {BRUSH_M} m inside)", lambda g: pct(sum(through(shown(r)) for r in g), len(g)), far)
    line("Today's straight arrow goes through something", lambda g: pct(sum(through(r["straight"]) for r in g), len(g)), far)
    line("Deepest point inside something, what is shown, m: median / 90th / deepest", lambda g: q([shown(r)["deep_m"] for r in g]), far)
    line("Deepest point inside something, straight arrow, m: median / 90th / deepest", lambda g: q([r["straight"]["deep_m"] for r in g]), far)
    line(f"A body fits all the way along what is shown ({HALF_BODY_M} m clear each side)", lambda g: pct(sum(shown(r)["closest_m"] >= HALF_BODY_M for r in g), len(g)), far)
    line("A body fits all the way along the straight arrow", lambda g: pct(sum(r["straight"]["closest_m"] >= HALF_BODY_M for r in g), len(g)), far)
    line("Truly blocked share of what is shown, mean", lambda g: mean_pct([shown(r)["blocked"] for r in g]), far)
    line("Truly blocked share of the straight arrow, mean", lambda g: mean_pct([r["straight"]["blocked"] for r in g]), far)
    line("Share of what is shown over floor the Kinect saw, mean", lambda g: mean_pct([shown(r)["free"] for r in g]), far)
    line("Share of what is shown never measured, mean", lambda g: mean_pct([shown(r)["unknown"] for r in g]), far)
    routes = [[r for r in g if r["way"] == "route" and r.get("route")] for g in far]
    line("Routes among the ways judged", lambda g: str(len(g)), routes)
    line("Route is clear", lambda g: pct(sum(clear(r["route"]) for r in g), len(g)), routes)
    line("Route goes through something", lambda g: pct(sum(through(r["route"]) for r in g), len(g)), routes)
    line("Route length / straight line: median / 90th / longest", lambda g: q([r["length_map"] / r["straight_map"] for r in g]), routes)
    line("Extra metres walked (true): median / 90th / most", lambda g: q([(r["length_map"] - r["straight_map"]) * r["true_per_map"] for r in g]), routes)
    line("Routes with at least one corner", lambda g: pct(sum(r["corners"] >= 1 for r in g), len(g)), routes)
    line("Corners per route: median / 90th / most", lambda g: q([r["corners"] for r in g], 0), routes)
    line("Route's end to the true place, true m: median / 90th / farthest", lambda g: q([r.get("end_to_place") for r in g]), routes)
    hard = [[r for r in g if not clear(r["straight"])] for g in far]
    easy = [[r for r in g if clear(r["straight"])] for g in far]
    line("Straight arrow not clear: what is shown is clear", lambda g: pct(sum(clear(shown(r)) for r in g), len(g)), hard)
    line("Straight arrow clear: what is shown is clear too", lambda g: pct(sum(clear(shown(r)) for r in g), len(g)), easy)
    worse = [[r for r in g if not through(r["straight"])] for g in far]
    line("Straight arrow does not go through anything: what is shown does", lambda g: pct(sum(through(shown(r)) for r in g), len(g)), worse)
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

    walks = {room: true_walk(truths[room]) for room in rooms}
    for room, w in walks.items():
        log(f"  {room}: the path the Kinect was truly carried along, in {len(w)} overlapping stretches of 2 m: clear {pct(sum(clear(m) for m in w), len(w))}, "
            f"brushes an edge {pct(sum(not clear(m) and not through(m) for m in w), len(w))}, goes through something {pct(sum(through(m) for m in w), len(w))}, "
            f"truly blocked share {mean_pct([m['blocked'] for m in w])}, never measured share {mean_pct([m['unknown'] for m in w])}")
    results = {name: [] for name, _, _, _ in SETTINGS}
    results[FROM_CAMERAS] = []
    draw = ["default settings"] + [d for d in args.draw.split(";") if d]
    times, sizes, drawn, per_room, no_floor, drift = [], defaultdict(list), Counter(), Counter(), 0, 0.0
    rng = np.random.default_rng(0)
    t_start = time.time()
    for row in rows:
        if row["state"] == "lost" or (args.limit and per_room[row["room"]] >= args.limit):
            continue
        per_room[row["room"]] += 1
        truth = truths[row["room"]]
        fix = Fix(row, plan, walk / "out" / "fix", rng, truth["plane"])
        no_floor += fix.floor_up is None
        own = View(fix, fix.camera_up)
        drift = max(drift, float(np.abs(np.array(own.position) - np.array(row["position"])).max()))  # this script's map against locate.py's
        # No floor found in the model's points: the upright map falls back to locate.py's own up, and the
        # "own floor" plans have no points, so those settings answer with the straight arrow.
        views = {"": own, "upright": View(fix, fix.floor_up) if fix.floor_up is not None else own}
        places = {key: [{"name": w["name"], "position": [float(v) for v in view.to_map(own.from_map([w["position"]]))[0]]}
                        for w in row["waypoints"]] for key, view in views.items()}
        plans = {}

        def run(name, key, kind, kw, start=None):
            view = views[key]
            if (key, kind) not in plans:
                plans[(key, kind)] = view.floor_plan(kind) or []
                sizes[kind].append(len(plans[(key, kind)]))
            points, cameras = plans[(key, kind)], np.round(view.cameras[:, :2], 2).tolist()
            start = view.position if start is None else start
            start_cls, start_gap, _ = look_up(truth, view.to_truth([start[:2]], start[2]))
            common = {"room": row["room"], "query": row["query"], "state": row["state"], "true_per_map": fix.true_per_map,
                      "start_class": int(start_cls[0]), "start_gap": float(start_gap[0])}
            got = route.plan({"state": row["state"], "heading_deg": 0, "position": list(start), "waypoints": places[key],
                              "map": {"points": points, "cameras": cameras}}, **kw)
            for w in places[key]:
                way = got["to"].get(w["name"])
                if way is None:
                    continue
                m = one_way(view, truth, way, start, start[2], w["position"])
                if "end_true" in m:
                    m["end_to_place"] = float(np.linalg.norm(np.array(m.pop("end_true")) - fix.places[w["name"]][:2]))
                results[name].append({**common, "place": w["name"], **m})
            return got, view, {"points": points, "cameras": cameras}, start

        for name, key, kind, kw in SETTINGS:
            got, view, room_map, start = run(name, key, kind, kw)
            if name == "default settings":
                times.append(got["ms"])
            if name in draw and drawn[(row["room"], name)] < args.pictures and per_room[row["room"]] % 7 == 1:
                drawn[(row["room"], name)] += 1
                tag = "".join(c if c.isalnum() else "_" for c in name)[:44]
                picture(out / f"route_{row['room']}_{row['query']}_{tag}.png", truth, view, room_map, start, start[2], got["to"],
                        f"{row['room']} query {row['query']}: {name}")
        if per_room[row["room"]] % 8 == 1:  # longer walks: start where each baseline camera stood (a spot someone truly stood at)
            for cam in own.cameras:
                run(FROM_CAMERAS, "", "shipped", {}, [float(v) for v in cam])
    log(f"routes worked out in {time.time() - t_start:.0f} s; route.plan per fix (five places, default settings): median {np.median(times):.0f} ms, "
        f"90th percentile {np.percentile(times, 90):.0f} ms, slowest {max(times)} ms")
    log(f"the wearer's position on the map as this script rebuilds it and as locate.py gave it: at most {drift:.3f} m apart")
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
