#!/usr/bin/env python3
"""Accuracy of phone_side.py on the walking test (the queries of locate_walk_eval.py, TUM RGB-D).

Two steps, both on CPU cores:

    --extract VARIANT   run the small model once over every baseline photo and every query frame
                        and keep the features (phone/feat/VARIANT/...). Also notes seconds per frame.
    --score VARIANT     build each room's map (12 and 24 baseline photos) from the kept features and
                        the reconstruction of phone_side_recon.py, then run every query of the
                        walking test (537 same room, 178 wrong room) through match + pose for a small
                        grid of settings. One line per query and setting in out/VARIANT.jsonl, with
                        the position and heading error against motion capture. No limits are applied
                        here: phone_side_tables.py chooses them on two rooms and tests on the third.

    python phone_side_eval.py --extract vits --data walk/data/tum --walk walk/out --work phone --threads 8
    python phone_side_eval.py --score vits --data walk/data/tum --walk walk/out --work phone --procs 32

Variants: vits, vitb (DINOv2 ViT-S/14, ViT-B/14 at 518 px), vits_int8, vits_392, xfeat, sift.

Error definition, as in LOCATE_WALK_RESULTS.md: the map's baseline cameras are fitted to their
true positions with one similarity transform, then the query camera is compared. Here that
transform is fitted once per map, not once per fix: the map is the same for every fix.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import phone_side as ps  # noqa: E402

SIZES = (12, 24)
# Settings tried per variant: closest photos k (0 = all), pixels a match may be off, lowest similarity,
# ratio to the second closest, reduced descriptor size, sub-patch placing. The grids were widened
# once after a first run (PHONE_SIDE_RESULTS.md, "How this was measured"): the first run's best
# settings sat at the edge of the grid (the most pixels off that were tried).
GRIDS = {
    "dino": [dict(k=k, reproj=r, min_sim=m, ratio=1.0, dim=d, refine=f)
             for k in (3, 6) for r in (12.0, 16.0, 24.0) for m in (0.0, 0.6) for d in (0, 64) for f in (False, True)],
    "xfeat": [dict(k=k, reproj=r, min_sim=m, ratio=1.0, dim=0, refine=False) for k in (3, 0) for r in (3.0, 6.0, 10.0, 15.0) for m in (0.7, 0.82, 0.9)
              if (k == 0 and m > 0.8) or r in (6.0, 10.0)],
    "sift": [dict(k=0, reproj=r, min_sim=0.0, ratio=q, dim=0, refine=False) for r in (3.0, 6.0, 10.0, 15.0) for q in (0.7, 0.8, 0.9)],
}


def family(variant):
    return "dino" if variant.startswith("vit") else variant.split("_")[0].rstrip("0123456789")


def make_extractor(variant, work, base_hub):
    def snapshot(hub, name):
        return sorted((Path(hub) / f"models--facebook--{name}" / "snapshots").iterdir())[-1]

    parts = variant.split("_")
    if parts[0] in ("vits", "vitb"):
        folder = snapshot(Path(work) / "hf" / "hub", "dinov2-small") if parts[0] == "vits" else snapshot(base_hub, "dinov2-base")
        long_side = next((int(p) for p in parts[1:] if p.isdigit()), 518)
        precision = next((p for p in parts[1:] if p in ("int8", "bf16")), "float32")
        return ps.DinoExtractor(folder, long_side, precision)
    if parts[0].startswith("xfeat"):
        top_k = int(parts[0][5:] or 2048)
        return ps.XFeatExtractor(Path(work) / "xfeat", top_k, next((int(p) for p in parts[1:] if p.isdigit()), 0))
    if parts[0].startswith("sift"):
        return ps.SiftExtractor(int(parts[0][4:] or 2048))
    raise SystemExit(f"unknown variant {variant}")


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


def walk_keys(walk):
    """The fixes the walking test ran with 12 baseline photos and 1 query frame: (kind, room, query room, query)."""
    keys = []
    for line in (Path(walk) / "results.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            if r["key"][1] == 12 and r["key"][2] == 1 and "error" not in r:
                keys.append((r["kind"], r["room"], r["query_room"], r["query"]))
    return keys


def feature_file(work, variant, name):
    return Path(work) / "feat" / variant / (name.replace("/", "__") + ".npz")


def save_features(path, f, seconds):
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part.npz")
    np.savez(part, size=np.asarray(f.size), xy=f.xy, desc=f.desc.astype(np.float16), glob=np.zeros(0) if f.glob is None else f.glob,
             grid=np.asarray(f.grid or (0, 0)), reach=f.reach, seconds=seconds)
    part.rename(path)


def load_features(path):
    z = np.load(path)
    grid = tuple(int(v) for v in z["grid"])
    return ps.Features(tuple(int(v) for v in z["size"]), z["xy"], ps.unit(z["desc"].astype(np.float32)), z["glob"].astype(np.float32) if len(z["glob"]) else None,
                       grid if grid[0] else None, float(z["reach"]))


# ----------------------------------------------------------------------------- extract
def extract(args):
    import torch

    torch.set_num_threads(args.threads)
    cv2.setNumThreads(args.threads)
    plan = json.loads((Path(args.walk) / "plan.json").read_text())
    todo = {}
    for room, r in plan["rooms"].items():
        for i, b in enumerate(r["baseline"]):
            todo[f"base_{room}_{i}"] = (b, True)
    for _, _, query_room, qi in walk_keys(args.walk):
        q = plan["rooms"][query_room]["queries"][qi]
        todo[f"{q['rec']}/{q['rgb']}"] = (q, False)
    if args.dense:  # every --dense-th frame of the recordings the queries came from, for the replay
        from locate_walk_eval import recording
        for room, r in plan["rooms"].items():
            for rec in sorted({q["rec"] for q in r["queries"]}):
                for f in recording(Path(args.data) / rec)[::args.dense]:
                    todo.setdefault(f"{rec}/{f['rgb']}", ({"rec": rec, "rgb": f["rgb"]}, False))
    extractor = make_extractor(args.extract, args.work, args.base_hub)
    print(f"{args.extract}: {len(todo)} frames, {torch.get_num_threads()} threads", flush=True)
    times = []
    for n, (name, (entry, stored)) in enumerate(sorted(todo.items())):
        path = feature_file(args.work, args.extract, name)
        if path.exists():
            continue
        img = cv2.imread(str(Path(args.data) / entry["rec"] / entry["rgb"]))
        if stored:  # a baseline photo as live/worker.py keeps it
            img = cv2.imdecode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])[1], cv2.IMREAD_COLOR)
        t0 = time.perf_counter()
        f = extractor.extract(img)
        times.append(time.perf_counter() - t0)
        save_features(path, f, times[-1])
        if n % 100 == 0:
            print(f"  {n}/{len(todo)} {name}: {len(f.xy)} features, {times[-1]:.3f} s", flush=True)
    if times:
        print(f"{args.extract}: median {np.median(times[3:] or times):.3f} s per frame on {args.threads} threads", flush=True)


# ----------------------------------------------------------------------------- score
def load_recon(work, room, n):
    z = np.load(Path(work) / "recon" / f"{room}_{n}.npz")
    return z["depth"], z["conf"], z["K"], z["E"], [int(i) for i in z["pick"]]


def room_map(work, variant, plan, room, n, dim):
    depth, conf, K, E, pick = load_recon(work, room, n)
    feats = [load_features(feature_file(work, variant, f"base_{room}_{i}")) for i in pick]
    m = ps.build_map(feats, depth, conf, K, E, kind=variant, dim=dim)
    P = np.array([-e[:, :3].T @ e[:, 3] for e in E.astype(np.float64)])
    G = np.array([plan["rooms"][room]["baseline"][i]["C"] for i in pick])
    s, R, t = similarity(P, G)
    fit_rms = float(np.sqrt((((s * P @ R.T + t) - G) ** 2).sum(1).mean()))
    return m, (s, R, t), fit_rms


def score_part(job):
    variant, work, walk, n, room, part, parts = job
    cv2.setNumThreads(1)
    plan = json.loads((Path(walk) / "plan.json").read_text())
    keys = [k for k in walk_keys(walk) if k[1] == room][part::parts]
    grid = GRIDS[family(variant)]
    maps = {dim: room_map(work, variant, plan, room, n, dim) for dim in sorted({g["dim"] for g in grid})}
    rows = []
    for kind, _, query_room, qi in keys:
        q = plan["rooms"][query_room]["queries"][qi]
        f = load_features(feature_file(work, variant, f"{q['rec']}/{q['rgb']}"))
        matched = {}  # the matches do not depend on reproj: match once per (k, min_sim, ratio, dim, refine)
        for g in grid:
            m, (s, R, t), _ = maps[g["dim"]]
            key = (g["k"], g["min_sim"], g["ratio"], g["dim"], g["refine"])
            if key not in matched:
                t0 = time.perf_counter()
                photos, best_sim = ps.nearest_photos(m, f.glob, g["k"])
                xy, xyz, _ = ps.match(m, f, photos, g["min_sim"], g["ratio"], g["refine"])
                matched[key] = (xy, xyz, best_sim, time.perf_counter() - t0)
            xy, xyz, best_sim, match_s = matched[key]
            t0 = time.perf_counter()
            out = ps.pose_frame(m, f, xy, xyz, reproj=g["reproj"])
            row = {"variant": variant, "n": n, **g, "kind": kind, "room": room, "query_room": query_room, "query": qi,
                   "matches": out["matches"], "inliers": out["inliers"], "spread": out["spread"], "best_sim": best_sim,
                   "reach": None if out["reach"] is None else round(out["reach"], 3),
                   "match_s": round(match_s, 4), "pose_s": round(time.perf_counter() - t0, 4)}
            if out["position"] is not None and kind == "same":
                row["pos_err"] = float(np.linalg.norm(s * R @ out["position"] + t - np.array(q["C"])))
                row["head_err"] = angle(R @ out["forward"], np.array(q["R"])[:, 2])
            rows.append(row)
    return rows


def score(args):
    from multiprocessing import Pool

    out = Path(args.work) / "out"
    out.mkdir(parents=True, exist_ok=True)
    plan = json.loads((Path(args.walk) / "plan.json").read_text())
    rooms = list(plan["rooms"])
    info = {"variant": args.score, "maps": {}}
    (Path(args.work) / "maps").mkdir(exist_ok=True)
    for n in SIZES:
        for room in rooms:
            for dim in sorted({g["dim"] for g in GRIDS[family(args.score)]}):
                m, (s, _, _), fit_rms = room_map(args.work, args.score, plan, room, n, dim)
                path = Path(args.work) / "maps" / f"{args.score}_{room}_{n}_d{dim}.npz"
                ps.save_map(path, m)
                info["maps"][f"{room}_{n}_d{dim}"] = {"bytes": path.stat().st_size, "features": int(len(m.xyz)), "photos": n,
                                                     "desc_dim": int(m.desc.shape[1]), "fit_rms_m": round(fit_rms, 4), "metres_per_unit": round(s, 4)}
                print(f"map {path.name}: {path.stat().st_size / 1e6:.2f} MB, {len(m.xyz)} features, baseline cameras fit the truth to {fit_rms:.3f} m", flush=True)
    # Which room is this? The best global similarity of every query frame to each room's photos.
    seen, recognise = set(), []
    for n in SIZES:
        globs = {}
        for room in rooms:
            pick = load_recon(args.work, room, n)[4]
            g = [load_features(feature_file(args.work, args.score, f"base_{room}_{i}")).glob for i in pick]
            globs[room] = None if any(v is None for v in g) else np.stack(g)
        if any(v is None for v in globs.values()):
            break
        for _, _, query_room, qi in walk_keys(args.walk):
            if (n, query_room, qi) in seen:
                continue
            seen.add((n, query_room, qi))
            q = plan["rooms"][query_room]["queries"][qi]
            f = load_features(feature_file(args.work, args.score, f"{q['rec']}/{q['rgb']}"))
            recognise.append({"n": n, "room": query_room, "query": qi, "sims": {room: float((globs[room] @ f.glob).max()) for room in rooms}})
    info["recognise"] = recognise
    (out / f"{args.score}_info.json").write_text(json.dumps(info))
    jobs = [(args.score, args.work, args.walk, n, room, part, args.parts) for n in SIZES for room in rooms for part in range(args.parts)]
    t0 = time.time()
    with Pool(args.procs) as pool, open(out / f"{args.score}.jsonl", "w") as sink:
        for i, rows in enumerate(pool.imap_unordered(score_part, jobs)):
            for row in rows:
                sink.write(json.dumps(row) + "\n")
            print(f"  part {i + 1}/{len(jobs)} done, {time.time() - t0:.0f} s", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extract", help="variant: keep its features of every baseline photo and query frame")
    ap.add_argument("--score", help="variant: match and solve every query for the grid of settings")
    ap.add_argument("--data", default="walk/data/tum")
    ap.add_argument("--walk", default="walk/out", help="folder with plan.json and results.jsonl of the walking test")
    ap.add_argument("--work", default="phone", help="folder with recon/, hf/, xfeat/; feat/, maps/ and out/ are written here")
    ap.add_argument("--base-hub", default="items/hf/hub", help="Hugging Face cache that holds facebook/dinov2-base")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--procs", type=int, default=max(1, len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else 4))
    ap.add_argument("--parts", type=int, default=8)
    ap.add_argument("--dense", type=int, default=0, help="with --extract: also every this-many-th frame of the query recordings")
    args = ap.parse_args()
    if args.extract:
        extract(args)
    if args.score:
        score(args)


if __name__ == "__main__":
    main()
