#!/usr/bin/env python3
"""Measure locate.py ("where is the wearer?") on real photos.

Data: PASLCD (see fetch_paslcd.py and fetch_poses.py). Each scene has a reference capture and a
later capture of the same place on a different camera path, with some objects moved.

The baseline is 24, 12 or 8 photos spread evenly over a scene's reference capture. One query
photo at a time goes through the model together with the baseline, exactly as in production
(rc.reconstruct, then locate.locate with the query as the only new frame). Three kinds of query:

  same                 a photo from the later capture of the same scene: should be "found"
  wrong                a photo from the later capture of another scene: must be "lost"
  same_other_light     indoor scenes only: the baseline is the reference capture of the scene's
                       second PASLCD instance, taken under different lighting than the query

For same-room queries the answer is checked against the dataset's COLMAP poses: our baseline
cameras are fitted to theirs with a similarity transform (scale, rotation, shift), and the
query camera's position and viewing direction are compared after that fit. COLMAP has no
real-world scale, so the position error is given as a share of the baseline cameras' spread.

Work is done in rounds (every baseline size and every scene once per round) and each result is
appended to results.jsonl at once, so a run that is cut short still gives a balanced table, and
a second run with the same --out continues.

    python locate_eval.py --data data/PASLCD --poses poses --out out      # on a GPU
    python locate_eval.py --out out --summarise                           # tables only

To try a change to locate.py without the GPU: --dump DIR saves every pass's reconstruction once
(GPU, no scoring), --from-dump DIR scores the saved ones on a CPU (--shard i/n to run n in parallel).
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
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ORDER = [0, 6, 3, 9, 1, 7, 4, 10, 2, 8, 5, 11]  # which query photo each round uses: spread over the capture
SWEEP = [0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
BINS = [(0.0, 0.2), (0.2, 0.5), (0.5, 0.8), (0.8, 1.01)]


# ----------------------------------------------------------------------------- pose truth
def colmap_camera(pose):
    """[qw, qx, qy, qz, tx, ty, tz] (world -> camera) -> camera position and viewing direction."""
    w, x, y, z = pose[:4]
    R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                  [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                  [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])
    return -R.T @ np.array(pose[4:]), R[2]


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


def spread(truth, names):
    """Largest distance between two of these cameras, in the truth's units: the scene size."""
    c = np.array([colmap_camera(truth[n])[0] for n in names])
    return float(np.sqrt(((c[:, None] - c[None]) ** 2).sum(-1)).max())


def pose_check(camera, E, names, truth, size, metres_per_unit):
    """Compare the last camera (the query) with the truth, after fitting the others (the
    baseline) to the truth. None if the truth lacks one of the photos."""
    if truth is None or any(n not in truth for n in names):
        return None
    ours = [camera(e) for e in E]
    theirs = [colmap_camera(truth[n]) for n in names]
    P, G = np.array([p for p, _ in ours[:-1]]), np.array([g for g, _ in theirs[:-1]])
    s, R, t = similarity(P, G)
    fit = float(np.sqrt((((s * P @ R.T + t) - G) ** 2).sum(1).mean()))
    error = float(np.linalg.norm(s * R @ ours[-1][0] + t - theirs[-1][0]))
    a, b = R @ ours[-1][1], theirs[-1][1]
    angle = float(np.degrees(np.arccos(np.clip(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)), -1, 1))))
    return {
        "position_error_share": error / size,  # share of the baseline cameras' spread
        "position_error_rough_m": error / s * metres_per_unit,  # in locate.py's assumed metres
        "heading_error_deg": angle,  # angle between the two viewing directions, in 3D
        "baseline_fit_share": fit / size,  # how well our baseline cameras fit theirs (RMS)
        "nearest_baseline_share": float(np.linalg.norm(G - theirs[-1][0], axis=1).min()) / size,
        "centroid_guess_share": float(np.linalg.norm(G.mean(0) - theirs[-1][0])) / size,  # error of a blind guess
    }


# ----------------------------------------------------------------------------- tables
def share(k, n):
    return f"{k}/{n} ({100 * k / n:.0f}%)" if n else "-"


def pick(rows, kind, n=None, scene=None, setting=None):
    return [r for r in rows if r["kind"] == kind and (n is None or r["n_baseline"] == n)
            and (scene is None or r["scene"] == scene) and (setting is None or r["setting"] == setting)]


def num(values, how, digits=2):
    values = [v for v in values if v is not None]
    if not values:
        return "-"
    v = {"min": np.min, "max": np.max, "median": np.median, "p90": lambda x: np.percentile(x, 90),
         "p10": lambda x: np.percentile(x, 10)}[how](values)
    return f"{v:.{digits}f}"


def states(rows):
    c = Counter(r["state"] for r in rows)
    return c["found"], c["unsure"], c["lost"]


def tables(rows, meta):
    failed = len([1 for r in rows if "error" in r])
    rows = [r for r in rows if "error" not in r]
    sizes = sorted({r["n_baseline"] for r in rows}, reverse=True)
    if not sizes:
        return "No results yet.\n"
    top = sizes[0]
    scenes = list(dict.fromkeys(r["scene"] for r in rows))
    found_at, unsure_at = meta.get("found", 0.5), meta.get("unsure", 0.2)
    L = [f"Thresholds in use: found at match >= {found_at}, lost at match < {unsure_at}. "
         f"Passes so far: {len(rows)}" + (f" ({failed} failed)" if failed else "") + ".", ""]

    def groups(kind, n):
        out = [(f"{s} ({pick(rows, kind, n, s)[0]['setting']})", pick(rows, kind, n, s)) for s in scenes if pick(rows, kind, n, s)]
        for label, setting in (("Indoor", "indoor"), ("Outdoor", "outdoor"), ("All", None)):
            got = pick(rows, kind, n, setting=setting)
            if got:
                out.append((f"**{label} pooled**", got))
        return out

    L += [f"### 1. Same room ({top} baseline photos, one query photo from the later capture)", "",
          "| Scene | Queries | Found | Unsure | Lost | Match min | Match median | Match max |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for label, got in groups("same", top):
        f, u, lo = states(got)
        m = [r["match"] for r in got]
        L.append(f"| {label} | {len(got)} | {share(f, len(got))} | {share(u, len(got))} | {share(lo, len(got))} | "
                 f"{num(m, 'min')} | {num(m, 'median')} | {num(m, 'max')} |")

    L += ["", f"### 2. Wrong room ({top} baseline photos, one query photo from another scene; must be lost)", "",
          "| Baseline scene | Queries | Lost | Unsure (wrong) | Found (wrong) | Match median | Match max | Highest match came from |",
          "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for label, got in groups("wrong", top):
        f, u, lo = states(got)
        m = [r["match"] for r in got]
        worst = max(got, key=lambda r: r["match"])
        L.append(f"| {label} | {len(got)} | {share(lo, len(got))} | {share(u, len(got))} | {share(f, len(got))} | "
                 f"{num(m, 'median')} | {num(m, 'max')} | {worst['query_scene']} photo on {worst['scene']} baseline |")
    not_lost = sorted([r for r in rows if r["kind"] == "wrong" and r["state"] != "lost"], key=lambda r: -r["match"])
    if not_lost:
        L += ["", "Wrong-room queries that were not lost (all baseline sizes):", ""]
        L += [f"- {r['query_scene']} photo {r['query']} on {r['scene']} baseline of {r['n_baseline']}: "
              f"{r['state']}, match {r['match']:.2f}" for r in not_lost[:30]]

    def pose(got, key, scale=1.0):
        return [r["pose"][key] * scale for r in got if r.get("pose")]

    other = [r for r in rows if r["kind"] == "same_other_light"]
    if other:
        L += ["", "### 2b. Same room, baseline taken under different lighting (indoor scenes, PASLCD instance 2)", "",
              "| Baseline photos | Scene | Queries | Found | Unsure | Lost | Match min | Match median | Position error % median | max | "
              "Heading error deg median | max |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for n in sizes:
            for label, got in groups("same_other_light", n):
                if "Outdoor" in label or "All" in label:
                    continue
                f, u, lo = states(got)
                m = [r["match"] for r in got]
                e, h = pose(got, "position_error_share", 100), pose(got, "heading_error_deg")
                L.append(f"| {n} | {label} | {len(got)} | {share(f, len(got))} | {share(u, len(got))} | {share(lo, len(got))} | {num(m, 'min')} | "
                         f"{num(m, 'median')} | {num(e, 'median', 1)} | {num(e, 'max', 1)} | {num(h, 'median', 1)} | {num(h, 'max', 1)} |")

    L += ["", f"### 3. Is the position right? (same-room queries, {top} baseline photos, against COLMAP poses)", "",
          "Position error is a percentage of the largest distance between two baseline cameras. "
          "\"Rough m\" uses locate.py's own assumed scale (things are about 2 m away), so it is not a measured distance.", "",
          "| Scene | Queries with truth | Position error % median | p90 | max | Rough m median | Heading error deg median | p90 | max | "
          "Baseline fit % | Query to nearest baseline camera % median |",
          "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for label, got in groups("same", top) + [("**All pooled, found only**", [r for r in pick(rows, "same", top) if r["state"] == "found"])]:
        e, h = pose(got, "position_error_share", 100), pose(got, "heading_error_deg")
        L.append(f"| {label} | {len(e)} | {num(e, 'median', 1)} | {num(e, 'p90', 1)} | {num(e, 'max', 1)} | "
                 f"{num(pose(got, 'position_error_rough_m'), 'median')} | {num(h, 'median', 1)} | {num(h, 'p90', 1)} | {num(h, 'max', 1)} | "
                 f"{num(pose(got, 'baseline_fit_share', 100), 'median', 1)} | {num(pose(got, 'nearest_baseline_share', 100), 'median', 1)} |")
    blind = pose(pick(rows, "same", top), "centroid_guess_share", 100)
    L += ["", f"For scale: guessing the middle of the baseline cameras every time would give a median position error of {num(blind, 'median', 1)}%.", "",
          "Position and heading error by match score (same-room queries, all baseline sizes):", "",
          "| Match | Queries with truth | Position error % median | max | Heading error deg median | max |", "| --- | --- | --- | --- | --- | --- |"]
    for lo, hi in BINS:
        got = [r for r in rows if r["kind"] == "same" and lo <= r["match"] < hi]
        e, h = pose(got, "position_error_share", 100), pose(got, "heading_error_deg")
        L.append(f"| {lo:.1f} to {min(hi, 1.0):.1f} | {len(e)} | {num(e, 'median', 1)} | {num(e, 'max', 1)} | {num(h, 'median', 1)} | {num(h, 'max', 1)} |")

    L += ["", "### 4. Cost: fewer baseline photos", "",
          "| Baseline photos | Same room: found / unsure / lost | Same-room match median (min) | Wrong room: lost / unsure / found | "
          "Wrong-room match max | Position error % median (p90) | Heading error deg median (p90) | Model s median | Locate s median | Peak GPU GB |",
          "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for n in sizes:
        same, wrong = pick(rows, "same", n), pick(rows, "wrong", n)
        f, u, lo = states(same)
        wf, wu, wl = states(wrong)
        e, h = pose(same, "position_error_share", 100), pose(same, "heading_error_deg")
        everything = [r for r in rows if r["n_baseline"] == n]
        L.append(f"| {n} | {share(f, len(same))} / {u} / {lo} | {num([r['match'] for r in same], 'median')} ({num([r['match'] for r in same], 'min')}) | "
                 f"{share(wl, len(wrong))} / {wu} / {wf} | {num([r['match'] for r in wrong], 'max')} | {num(e, 'median', 1)} ({num(e, 'p90', 1)}) | "
                 f"{num(h, 'median', 1)} ({num(h, 'p90', 1)}) | {num([r['inference_s'] for r in everything], 'median', 1)} | "
                 f"{num([r['locate_s'] for r in everything], 'median', 1)} | {num([r['peak_gpu_gb'] for r in everything], 'max', 1)} |")
    L += ["", "Model s is VGGT inference as printed by the detector; Locate s is locate.locate. Loading the model and reading the photos are not included.", "",
          "### 5. Match scores and thresholds", "",
          "Share of queries with a match at or above each value. Same room should be high, wrong room should be zero.", "",
          "| Match at or above | " + " | ".join(f"Same room, {n}" for n in sizes) + " | " + " | ".join(f"Wrong room, {n}" for n in sizes)
          + (" | Same room, other lighting, all sizes" if other else "") + " |",
          "| --- |" + " --- |" * (2 * len(sizes) + (1 if other else 0))]
    for t in SWEEP:
        cells = []
        for kind in ("same", "wrong"):
            for n in sizes:
                got = pick(rows, kind, n)
                cells.append(share(sum(r["match"] >= t for r in got), len(got)))
        if other:
            cells.append(share(sum(r["match"] >= t for r in other), len(other)))
        L.append(f"| {t:.2f} | " + " | ".join(cells) + " |")
    L += ["", "| Baseline photos | Same room: min | p10 | median | Wrong room: median | p90 | max |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for n in sizes:
        s, w = [r["match"] for r in pick(rows, "same", n)], [r["match"] for r in pick(rows, "wrong", n)]
        L.append(f"| {n} | {num(s, 'min')} | {num(s, 'p10')} | {num(s, 'median')} | {num(w, 'median')} | {num(w, 'p90')} | {num(w, 'max')} |")
    return "\n".join(L) + "\n"


def read_rows(out):
    path = Path(out) / "results.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


# ----------------------------------------------------------------------------- run
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", help="PASLCD folder (see fetch_paslcd.py)")
    ap.add_argument("--poses", help="folder written by fetch_poses.py; without it positions are not checked")
    ap.add_argument("--out", default="out")
    ap.add_argument("--scenes", nargs="*")
    ap.add_argument("--n-baseline", nargs="*", type=int, default=[24, 12, 8])
    ap.add_argument("--kinds", nargs="*", default=["same", "wrong", "same_other_light"], choices=["same", "wrong", "same_other_light"])
    ap.add_argument("--rounds", type=int, default=9, help="query photos per scene and kind. 9 covers every other scene once; same-room kinds stop after "
                         "12 rounds (12 query photos per scene), wrong-room queries repeat after 36")
    ap.add_argument("--minutes", type=float, default=36, help="stop starting new passes after this long")
    ap.add_argument("--detector-dir", help="folder with locate.py and recon_change.py (default: next to this script, else its parent)")
    ap.add_argument("--detector-commit", default="unknown")
    ap.add_argument("--summarise", action="store_true", help="only rebuild results.md from results.jsonl")
    ap.add_argument("--dump", help="save each pass's reconstruction in this folder instead of scoring it (about 50 MB per pass at 24 photos)")
    ap.add_argument("--from-dump", help="score reconstructions saved with --dump instead of running the model; passes without a file are skipped")
    ap.add_argument("--shard", default="0/1", help="i/n: do only every n-th pass, starting at the i-th")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.summarise:
        meta = json.loads((out / "meta.json").read_text()) if (out / "meta.json").exists() else {}
        text = tables(read_rows(out), meta)
        (out / "results.md").write_text(text)
        print(text)
        return

    started = time.time()
    detector_dir = Path(args.detector_dir) if args.detector_dir else HERE if (HERE / "locate.py").exists() else HERE.parent
    sys.path[:0] = [str(HERE), str(detector_dir)]
    import locate
    import recon_change as rc
    import run_bench as rb
    import torch

    scenes = args.scenes or rb.INDOOR + rb.OUTDOOR
    meta = {
        "dataset": "PASLCD", "date": datetime.datetime.now().isoformat(timespec="seconds"),
        "detector_commit": args.detector_commit, "found": locate.FOUND, "unsure": locate.UNSURE,
        "locate_sha256": hashlib.sha256(Path(locate.__file__).read_bytes()).hexdigest(),
        "recon_change_sha256": hashlib.sha256(Path(rc.__file__).read_bytes()).hexdigest(),
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "args": {k: v for k, v in vars(args).items() if k not in ("data", "out", "poses")},
    }
    print(json.dumps(meta, indent=2), flush=True)
    (out / "meta.json").write_text(json.dumps(meta, indent=2))

    # Photo lists. The 24 baseline photos are the ones run_bench.py uses; smaller baselines are
    # an even pick from those, so every baseline starts with the same photo.
    info = {}
    for scene in scenes:
        base, query, _, _ = rb.scene_pairs(args.data, scene, 1, 24, 12)
        pose_file = Path(args.poses) / f"{scene}_inst1.json" if args.poses else None
        truth = json.loads(pose_file.read_text()) if pose_file and pose_file.exists() else None
        have = truth is not None and all(p.name in truth for p in base)
        info[scene] = {"base": base, "query": query, "truth": truth if have else None,
                       "size": spread(truth, [p.name for p in base]) if have else None,
                       "setting": "indoor" if scene in rb.INDOOR else "outdoor"}
        print(f"{scene}: {len(base)} baseline, {len(query)} query, pose truth "
              f"{'yes' if have else 'NO'}" + (f" for {sum(p.name in truth for p in query)}/{len(query)} queries" if have else ""), flush=True)
        # Instance 2 (indoor scenes): its reference capture was taken under different lighting.
        # Its query photos are the same pictures as instance 1's.
        if "same_other_light" in args.kinds and (Path(args.data) / scene / "Instance_2" / "images").is_dir():
            try:
                base2, query2, _, _ = rb.scene_pairs(args.data, scene, 2, 24, 12)
                pose_file = Path(args.poses) / f"{scene}_inst2.json" if args.poses else None
                truth2 = json.loads(pose_file.read_text()) if pose_file and pose_file.exists() else None
                have2 = truth2 is not None and all(p.name in truth2 for p in base2)
                info[scene]["light2"] = {"base": base2, "query": query2, "truth": truth2 if have2 else None,
                                         "size": spread(truth2, [p.name for p in base2]) if have2 else None}
                print(f"{scene} instance 2: {len(base2)} baseline, {len(query2)} query, pose truth {'yes' if have2 else 'NO'}", flush=True)
            except Exception as e:
                print(f"{scene} instance 2 skipped: {type(e).__name__}: {e}", flush=True)

    # Photos are shrunk once to the size the model works at (the same resize reconstruct does).
    shapes, small = {}, {}

    def load(path, hw=None):
        img = cv2.imread(str(path))
        if img is None:
            raise RuntimeError(f"Cannot read {path}")
        shapes[path] = img.shape[:2]
        H, W = hw or rc.fit_size(*img.shape[:2])
        small[(path, H, W)] = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)
        return H, W

    def photo(path, hw):
        if (path, *hw) not in small:
            load(path, hw)
        return small[(path, *hw)]

    t0 = time.time()
    for scene in [] if args.from_dump else scenes:
        used = [info[scene]] if set(args.kinds) - {"same_other_light"} else []
        for s in used + ([info[scene]["light2"]] if "light2" in info[scene] else []):
            s["hw"] = load(s["base"][0])
            with ThreadPoolExecutor(4) as pool:
                list(pool.map(lambda p, hw=s["hw"]: load(p, hw), s["base"][1:] + s["query"]))
    print(f"Read {len(small)} photos in {time.time() - t0:.0f} s", flush=True)

    tasks = []
    for k in range(args.rounds):
        for n in args.n_baseline:
            for i, scene in enumerate(scenes):
                s = info[scene]
                if "same" in args.kinds and k < min(12, len(s["query"])):  # no photo is used twice
                    tasks.append(("same", n, scene, scene, s["query"][ORDER[k % 12] % len(s["query"])], k))
                if len(scenes) > 1 and "wrong" in args.kinds:
                    wrong = scenes[(i + 1 + k % (len(scenes) - 1)) % len(scenes)]
                    photos = info[wrong]["query"]
                    tasks.append(("wrong", n, scene, wrong, photos[(ORDER[k % 12] + i) % len(photos)], k))
                if "light2" in s and k < min(12, len(s["light2"]["query"])):
                    photos = s["light2"]["query"]
                    tasks.append(("same_other_light", n, scene, scene, photos[ORDER[k % 12] % len(photos)], k))
    rows = read_rows(out)
    done = {(r["kind"], r["n_baseline"], r["scene"], r["round"]) for r in rows}
    shard, of = (int(v) for v in args.shard.split("/"))
    tasks = tasks[shard::of]
    stored = Path(args.dump or args.from_dump) if args.dump or args.from_dump else None
    if args.dump:
        stored.mkdir(parents=True, exist_ok=True)
        done = {t[:3] + (t[5],) for t in tasks if (stored / f"{t[0]}_{t[1]}_{t[2]}_{t[5]}.npz").exists()}
    print(f"{len(tasks)} passes planned, {len(done)} already done", flush=True)

    model = None if args.from_dump else rc.load_model()
    with open(out / "results.jsonl", "a") as sink:
        for kind, n, scene, query_scene, query, k in tasks:
            if (kind, n, scene, k) in done:
                continue
            if time.time() - started > args.minutes * 60:
                print(f"Stopping: {args.minutes} minutes used", flush=True)
                break
            s = info[scene]["light2"] if kind == "same_other_light" else info[scene]
            base = rb.pick_even(s["base"], n)
            row = {"kind": kind, "n_baseline": len(base), "scene": scene, "setting": info[scene]["setting"],
                   "query_scene": query_scene, "query": query.name, "round": k}
            file = stored / f"{kind}_{n}_{scene}_{k}.npz" if stored else None
            if args.from_dump and not file.exists():
                continue
            try:
                if args.from_dump:
                    saved = np.load(file)
                    recon = rc.Recon(saved["depth"], saved["conf"], saved["K"], saved["E"], None, saved["feat"] if "feat" in saved else None)
                    row.update(json.loads(str(saved["meta"])))
                else:
                    frames = [photo(p, s["hw"]) for p in base + [query]]
                    h, w = shapes[query]
                    row["squashed"] = bool(abs(w / h - s["hw"][1] / s["hw"][0]) > 0.03)  # the query has another shape than the baseline
                    log = io.StringIO()
                    t0 = time.time()
                    with contextlib.redirect_stdout(log):
                        recon = rc.reconstruct(model, frames)
                    row["reconstruct_s"] = round(time.time() - t0, 2)
                    printed = re.search(r"inference ([\d.]+) s", log.getvalue())
                    row["inference_s"] = float(printed.group(1)) if printed else None
                    row["peak_gpu_gb"] = round(torch.cuda.max_memory_allocated() / 1e9, 1) if torch.cuda.is_available() else None
                if args.dump:  # everything locate reads, without the pictures
                    part = file.with_suffix(".part.npz")
                    np.savez(part, depth=recon.depth, conf=recon.conf, K=recon.K, E=recon.E, meta=json.dumps(row),
                             **({} if recon.feat is None else {"feat": recon.feat}))
                    part.rename(file)
                    print(f"round {k} n={len(base)} {kind} {scene} <- {query_scene}/{query.name}: saved, model {row['inference_s']} s", flush=True)
                    continue
                t0 = time.time()
                result, _, _ = locate.locate(recon, list(range(len(base))), [len(base)])
                row["locate_s"] = round(time.time() - t0, 2)
                row.update(state=result["state"], match=result["confidence"], position=result["position"],
                           heading_deg=result["heading_deg"], metres_per_unit=result["metres_per_unit"],
                           looks_alike=result.get("looks_alike"), match_by_depth=result.get("match_by_depth"),
                           look_limit=result.get("look_limit"))
                if kind != "wrong":
                    row["pose"] = pose_check(locate.camera, recon.E, [p.name for p in base + [query]], s["truth"], s["size"],
                                             result["metres_per_unit"])
                shown = f" | position error {100 * row['pose']['position_error_share']:.1f}%, heading error " \
                        f"{row['pose']['heading_error_deg']:.1f} deg" if row.get("pose") else ""
                print(f"round {k} n={len(base)} {kind} {scene} <- {query_scene}/{query.name}: {row['state']} {row['match']:.2f} | "
                      f"model {row['inference_s']} s, locate {row['locate_s']} s{shown}", flush=True)
            except Exception as e:  # keep going: one bad pass must not lose the rest
                row["error"] = f"{type(e).__name__}: {e}"
                print(f"FAILED {kind} {scene} n={n} round {k}: {row['error']}", flush=True)
            rows.append(row)
            sink.write(json.dumps(row) + "\n")
            sink.flush()
    if args.dump:
        return
    text = tables(rows, meta)
    (out / "results.md").write_text(text)
    print("\n" + text)


if __name__ == "__main__":
    main()
