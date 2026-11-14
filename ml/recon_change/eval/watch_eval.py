#!/usr/bin/env python3
"""Measure watch.py (watched spots) on PASLCD, from saved reconstructions. CPU only.

For one scene:
  changed pair    every labelled changed region of every query photo becomes a watched spot: its
                  3D points (query depth inside the mask blob) are projected into the baseline
                  frames, and the box around them in the best baseline frame is the spot
                  (positive). For each positive, a box of the same size is put at a random place
                  of a random baseline frame whose 3D region lands in the same query photo and
                  near no labelled change in any query photo (negative).
  unchanged pair  the same boxes, checked against 12 other photos of the baseline capture.
                  Every one is a negative.
Each spot is measured against the query photo its region was labelled in ("single": the region's
pixel size in that photo is what the size bands mean) and against the three best of all 12
query photos ("multi": what a live check would use). The comparison is repeated on the photos at
1, 2 and 4 times the model's resolution and at their original size.

Nothing is decided here: the raw measurements go to <out>/<scene>_inst<k>.json and
watch_report.py turns them into scores, limits and tables.

    python watch_eval.py --recon recall/out/base --data bench/data/PASLCD --scene Lounge --out watch/out/eval
"""
import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import recon_change as rc  # noqa: E402
import run_bench as rb  # noqa: E402
import watch  # noqa: E402

SCALES = {"x1": 1.0, "x2": 2.0, "x4": 4.0, "full": None}
MIN_REGION = 50  # pixels, as in RESULTS.md
HIT_SHARE = 0.10


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_recon(folder):
    s = np.load(Path(folder) / "recon.npz")
    recon = rc.Recon(*(s[k] for k in ("depth", "conf", "K", "E", "rgb")), s["feat"] if "feat" in s else None)
    return recon, int(s["n_before"]), s["gt"], [str(n) for n in s["photos"]]


def read_rgb(paths):
    return [cv2.cvtColor(img, cv2.COLOR_BGR2RGB) for img in rb.read_images(paths)]


def clean(x):
    """JSON-ready: numpy numbers to Python, NaN to None."""
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, np.ndarray):
        return clean(x.tolist())
    return x


def project(scene, pts, j):
    """World points in frame j: u, v (pixel i covers [i, i + 1)), z, inside."""
    K, E = scene.r.K[j], scene.r.E[j]
    cam = pts @ E[:, :3].T + E[:, 3]
    z = cam[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        u = K[0, 0] * cam[:, 0] / z + K[0, 2] + 0.5
        v = K[1, 1] * cam[:, 1] / z + K[1, 2] + 0.5
    return u, v, z, (z > 1e-6) & (u >= 0) & (u < scene.W) & (v >= 0) & (v < scene.H)


def region_spot(scene, q, mask, base):
    """A labelled region of query frame q as a box in a baseline frame: the baseline frame that
    sees the place from the most similar direction, among those that have it fully in view, not
    hidden, and not much smaller. None if there is none."""
    H, W = scene.H, scene.W
    ys, xs = np.nonzero(mask & (scene.r.depth[q] > 0))
    if len(ys) < 10:
        return None
    pts = scene.points(q)[ys, xs]
    if len(pts) > 800:
        pts = pts[np.linspace(0, len(pts) - 1, 800).round().astype(int)]
    centroid = pts.mean(0)
    box_q = (xs.max() - xs.min() + 1) * (ys.max() - ys.min() + 1)
    best = None
    for b in base:
        u, v, z, inside = project(scene, pts, b)
        if inside.mean() < 0.95:
            continue
        u, v, z = u[inside], v[inside], z[inside]
        seen = scene.r.depth[b][v.astype(np.int64), u.astype(np.int64)]
        if np.median((seen - z) / z) < -0.3:  # the baseline frame has something well in front of the place
            continue
        x0, x1 = np.percentile(u, [2, 98])
        y0, y1 = np.percentile(v, [2, 98])
        x0, y0, x1, y1 = max(0.0, x0 - 1), max(0.0, y0 - 1), min(float(W), x1 + 1), min(float(H), y1 + 1)
        if (x1 - x0) * (y1 - y0) < 0.5 * box_q:
            continue
        a, c = centroid - scene.centre[q], centroid - scene.centre[b]
        cos = float(a @ c / (np.linalg.norm(a) * np.linalg.norm(c) + 1e-9))
        if best is None or cos > best[0]:
            best = (cos, {"frame": int(b), "box": [x0 / W, y0 / H, x1 / W, y1 / H]})
    return best[1] if best else None


def near_change(scene, geo, gts, after, margin):
    """Does the spot's 3D region land on or near a labelled change in any query photo?"""
    H, W = scene.H, scene.W
    for i, j in enumerate(after):
        u, v, _, inside = project(scene, geo["points"], j)
        if inside.sum() < 3:
            continue
        x0, x1 = int(max(0, u[inside].min() - margin)), int(min(W, u[inside].max() + margin + 1))
        y0, y1 = int(max(0, v[inside].min() - margin)), int(min(H, v[inside].max() + margin + 1))
        if gts[i][y0:y1, x0:x1].any():
            return True
    return False


def matched_negative(scene, positive, q, gts, base, after, rng, tries=150):
    """A box of the positive's size somewhere else: it must land in query frame q and stay clear
    of every labelled change. Random boxes mostly fall on bare walls and floors, which are easy,
    so the first candidate with at least 0.7 times the positive's texture is taken; if there is
    none, the most textured candidate."""
    u0, v0, u1, v1 = positive["box"]
    w, h = u1 - u0, v1 - v0
    if w >= 0.98 or h >= 0.98:
        return None
    want = 0.7 * texture(scene, positive)
    margin = max(8.0, 0.5 * float(np.sqrt(w * scene.W * h * scene.H)))
    best = None
    for _ in range(tries):
        b = int(rng.choice(base))
        x, y = rng.uniform(0.005, 0.995 - w), rng.uniform(0.005, 0.995 - h)
        spot = {"frame": b, "box": [float(x), float(y), float(x + w), float(y + h)]}
        tex = texture(scene, spot)
        if best is not None and tex <= best[0]:
            continue
        geo = watch.spot_geometry(scene, {"name": "", **spot})
        if len(geo["points"]) < 0.5 * geo["area"] or len(geo["points"]) < 10:
            continue
        if project(scene, geo["points"], q)[3].mean() < 0.95:
            continue
        if near_change(scene, geo, gts, after, margin):
            continue
        best = (tex, spot)
        if tex >= want:
            break
    return best[1] if best else None


def texture(scene, spot):
    """How much there is to see inside the box: spread of the colour, in Lab units."""
    H, W = scene.H, scene.W
    u0, v0, u1, v1 = spot["box"]
    crop = scene.r.rgb[spot["frame"]][int(v0 * H):max(int(v1 * H), int(v0 * H) + 2), int(u0 * W):max(int(u1 * W), int(u0 * W) + 2)]
    return float(cv2.cvtColor(np.ascontiguousarray(crop), cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32).std(0).mean())


def measure(scene, spot, base, new, q):
    """Everything watch.py measures for one spot, the way watch.check goes about it: against
    query frame q alone, against the best of all new frames, and the baseline's own
    disagreement, at every resolution. No angle limit here: the angle is recorded and
    watch_report.py applies watch.MAX_ANGLE."""
    geo = watch.spot_geometry(scene, {"name": "", **spot})
    out = {"points": len(geo["points"]), "own": {}, "single": {}, "multi": [], "view": None, "multi_why": None}
    if not len(geo["points"]):
        return out
    refs = watch.references(scene, geo, base)
    out["references"] = len(refs)
    usual = {}

    def own(ref, key):
        if (ref["frame"], key) not in usual:
            usual[ref["frame"], key] = watch.baseline_disagreement(scene, ref, base, 3, SCALES[key])
        return usual[ref["frame"], key]

    if q is not None:
        good, poor = watch.looks_at(scene, refs, [q], 360.0)
        if good or poor:
            out["view"], ref = (good or poor)[0]
            out["ref"] = {"frame": ref["frame"], "box": [ref["box"][0] / scene.W, ref["box"][1] / scene.H,
                                                         ref["box"][2] / scene.W, ref["box"][3] / scene.H]}
            out["fx"] = float(scene.r.K[q][0, 0])
            if good:
                for key, scale in SCALES.items():
                    out["single"][key] = watch.compare(scene, ref, q, scale)
                    out["own"][key] = own(ref, key)
    good, poor = watch.looks_at(scene, refs, new, 360.0)
    if not good:
        out["multi_why"] = watch.why_not_seen(poor) if poor else "out of view"
    # the best looks first; enough of them that three remain after an angle limit
    for v, ref in good[:6]:
        out["multi"].append({"view": v, "ref": ref["frame"], "x1": watch.compare(scene, ref, v["frame"], 1.0), "own_x1": own(ref, "x1")})
    return out


def box_share(mask, box):
    """Share of a 0..1 box of a picture that a mask covers."""
    H, W = mask.shape
    u0, v0, u1, v1 = box
    crop = mask[int(v0 * H):max(int(v1 * H), int(v0 * H) + 1), int(u0 * W):max(int(u1 * W), int(u0 * W) + 1)]
    return float(crop.mean()) if crop.size else 0.0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recon", required=True, help="folder with <scene>_inst<k>_changed/recon.npz (saved by the recall work)")
    ap.add_argument("--data", required=True, help="PASLCD folder, for the original photos")
    ap.add_argument("--scene", required=True)
    ap.add_argument("--instance", type=int, default=1)
    ap.add_argument("--out", required=True)
    ap.add_argument("--whole-room", help="folder with the recon_change.py and run_bench.py of the recall work, for the "
                                         "whole-room detector on the same regions (geometry, and geometry + looks different)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    cv2.setNumThreads(4)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tag = f"{args.scene}_inst{args.instance}"
    rng = np.random.default_rng(args.seed + sum(map(ord, tag)))
    baseline, query, _, held_back = rb.scene_pairs(args.data, args.scene, args.instance, 24, 12)
    t0 = time.time()

    # ------------------------------------------------------------------ changed pair
    recon, n_before, gts, names = load_recon(Path(args.recon) / f"{tag}_changed")
    if names != [p.name for p in query]:
        raise SystemExit(f"{tag}: the saved reconstruction is of other photos than scene_pairs gives")
    base, after = list(range(n_before)), list(range(n_before, len(recon.depth)))
    base_photos = read_rgb(baseline)
    scene = watch.Scene(recon, base_photos + read_rgb(query))
    whole = {}
    if args.whole_room:
        rc_old = load_module("rc_recall", Path(args.whole_room) / "recon_change.py")
        rb_old = load_module("rb_recall", Path(args.whole_room) / "run_bench.py")
        _, detail = rc_old.analyse(rc_old.Recon(recon.depth, recon.conf, recon.K, recon.E, recon.rgb, recon.feat), n_before)
        whole = rb_old.predicted_masks(recon, detail, 5)
    print(f"{tag} changed: loaded, whole-room detector done, {time.time() - t0:.0f} s", flush=True)

    rows, no_spot, no_negative = [], 0, 0
    for i, q in enumerate(after):
        n, labels, stats, _ = cv2.connectedComponentsWithStats(gts[i].astype(np.uint8), connectivity=8)
        for k in range(1, n):
            area = int(stats[k, cv2.CC_STAT_AREA])
            if area < MIN_REGION:
                continue
            blob = labels == k
            hit = {name: bool((mask & blob).sum() >= HIT_SHARE * area) for name, mask in
                   (("geometry", whole[q][0] | whole[q][1]), ("with_looks", whole[q][0] | whole[q][1] | whole[q][2]))} if whole else {}
            spot = region_spot(scene, q, blob, base)
            common = {"scene": args.scene, "instance": args.instance, "pair": "changed", "photo": names[i], "q": int(q),
                      "region_area": area, "region_side": [int(stats[k, cv2.CC_STAT_WIDTH]), int(stats[k, cv2.CC_STAT_HEIGHT])]}
            if spot is None:
                no_spot += 1
                rows.append({**common, "label": 1, "spot": None, "whole_room": hit})
                continue
            rows.append({**common, "label": 1, "spot": spot, "texture": texture(scene, spot), "whole_room": hit,
                         **measure(scene, spot, base, after, q)})
            negative = matched_negative(scene, spot, q, gts, base, after, rng)
            if negative is None:
                no_negative += 1
                continue
            m = measure(scene, negative, base, after, q)
            flagged = {}
            if whole and m["view"] is not None and m["view"]["box"] is not None:
                flagged = {"geometry": box_share(whole[q][0] | whole[q][1], m["view"]["box"]) >= HIT_SHARE,
                           "with_looks": box_share(whole[q][0] | whole[q][1] | whole[q][2], m["view"]["box"]) >= HIT_SHARE}
            rows.append({**common, "label": 0, "spot": negative, "texture": texture(scene, negative), "whole_room": flagged, **m})
        print(f"{tag} changed: photo {i + 1}/{len(after)}, {len(rows)} spots, {time.time() - t0:.0f} s", flush=True)
    spots = [(row["spot"], row["label"], row["region_area"]) for row in rows if row["spot"] is not None]

    # ------------------------------------------------------------------ unchanged pair: the same boxes
    folder = Path(args.recon) / f"{tag}_unchanged"
    if (folder / "recon.npz").exists():
        del scene
        recon, n_before, _, names = load_recon(folder)
        if names != [p.name for p in held_back]:
            raise SystemExit(f"{tag}: the saved unchanged reconstruction is of other photos than scene_pairs gives")
        base, after = list(range(n_before)), list(range(n_before, len(recon.depth)))
        scene = watch.Scene(recon, base_photos + read_rgb(held_back))
        for n, (spot, was, area) in enumerate(spots):
            geo = watch.spot_geometry(scene, {"name": "", **spot})
            q = None
            if len(geo["points"]):
                good, poor = watch.looks_at(scene, watch.references(scene, geo, base), after, 360.0)
                if good:
                    q = good[int(rng.integers(len(good)))][0]["frame"]  # any clear look, not the best one
                elif poor:
                    q = max(poor, key=lambda x: x[0]["inside"])[0]["frame"]
            rows.append({"scene": args.scene, "instance": args.instance, "pair": "unchanged", "q": q, "label": 0,
                         "box_of": "positive" if was else "negative", "region_area": area, "spot": spot,
                         "texture": texture(scene, spot), "whole_room": {}, **measure(scene, spot, base, after, q)})
            if n % 40 == 0:
                print(f"{tag} unchanged: {n}/{len(spots)} spots, {time.time() - t0:.0f} s", flush=True)

    result = {"scene": args.scene, "instance": args.instance, "regions_without_baseline_view": no_spot,
              "positives_without_negative": no_negative, "seconds": round(time.time() - t0), "rows": rows}
    (out / f"{tag}.json").write_text(json.dumps(clean(result)))
    print(f"{tag}: {len(rows)} rows, {no_spot} regions without a baseline view, {no_negative} without a negative, "
          f"{time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
