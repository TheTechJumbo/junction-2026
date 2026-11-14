#!/usr/bin/env python3
"""Measure locate.py on a person walking through a real room with a handheld camera.

Data: TUM RGB-D (see fetch_tum.py). Every colour frame has the camera's true pose from motion
capture and a Kinect depth picture in real metres, so position, heading and the distance to a
place can be checked in metres.

Four steps, each its own mode:

    --check       do two recordings of a room share one ground-truth frame? (CPU)
    --make-plan   pick baseline frames, places and query frames, write plan.json (CPU)
    (default)     run the fixes on a GPU: one line per fix in results.jsonl, one small .npz each
    --summarise   tables from plan.json and results.jsonl (CPU): see locate_walk_tables.py

To try a change to locate.py without the GPU: --dump DIR saves every pass's reconstruction once
(GPU, no scoring), --from-dump DIR scores the saved ones on CPU cores (--shard i/n to run n in
parallel, --locate-args and --known-pair to pass settings to locate()). See locate_v2_eval.py.

A fix is assembled like live/worker.py where(): 24 baseline frames are kept per room (picked
like recon_change.sample_frames picks from a video: the sharpest of each stretch, then stored
as JPEG quality 92), 12 of them go through the model together with 1 or 3 frames of the wearer.

    python locate_walk_eval.py --data data/tum --check
    python locate_walk_eval.py --data data/tum --plan out/plan.json --make-plan
    python locate_walk_eval.py --data data/tum --plan out/plan.json --out out        # GPU
"""
import argparse
import contextlib
import datetime
import hashlib
import io
import json
import re
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
# Colour camera of each Kinect: fx, fy, cx, cy (TUM "file formats" page). Lens distortion is ignored.
INTRINSICS = {"freiburg1": (517.3, 516.5, 318.6, 255.3), "freiburg2": (520.9, 521.0, 325.1, 249.7),
              "freiburg3": (535.4, 539.2, 320.1, 247.6)}
DEPTH_FACTOR = 5000.0  # 16-bit depth PNG: 5000 = 1 m, 0 = no reading
# A room: the recording the baseline comes from, other recordings of the same room, and how far
# in time (seconds) a held-out frame of the baseline recording must be from every baseline frame
# a fix uses.
ROOMS = {
    "fr1_office": {"baseline": "freiburg1_room", "others": ["freiburg1_desk", "freiburg1_desk2", "freiburg1_360", "freiburg1_xyz"], "gap_s": 1.5},
    "fr2_desk": {"baseline": "freiburg2_desk", "others": ["freiburg2_xyz"], "gap_s": 2.5},
    "fr3_office": {"baseline": "freiburg3_long_office_household", "others": [], "gap_s": 2.5},
}
N_KEPT, N_USED = 24, 12  # live/worker.py: RECON_FRAMES keeps 24, RECON_LOCATE_BASELINE uses 12
QUERIES_PER_ROOM = 240


# ----------------------------------------------------------------------------- reading TUM
def read_list(path):
    rows = []
    for line in Path(path).read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            parts = line.split()
            rows.append((float(parts[0]), parts[1:]))
    return rows


def rotation(q):
    """Quaternion qx qy qz qw -> 3x3 matrix, camera axes (x right, y down, z forward) in the world."""
    x, y, z, w = np.asarray(q, float) / np.linalg.norm(q)
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                     [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                     [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])


def recording(folder):
    """Colour frames that have a true pose within 20 ms: time, file, depth file, camera centre C,
    rotation R (camera -> world), and how fast the camera moved (m/s) and turned (deg/s)."""
    folder = Path(folder)
    depth = read_list(folder / "depth.txt")
    truth = read_list(folder / "groundtruth.txt")
    depth_t, truth_t = np.array([t for t, _ in depth]), np.array([t for t, _ in truth])
    G = np.array([[float(v) for v in p] for _, p in truth])
    frames = []
    for t, (name,) in read_list(folder / "rgb.txt"):
        j = int(np.argmin(np.abs(truth_t - t)))
        if abs(truth_t[j] - t) > 0.02:
            continue
        k = int(np.argmin(np.abs(depth_t - t)))
        a = int(np.clip(np.searchsorted(truth_t, t - 0.1), 0, len(truth_t) - 1))
        b = int(np.clip(np.searchsorted(truth_t, t + 0.1), 0, len(truth_t) - 1))
        span = truth_t[b] - truth_t[a]
        speed = turn = None
        if 0.05 < span < 0.5:
            speed = float(np.linalg.norm(G[b, :3] - G[a, :3]) / span)
            between = rotation(G[b, 3:]) @ rotation(G[a, 3:]).T
            turn = float(np.degrees(np.arccos(np.clip((np.trace(between) - 1) / 2, -1, 1))) / span)
        frames.append({"t": t, "rgb": name, "depth": depth[k][1][0] if abs(depth_t[k] - t) <= 0.02 else None,
                       "C": G[j, :3], "R": rotation(G[j, 3:]), "speed": speed, "turn": turn})
    return frames


def kinect_depth(folder, frame):
    d = cv2.imread(str(Path(folder) / frame["depth"]), cv2.IMREAD_UNCHANGED)
    return d.astype(np.float32) / DEPTH_FACTOR


def kinect_cloud(folder, frame, cam, step=8):
    """World points from one Kinect depth picture, every step-th pixel."""
    fx, fy, cx, cy = cam
    d = kinect_depth(folder, frame)[::step, ::step]
    v, u = np.mgrid[0:480:step, 0:640:step]
    ok = d > 0
    pts = np.stack([(u[ok] - cx) / fx * d[ok], (v[ok] - cy) / fy * d[ok], d[ok]], 1)
    return pts @ frame["R"].T + frame["C"]


def agreement(points, folder, frame, cam, tol=0.05):
    """Share of world points that, seen from this frame, lie at the depth its Kinect measured.
    Returns (share that agree, how many points landed on a valid depth pixel)."""
    fx, fy, cx, cy = cam
    p = (points - frame["C"]) @ frame["R"]
    z = p[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        u, v = np.rint(fx * p[:, 0] / z + cx), np.rint(fy * p[:, 1] / z + cy)
    inside = (z > 0.3) & (u >= 0) & (u < 640) & (v >= 0) & (v < 480)
    d = kinect_depth(folder, frame)[v[inside].astype(int), u[inside].astype(int)]
    ok = d > 0
    if ok.sum() < 50:
        return None, int(ok.sum())
    return float((np.abs(d[ok] - z[inside][ok]) / z[inside][ok] < tol).mean()), int(ok.sum())


def angle_between(Ra, Rb):
    return float(np.degrees(np.arccos(np.clip(Ra[:, 2] @ Rb[:, 2], -1, 1))))


def check(data):
    """Do the recordings of a room share one world frame? Take frames of the other recording,
    find the baseline-recording frame taken from nearly the same true pose, and see whether the
    Kinect depth of one lands on the Kinect depth of the other. Within one recording (frames one
    second apart) the same test shows what agreement looks like when the frame is shared."""
    for room, spec in ROOMS.items():
        base_dir = Path(data) / spec["baseline"]
        cam = INTRINSICS[spec["baseline"].split("_")[0]]
        A = recording(base_dir)
        C = np.array([f["C"] for f in A])
        print(f"\n{room}: {spec['baseline']}: {len(A)} frames with truth, {A[-1]['t'] - A[0]['t']:.0f} s, camera x {C[:, 0].min():.2f}..{C[:, 0].max():.2f} "
              f"y {C[:, 1].min():.2f}..{C[:, 1].max():.2f} z {C[:, 2].min():.2f}..{C[:, 2].max():.2f}", flush=True)
        pts = np.concatenate([kinect_cloud(base_dir, f, cam, 16) for f in A[::max(1, len(A) // 40)] if f["depth"]])
        pct = np.percentile(pts, [1, 5, 25, 50, 75, 95, 99], axis=0)
        print("  Kinect points in the world, percentiles 1 5 25 50 75 95 99:")
        for axis in range(3):
            print(f"    {'xyz'[axis]}: " + " ".join(f"{v:6.2f}" for v in pct[:, axis]))
        hist, edges = np.histogram(pts[:, 2], bins=np.arange(-0.5, 3.01, 0.05))
        top = np.argsort(hist)[::-1][:4]
        print("  most common heights z (m): " + ", ".join(f"{edges[i]:.2f} ({100 * hist[i] / len(pts):.0f}%)" for i in top))
        down = np.mean([f["R"][:, 1] for f in A], 0)
        print(f"  mean camera down-axis in the world: {np.round(down, 2)}")
        own = [agreement(kinect_cloud(base_dir, A[i], cam), base_dir, A[i + 30], cam)[0] for i in range(0, len(A) - 31, max(1, len(A) // 25))
               if A[i]["depth"] and A[i + 30]["depth"]]
        print(f"  same recording, frames 1 s apart: median agreement {np.median([o for o in own if o is not None]):.2f}")
        for other in spec["others"]:
            other_dir = Path(data) / other
            if not other_dir.exists():
                print(f"  {other}: not downloaded")
                continue
            B = recording(other_dir)
            CB = np.array([f["C"] for f in B])
            print(f"  {other}: {len(B)} frames, {B[-1]['t'] - B[0]['t']:.0f} s, camera x {CB[:, 0].min():.2f}..{CB[:, 0].max():.2f} "
                  f"y {CB[:, 1].min():.2f}..{CB[:, 1].max():.2f} z {CB[:, 2].min():.2f}..{CB[:, 2].max():.2f}")
            got, near = [], 0
            for f in B[::max(1, len(B) // 40)]:
                if not f["depth"]:
                    continue
                cost = [np.linalg.norm(a["C"] - f["C"]) + np.radians(angle_between(a["R"], f["R"])) for a in A]
                a = A[int(np.argmin(cost))]
                if np.linalg.norm(a["C"] - f["C"]) > 0.6 or angle_between(a["R"], f["R"]) > 35 or not a["depth"]:
                    continue
                near += 1
                share, n = agreement(kinect_cloud(other_dir, f, cam), base_dir, a, cam)
                if share is not None:
                    got.append(share)
            print(f"    frames with a baseline-recording frame at nearly the same true pose: {near}; "
                  f"depth agreement median {np.median(got) if got else float('nan'):.2f}, min {min(got) if got else float('nan'):.2f} over {len(got)}")


# ----------------------------------------------------------------------------- plan
def spread_order(n):
    """0..n-1 in an order whose every prefix is spread over the range."""
    return sorted(range(n), key=lambda i: (i * 0.6180339887) % 1.0)


def as_json(frame, rec, **more):
    return {"rec": rec, "rgb": frame["rgb"], "t": frame["t"], "C": [float(v) for v in frame["C"]],
            "R": [[float(v) for v in row] for row in frame["R"]], "speed": frame["speed"], "turn": frame["turn"], **more}


def make_plan(data, plan_path, shared):
    sys.path[:0] = [str(HERE), str(HERE.parent)]
    import recon_change as rc

    plan = {"date": datetime.datetime.now().isoformat(timespec="seconds"), "rooms": {}}
    for room, spec in ROOMS.items():
        base_dir = Path(data) / spec["baseline"]
        cam = INTRINSICS[spec["baseline"].split("_")[0]]
        A = recording(base_dir)
        # The baseline: as recon_change.sample_frames does for a video. n stretches, four candidate
        # frames in each, the sharpest wins.
        total, best = len(A), {}
        for s in range(N_KEPT):
            for f in np.linspace(s * total / N_KEPT, (s + 1) * total / N_KEPT, 6)[1:-1]:
                i = int(f)
                if not A[i]["depth"]:
                    continue
                score = float(rc.sharpness(cv2.imread(str(base_dir / A[i]["rgb"]))))
                if score > best.get(s, (-1.0, None))[0]:
                    best[s] = (score, i)
        kept = [best[s][1] for s in sorted(best)]
        used = sorted({int(i) for i in np.linspace(0, len(kept) - 1, min(N_USED, len(kept))).round()})
        used_t = np.array([A[kept[i]]["t"] for i in used])
        kept_t = np.array([A[i]["t"] for i in kept])

        # Places: one pixel in each of five of the used baseline frames, on something with texture
        # and a steady Kinect depth between 0.5 and 3 m, away from the picture's edge.
        places = []
        for slot in [used[i] for i in np.linspace(0, len(used) - 1, 5).round().astype(int)]:
            frame = A[kept[slot]]
            grey = cv2.cvtColor(cv2.imread(str(base_dir / frame["rgb"])), cv2.COLOR_BGR2GRAY).astype(np.float32)
            d = kinect_depth(base_dir, frame)
            best_spot = None
            for v in range(120, 361, 20):
                for u in range(140, 501, 20):
                    w = d[v - 6:v + 7, u - 6:u + 7]
                    if (w <= 0).any() or w.max() - w.min() > 0.04 or not 0.5 <= float(np.median(w)) <= 3.0:
                        continue
                    texture = float(cv2.Laplacian(grey[v - 20:v + 21, u - 20:u + 21], cv2.CV_32F).var())
                    if best_spot is None or texture > best_spot[0]:
                        best_spot = (texture, u, v)
            if best_spot is None:
                continue
            _, u, v = best_spot
            z = float(np.median(d[v - 3:v + 4, u - 3:u + 4]))
            world = frame["R"] @ np.array([(u - cam[0 + 2]) / cam[0] * z, (v - cam[3]) / cam[1] * z, z]) + frame["C"]
            places.append({"name": f"place{len(places) + 1}", "frame": slot, "u": u / 639.0, "v": v / 479.0, "px": [u, v],
                           "depth_m": z, "world": [float(x) for x in world]})

        # Same-room queries. Held-out frames of the baseline recording: away in time from every
        # baseline frame a fix uses (gap_s) and at least 0.5 s from the other kept ones.
        def away(f):
            return np.abs(used_t - f["t"]).min() >= spec["gap_s"] and np.abs(kept_t - f["t"]).min() >= 0.5

        sources = {"held_out": (spec["baseline"], [f for f in A if away(f)])}
        for other in spec["others"]:
            if other in shared and (Path(data) / other).exists():
                sources[other] = (other, recording(Path(data) / other)[30:])  # skip the first second
        share = QUERIES_PER_ROOM // len(sources)
        lists = {}
        for name, (rec, frames) in sources.items():
            every = recording(Path(data) / rec) if rec != spec["baseline"] else A
            times = np.array([f["t"] for f in every])
            picks = [frames[i] for i in np.linspace(0, len(frames) - 1, min(share, len(frames))).round().astype(int)]
            out = []
            for f in picks:
                earlier = [every[int(np.argmin(np.abs(times - (f["t"] - back))))]["rgb"] for back in (1.0, 0.5)]
                out.append(as_json(f, rec, source=name, earlier=earlier,
                                   gap_s=float(np.abs(used_t - f["t"]).min()) if rec == spec["baseline"] else None))
            lists[name] = [out[i] for i in spread_order(len(out))]
        queries = [q for group in zip(*lists.values()) for q in group] if len({len(v) for v in lists.values()}) == 1 else \
            [q for i in range(max(len(v) for v in lists.values())) for v in lists.values() if i < len(v) for q in [v[i]]]

        d_all = np.concatenate([kinect_depth(base_dir, A[kept[i]])[::4, ::4].ravel() for i in used])
        plan["rooms"][room] = {
            "baseline_recording": spec["baseline"], "intrinsics": cam, "gap_s": spec["gap_s"],
            "baseline": [as_json(A[i], spec["baseline"], sharpness=best[s][0]) for s, i in zip(sorted(best), kept)],
            "used": used, "places": places, "queries": queries,
            "kinect_median_depth_m": float(np.median(d_all[d_all > 0])), "kinect_valid_share": float((d_all > 0).mean()),
        }
        print(f"{room}: kept {len(kept)} baseline frames, use {len(used)}, {len(places)} places, {len(queries)} queries "
              f"({', '.join(f'{k}: {len(v)}' for k, v in lists.items())}), Kinect median depth of the used frames "
              f"{plan['rooms'][room]['kinect_median_depth_m']:.2f} m", flush=True)
        # A picture of the places, to see what each one is on.
        tiles = []
        for p in places:
            img = cv2.imread(str(base_dir / A[kept[p["frame"]]]["rgb"]))
            cv2.circle(img, tuple(p["px"]), 14, (0, 255, 255), 3)
            cv2.putText(img, f"{p['name']} {p['depth_m']:.1f} m", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
            tiles.append(cv2.resize(img, (480, 360)))
        if tiles:
            cv2.imwrite(str(Path(plan_path).parent / f"places_{room}.jpg"), np.hstack(tiles), [cv2.IMWRITE_JPEG_QUALITY, 80])
    Path(plan_path).write_text(json.dumps(plan))
    print(f"wrote {plan_path}")


# ----------------------------------------------------------------------------- run
def tasks_for(plan, rounds):
    """Rounds, so a run that is cut short is still balanced. Per room and round: six same-room
    fixes and two wrong-room fixes with 12 baseline frames and 1 query frame; the first of the
    six again with 24 baseline frames and again with 3 query frames; and every second round one
    wrong-room fix with 24 baseline frames."""
    rooms = list(plan["rooms"])
    tasks = []
    for k in range(rounds):
        for i, room in enumerate(rooms):
            q = plan["rooms"][room]["queries"]
            for j in range(6):
                if 6 * k + j < len(q):
                    tasks.append(("same", N_USED, 1, room, room, 6 * k + j))
            for j in range(2):
                other = rooms[(i + 1 + (2 * k + j) % (len(rooms) - 1)) % len(rooms)]
                tasks.append(("wrong", N_USED, 1, room, other, (2 * k + j) % len(plan["rooms"][other]["queries"])))
            if 6 * k < len(q):
                tasks.append(("same", N_KEPT, 1, room, room, 6 * k))
                tasks.append(("same", N_USED, 3, room, room, 6 * k))
            if k % 2 == 0:
                other = rooms[(i + 1 + (k // 2) % (len(rooms) - 1)) % len(rooms)]
                tasks.append(("wrong", N_KEPT, 1, room, other, (2 * k) % len(plan["rooms"][other]["queries"])))
    return tasks


def views_shares(locate, rc, recon, valid, base, newest):
    """The parts of locate.py's match score, for the newest frame and for the four baseline frames
    it uses as its yardstick. Per frame: the share of its trusted pixels that at least 1 / at
    least 2 baseline frames see at the same depth, the share where at least 1 / at least 2 also
    look alike, and the share of its pixels that are trusted. The shipped score is the fourth
    number of the query (times min(1, trusted share / the baseline's)) over the median of the same
    for the four baseline frames."""
    feat = locate.unit_features(recon)
    near = rc.nearest_in_window(recon.depth, 7)
    rng = np.random.default_rng(0)

    def shares(q, others):
        agree, alike, seen = locate.views(recon, valid, near, feat, q, others, 0.06, 6000, rng)
        if not len(agree):
            return [0.0, 0.0, 0.0, 0.0, seen]
        return [float((agree >= 1).mean()), float((agree >= 2).mean()), float((alike >= 1).mean()), float((alike >= 2).mean()), seen]

    probes = [base[i] for i in np.linspace(0, len(base) - 1, min(4, len(base))).round().astype(int)]
    return {"views_query": shares(newest, base), "views_probes": [shares(p, [j for j in base if j != p]) for p in probes],
            "trusted": float(valid[base].mean())}


def run(args):
    started = time.time()
    out = Path(args.out)
    (out / "fix").mkdir(parents=True, exist_ok=True)
    detector_dir = Path(args.detector_dir) if args.detector_dir else HERE if (HERE / "locate.py").exists() else HERE.parent
    sys.path[:0] = [str(HERE), str(detector_dir)]
    import locate
    import recon_change as rc
    import torch

    plan = json.loads(Path(args.plan).read_text())
    meta = {"dataset": "TUM RGB-D", "date": datetime.datetime.now().isoformat(timespec="seconds"), "detector_commit": args.detector_commit,
            "found": locate.FOUND, "unsure": locate.UNSURE,
            "locate_sha256": hashlib.sha256(Path(locate.__file__).read_bytes()).hexdigest(),
            "recon_change_sha256": hashlib.sha256(Path(rc.__file__).read_bytes()).hexdigest(),
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}
    print(json.dumps(meta, indent=2), flush=True)
    (out / "meta.json").write_text(json.dumps(meta, indent=2))

    def picture(rec, name):
        img = cv2.imread(str(Path(args.data) / rec / name))
        if img is None:
            raise RuntimeError(f"Cannot read {rec}/{name}")
        return img

    stored = {}  # baseline frames as the worker stores them: JPEG quality 92
    for room, r in [] if args.from_dump else plan["rooms"].items():
        stored[room] = [cv2.imdecode(cv2.imencode(".jpg", picture(b["rec"], b["rgb"]), [cv2.IMWRITE_JPEG_QUALITY, 92])[1], cv2.IMREAD_COLOR)
                        for b in r["baseline"]]

    tasks = [t for t in tasks_for(plan, args.rounds) if not args.frames or f"{t[1]}+{t[2]}" in args.frames]
    if args.shard:
        part, parts = (int(v) for v in args.shard.split("/"))
        tasks = tasks[part::parts]
    stored_dir = Path(args.dump or args.from_dump) if args.dump or args.from_dump else None
    if args.dump:
        stored_dir.mkdir(parents=True, exist_ok=True)
    extra = json.loads(args.locate_args) if args.locate_args else {}
    path = out / "results.jsonl"
    done = {tuple(json.loads(line)["key"]) for line in path.read_text().splitlines() if line.strip()} if path.exists() else set()
    print(f"{len(tasks)} fixes planned, {len(done)} already done", flush=True)
    model = None if args.from_dump else rc.load_model()
    with open(path, "a") as sink:
        for kind, n_base, n_new, room, query_room, qi in tasks:
            key = (kind, n_base, n_new, room, query_room, qi)
            if key in done:
                continue
            if time.time() - started > args.minutes * 60:
                print(f"Stopping: {args.minutes} minutes used", flush=True)
                break
            r, q = plan["rooms"][room], plan["rooms"][query_room]["queries"][qi]
            file = stored_dir / ("_".join(str(v) for v in key) + ".npz") if stored_dir else None
            if file is not None and file.exists() != bool(args.from_dump):  # --dump: saved already; --from-dump: never saved
                continue
            # live/worker.py where(): an even pick of the kept frames, plus every frame a place was picked in
            spread = np.linspace(0, len(r["baseline"]) - 1, min(n_base, len(r["baseline"]))).round().astype(int)
            pick = sorted({int(i) for i in spread} | {p["frame"] for p in r["places"]})
            slot = {f: i for i, f in enumerate(pick)}
            names = (q["earlier"] if n_new == 3 else []) + [q["rgb"]]
            row = {"key": list(key), "kind": kind, "n_baseline": len(pick), "n_new": len(names), "room": room, "query_room": query_room,
                   "query": qi, "pick": pick}
            try:
                if args.from_dump:
                    saved = np.load(file)
                    recon = rc.Recon(saved["depth"], saved["conf"], saved["K"], saved["E"], None, saved["feat"] if "feat" in saved else None)
                    row.update(json.loads(str(saved["meta"])))
                    base, new = pick, names  # only their number is used below
                else:
                    base = [stored[room][i] for i in pick]
                    new = [picture(q["rec"], name) for name in names]
                    row["sharpness"] = float(rc.sharpness(new[-1]))
                    log = io.StringIO()
                    t0 = time.time()
                    with contextlib.redirect_stdout(log):
                        recon = rc.reconstruct(model, base + new)
                    row["reconstruct_s"] = round(time.time() - t0, 2)
                    printed = re.search(r"inference ([\d.]+) s", log.getvalue())
                    row["inference_s"] = float(printed.group(1)) if printed else None
                    row["peak_gpu_gb"] = round(torch.cuda.max_memory_allocated() / 1e9, 1) if torch.cuda.is_available() else None
                if args.dump:  # everything locate reads, without the pictures
                    part = file.with_suffix(".part.npz")
                    np.savez(part, depth=recon.depth, conf=recon.conf, K=recon.K, E=recon.E, meta=json.dumps(row),
                             **({} if recon.feat is None else {"feat": recon.feat}))
                    part.rename(file)
                    print(f"{kind} n={len(base)}+{len(new)} {room} <- {query_room}/{q['rec']}/{q['rgb']}: saved, model {row['inference_s']} s", flush=True)
                    continue
                t0 = time.time()
                base_idx = list(range(len(base)))
                if args.known_pair and len(r["places"]) >= 2:  # one true distance: between the two places farthest apart
                    a, b = max(((a, b) for i, a in enumerate(r["places"]) for b in r["places"][i + 1:]),
                               key=lambda ab: np.linalg.norm(np.array(ab[0]["world"]) - np.array(ab[1]["world"])))
                    extra["known"] = {"a": a["name"], "b": b["name"],
                                      "metres": float(np.linalg.norm(np.array(a["world"]) - np.array(b["world"])))}
                result, _, valid = locate.locate(recon, base_idx, list(range(len(base), len(base) + len(new))),
                                                 [{**p, "frame": slot[p["frame"]]} for p in r["places"]], **extra)
                row["locate_s"] = round(time.time() - t0, 2)
                row.update({k: v for k, v in result.items() if k not in ("map", "track")})
                try:  # what the match score is made of, to try other rules without the GPU (see views_shares)
                    row.update(views_shares(locate, rc, recon, valid, base_idx, len(base) + len(new) - 1))
                except Exception as e:
                    row["views_error"] = f"{type(e).__name__}: {e}"
                row["E"] = np.round(recon.E.astype(np.float64), 6).tolist()
                row["focal"] = [float(recon.K[-1][0, 0]), float(recon.K[-1][1, 1])]
                row["median_baseline_depth"] = float(np.median(recon.depth[base_idx][valid[base_idx]]))
                # For trying other ways to get metres without the GPU: the baseline's trusted points, thinned.
                if not args.from_dump:  # (with a saved reconstruction the points can be made again)
                    cloud = np.concatenate([rc.unproject(recon.depth[i], recon.K[i], recon.E[i])[valid[i]][::47] for i in base_idx])
                    np.savez(out / "fix" / ("_".join(str(v) for v in key) + ".npz"), cloud=cloud.astype(np.float32))
                print(f"{kind} n={len(base)}+{len(new)} {room} <- {query_room}/{q['rec']}/{q['rgb']}: {row['state']} {row['confidence']:.2f} | "
                      f"model {row['inference_s']} s, locate {row['locate_s']} s", flush=True)
            except Exception as e:  # keep going: one bad fix must not lose the rest
                row["error"] = f"{type(e).__name__}: {e}"
                print(f"FAILED {key}: {row['error']}", flush=True)
            sink.write(json.dumps(row) + "\n")
            sink.flush()
    print(f"finished after {(time.time() - started) / 60:.1f} min", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data/tum", help="folder written by fetch_tum.py")
    ap.add_argument("--plan", default="out/plan.json")
    ap.add_argument("--out", default="out")
    ap.add_argument("--check", action="store_true", help="do the recordings of each room share one ground-truth frame?")
    ap.add_argument("--make-plan", action="store_true", help="pick baseline frames, places and queries")
    ap.add_argument("--shared", nargs="*", default=[], help="with --make-plan: other recordings whose ground truth shares the "
                    "baseline recording's frame (see --check); only these give queries")
    ap.add_argument("--rounds", type=int, default=40)
    ap.add_argument("--minutes", type=float, default=26, help="stop starting new fixes after this long")
    ap.add_argument("--dump", help="save each pass's reconstruction in this folder instead of scoring it (about 28 MB per pass at 12 + 1 frames)")
    ap.add_argument("--from-dump", help="score reconstructions saved with --dump instead of running the model; passes without a file are skipped")
    ap.add_argument("--frames", nargs="*", default=[], help="only these settings, as baseline+query frames: 12+1 24+1 12+3")
    ap.add_argument("--shard", help="i/n: take every n-th fix starting at the i-th, to run n processes side by side")
    ap.add_argument("--locate-args", help="JSON of extra arguments for locate.locate(), for example {\"need\": 1}")
    ap.add_argument("--known-pair", action="store_true", help="give locate() one true distance: between the two places farthest apart")
    ap.add_argument("--detector-dir", help="folder with locate.py and recon_change.py (default: next to this script, else its parent)")
    ap.add_argument("--detector-commit", default="unknown")
    args = ap.parse_args()
    if args.check:
        check(args.data)
    elif args.make_plan:
        Path(args.plan).parent.mkdir(parents=True, exist_ok=True)
        make_plan(args.data, args.plan, set(args.shared))
    else:
        run(args)


if __name__ == "__main__":
    main()
