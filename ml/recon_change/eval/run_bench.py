#!/usr/bin/env python3
"""Score recon_change.py on real before/after captures that come with change masks.

Data: PASLCD (see fetch_paslcd.py). Each scene has a baseline capture and a query capture of
the same place in a different state, taken on independent paths, with a hand-drawn change mask
for every query photo. Two kinds of pair are scored:

  changed    24 baseline photos against 12 query photos with masks
  unchanged  the same 24 baseline photos against 12 other photos of the baseline capture.
             Nothing changed, so every flagged pixel is a false alarm.

For each pair the detector runs exactly as in production (rc.reconstruct, then rc.analyse).
The predicted change mask of a query photo is
  appeared: the pixels of that photo that the detector put in a kept cluster
  gone:     the 3D points of kept "gone" clusters (found on baseline photos), projected into
            the query photo and dilated, unless the query photo has a surface in front of them
and it is compared with the ground-truth mask at the model's resolution.

    python run_bench.py --data data/PASLCD --out out/paslcd --pairs changed unchanged
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
INDOOR = ["Cantina", "Lounge", "Printing_area", "Lunch_room", "Meeting_room"]
OUTDOOR = ["Garden", "Pots", "Zen", "Playground", "Porch"]
IMG_EXT = {".jpg", ".jpeg", ".png"}
HIDDEN = 0.10  # a gone point is hidden in a query photo if that photo's surface is this much nearer
YELLOW = (0, 230, 255)  # BGR


# ----------------------------------------------------------------------------- data
def pick_even(items, n):
    """n items spread evenly over a sorted list (same rule as fetch_paslcd.py)."""
    if len(items) <= n:
        return list(items)
    step = (len(items) - 1) / (n - 1)
    return [items[i] for i in sorted({int(round(k * step)) for k in range(n)})]


def scene_pairs(root, scene, instance, n_baseline, n_query):
    """Photo lists for one scene: baseline, query (with masks) and the unchanged-control query.

    A pool of 1.5 x n_baseline photos is spread evenly over the baseline capture. Every third
    one is held back as the unchanged control; the other two thirds are the baseline."""
    base = Path(root) / scene / f"Instance_{instance}"
    images = sorted(p for p in (base / "images").iterdir() if p.suffix.lower() in IMG_EXT)
    masks = {p.stem: p for p in (base / "gt_mask").glob("*.png")}
    pool = pick_even([p for p in images if "_test_" not in p.name], n_baseline * 3 // 2)
    held_back = pool[1::3][:n_query]
    baseline = [p for p in pool if p not in held_back]
    query = pick_even([p for p in images if "_test_" in p.name and p.stem in masks], n_query)
    return baseline, query, [masks[p.stem] for p in query], held_back


def read_images(paths):
    with ThreadPoolExecutor(4) as pool:  # OpenCV decodes outside the Python lock
        images = list(pool.map(lambda p: cv2.imread(str(p)), paths))
    for p, img in zip(paths, images):
        if img is None:
            raise RuntimeError(f"Cannot read {p}")
    return images


def read_mask(path, photo_shape, H, W):
    """Ground-truth mask at the model's resolution (nearest neighbour). Non-zero = changed."""
    m = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if m is None:
        raise RuntimeError(f"Cannot read {path}")
    if (m.shape[0] > m.shape[1]) != (photo_shape[0] > photo_shape[1]):
        raise RuntimeError(f"{path.name} is {m.shape[1]}x{m.shape[0]} but its photo is "
                           f"{photo_shape[1]}x{photo_shape[0]}: orientation differs")
    return cv2.resize(m, (W, H), interpolation=cv2.INTER_NEAREST) > 0


# ----------------------------------------------------------------------------- prediction
def predicted_masks(recon, detail, fill):
    """{query frame: (appeared mask, gone mask)} at the model's resolution."""
    _, H, W = recon.depth.shape
    kernel = np.ones((fill, fill), np.uint8)
    appeared, gone = detail["appeared"], detail["gone"]
    kept = appeared["member"] >= 0
    gone_points = gone["points"][gone["member"] >= 0]
    out = {}
    for q in detail["after"]:
        a = np.zeros(H * W, np.uint8)
        a[appeared["pixels"][kept & (appeared["frames"] == q)]] = 1
        a = a.reshape(H, W)
        g = np.zeros((H, W), np.uint8)
        if len(gone_points):
            K, E = recon.K[q], recon.E[q]
            cam = gone_points @ E[:, :3].T + E[:, 3]
            cam = cam[cam[:, 2] > 1e-6]
            u = np.rint(K[0, 0] * cam[:, 0] / cam[:, 2] + K[0, 2]).astype(np.int64)
            v = np.rint(K[1, 1] * cam[:, 1] / cam[:, 2] + K[1, 2]).astype(np.int64)
            inside = (u >= 0) & (u < W) & (v >= 0) & (v < H)
            u, v, z = u[inside], v[inside], cam[inside, 2]
            in_view = z <= recon.depth[q][v, u] * (1 + HIDDEN)
            g[v[in_view], u[in_view]] = 1
            g = cv2.dilate(g, kernel)
        if fill > 1:
            a = cv2.morphologyEx(a, cv2.MORPH_CLOSE, kernel)
            g = cv2.morphologyEx(g, cv2.MORPH_CLOSE, kernel)
        out[q] = (a > 0, g > 0)
    return out


def gt_regions(gt, pred, min_px, hit_share):
    """Connected regions of the ground truth in one photo, and how many the prediction touches.
    A region is hit when at least hit_share of its pixels are predicted."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(gt.astype(np.uint8), connectivity=8)
    total = hit = 0
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < min_px:
            continue
        total += 1
        hit += int((pred & (labels == i)).sum() >= hit_share * area)
    return total, hit


def scores(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
    iou = tp / (tp + fp + fn) if tp + fp + fn else None
    return {"precision": p, "recall": r, "f1": f1, "iou": iou}


# ----------------------------------------------------------------------------- overlays
def overlay(rgb, gt, appeared, gone, rc, title):
    """Left: the query photo with the prediction (green = appeared, red = gone) and the ground
    truth outlined in yellow. Right: hits in green, false alarms in red, misses in blue."""
    left = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    for mask, colour in ((appeared, rc.GREEN), (gone & ~appeared, rc.RED)):
        left[mask] = (0.45 * left[mask] + 0.55 * np.array(colour)).astype(np.uint8)
    contours, _ = cv2.findContours(gt.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(left, contours, -1, YELLOW, 2)
    pred = appeared | gone
    right = (0.35 * cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)).astype(np.uint8)
    right[pred & gt] = (60, 220, 60)
    right[pred & ~gt] = (60, 60, 235)
    right[~pred & gt] = (235, 140, 40)
    tile = np.hstack([left, right])
    for colour, width in (((0, 0, 0), 3), ((255, 255, 255), 1)):
        cv2.putText(tile, title, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, width, cv2.LINE_AA)
    return tile


# ----------------------------------------------------------------------------- one pair
def run_pair(rc, model, torch, name, baseline, query, mask_paths, out_dir, args):
    """Reconstruct baseline + query once, detect, and score against the masks (None = no change)."""
    photos = read_images(baseline + query)
    if len({(p.shape[0] > p.shape[1]) for p in photos}) > 1:
        print(f"  WARNING {name}: portrait and landscape photos are mixed", flush=True)
    log = io.StringIO()
    t0 = time.time()
    with contextlib.redirect_stdout(log):
        recon = rc.reconstruct(model, photos)
    reconstruct_s = time.time() - t0
    print("  " + log.getvalue().strip(), flush=True)
    found = re.search(r"inference ([\d.]+) s", log.getvalue())
    peak_gb = torch.cuda.max_memory_allocated() / 1e9
    t0 = time.time()
    report, detail = rc.analyse(recon, len(baseline))
    detect_s = time.time() - t0

    _, H, W = recon.depth.shape
    after = detail["after"]
    if mask_paths is None:
        gts = [np.zeros((H, W), bool) for _ in after]
    else:
        gts = [read_mask(m, photos[q].shape, H, W) for m, q in zip(mask_paths, after)]
    pred = predicted_masks(recon, detail, args.fill)
    tp = fp = fn = tp_app = tp_gone = px_app = px_gone = regions = regions_hit = 0
    per_frame = []
    for q, gt in zip(after, gts):
        a, g = pred[q]
        p = a | g
        f_tp, f_fp, f_fn = int((p & gt).sum()), int((p & ~gt).sum()), int((~p & gt).sum())
        tp, fp, fn = tp + f_tp, fp + f_fp, fn + f_fn
        tp_app, tp_gone = tp_app + int((a & gt).sum()), tp_gone + int((g & gt).sum())
        px_app, px_gone = px_app + int(a.sum()), px_gone + int(g.sum())
        n, hit = gt_regions(gt, p, args.min_region_px, args.hit_share)
        regions, regions_hit = regions + n, regions_hit + hit
        per_frame.append({"photo": query[q - len(baseline)].name, "tp": f_tp, "fp": f_fp, "fn": f_fn,
                          "gt_regions": n, "gt_regions_hit": hit})
    out_dir.mkdir(parents=True, exist_ok=True)
    for q in pick_even(list(after), args.overlays):
        a, g = pred[q]
        i = q - len(baseline)
        f = per_frame[i]
        title = f"{name} {query[i].stem} tp {f['tp']} fp {f['fp']} fn {f['fn']}"
        cv2.imwrite(str(out_dir / f"overlay_{query[i].stem}.jpg"), overlay(recon.rgb[q], gts[i], a, g, rc, title),
                    [cv2.IMWRITE_JPEG_QUALITY, 85])
    rc.contact_sheet(recon, detail, out_dir / "detector_sheet.jpg")
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))

    pixels = len(after) * H * W
    return {
        "pair": name, "frames": {"baseline": len(baseline), "query": len(query)}, "resolution": [W, H],
        "tp": tp, "fp": fp, "fn": fn, **scores(tp, fp, fn),
        "gt_pixel_share": (tp + fn) / pixels, "predicted_pixel_share": (tp + fp) / pixels,
        "appeared_px": px_app, "appeared_px_on_gt": tp_app, "gone_px": px_gone, "gone_px_on_gt": tp_gone,
        "clusters_appeared": len(report["appeared"]), "clusters_gone": len(report["gone"]),
        "clusters_in_detector_control": len(report["false_alarms_in_control"]),
        "gt_regions": regions, "gt_regions_hit": regions_hit,
        "verdict": report["verdict"].split(":")[0].split(" between")[0],
        "depth_noise_sigma": report["depth_noise_sigma"],
        "depth_noise_between_clips": report["depth_noise_between_clips"],
        "self_consistency": report["self_consistency"], "thresholds": report["thresholds"],
        "inference_s": float(found.group(1)) if found else None, "reconstruct_s": round(reconstruct_s, 1),
        "detect_s": round(detect_s, 1), "peak_gpu_gb": round(peak_gb, 1), "per_frame": per_frame,
    }


# ----------------------------------------------------------------------------- tables
def fmt(x, digits=3):
    return "-" if x is None else f"{x:.{digits}f}"


def pooled(rows):
    tp, fp, fn = (sum(r[k] for r in rows) for k in ("tp", "fp", "fn"))
    out = {"tp": tp, "fp": fp, "fn": fn, **scores(tp, fp, fn)}
    for k in ("f1", "iou"):
        vals = [r[k] if r[k] is not None else 0.0 for r in rows]
        out["mean_scene_" + k] = sum(vals) / len(vals) if vals else None
    out["gt_regions"] = sum(r["gt_regions"] for r in rows)
    out["gt_regions_hit"] = sum(r["gt_regions_hit"] for r in rows)
    out["clusters"] = sum(r["clusters_appeared"] + r["clusters_gone"] for r in rows)
    return out


def tables(results):
    lines = []
    changed = [r for r in results if r.get("kind") == "changed" and "error" not in r]
    if changed:
        lines += ["### Changed pairs (ground-truth masks)", "",
                  "| Scene | Precision | Recall | F1 | IoU | GT regions hit | Clusters (appeared + gone) | Verdict | "
                  "Noise between clips | Inference s | Detect s | Peak GPU GB |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for r in changed:
            lines.append(f"| {r['scene']} ({r['setting']}) | {fmt(r['precision'])} | {fmt(r['recall'])} | {fmt(r['f1'])} | "
                         f"{fmt(r['iou'])} | {r['gt_regions_hit']}/{r['gt_regions']} | "
                         f"{r['clusters_appeared']} + {r['clusters_gone']} | {r['verdict']} | "
                         f"{r['depth_noise_between_clips']:.3f} | {fmt(r['inference_s'], 1)} | {r['detect_s']} | {r['peak_gpu_gb']} |")
        for label, rows in (("Indoor", [r for r in changed if r["setting"] == "indoor"]),
                            ("Outdoor", [r for r in changed if r["setting"] == "outdoor"]),
                            ("All", changed)):
            if rows:
                o = pooled(rows)
                lines.append(f"| **{label}, pixels pooled ({len(rows)} scenes)** | {fmt(o['precision'])} | {fmt(o['recall'])} | "
                             f"{fmt(o['f1'])} | {fmt(o['iou'])} | {o['gt_regions_hit']}/{o['gt_regions']} | {o['clusters']} | | | | | |")
                lines.append(f"| **{label}, mean over scenes** | | | {fmt(o['mean_scene_f1'])} | "
                             f"{fmt(o['mean_scene_iou'])} | | | | | | | |")
        lines.append("")
    unchanged = [r for r in results if r.get("kind") == "unchanged" and "error" not in r]
    if unchanged:
        lines += ["### Unchanged pairs (every flagged pixel is a false alarm)", "",
                  "| Scene | Clusters (appeared + gone) | Flagged pixels, % of query photos | Verdict | Noise between clips |",
                  "| --- | --- | --- | --- | --- |"]
        for r in unchanged:
            lines.append(f"| {r['scene']} ({r['setting']}) | {r['clusters_appeared']} + {r['clusters_gone']} | "
                         f"{100 * r['predicted_pixel_share']:.2f} | {r['verdict']} | {r['depth_noise_between_clips']:.3f} |")
        clean = sum(r["clusters_appeared"] + r["clusters_gone"] == 0 for r in unchanged)
        lines += [f"| **Scenes with no false cluster** | **{clean}/{len(unchanged)}** | | | |", ""]
    failed = [r for r in results if "error" in r]
    if failed:
        lines += ["### Failed", ""] + [f"- {r['pair']}: {r['error']}" for r in failed] + [""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, help="PASLCD folder (see fetch_paslcd.py)")
    ap.add_argument("--out", default="out/paslcd")
    ap.add_argument("--scenes", nargs="*", default=INDOOR + OUTDOOR)
    ap.add_argument("--instance", type=int, default=1, help="1 = same lighting, 2 = different lighting")
    ap.add_argument("--pairs", nargs="*", default=["changed", "unchanged"], choices=["changed", "unchanged"])
    ap.add_argument("--n-baseline", type=int, default=24)
    ap.add_argument("--n-query", type=int, default=12)
    ap.add_argument("--fill", type=int, default=5,
                    help="pixels: closes gaps in the predicted mask and dilates projected gone points")
    ap.add_argument("--min-region-px", type=int, default=50,
                    help="ignore ground-truth regions smaller than this at model resolution")
    ap.add_argument("--hit-share", type=float, default=0.10,
                    help="share of a ground-truth region that must be predicted to count as hit")
    ap.add_argument("--overlays", type=int, default=3, help="overlay pictures saved per pair")
    ap.add_argument("--detector-dir", help="folder with recon_change.py (default: next to this script, else its parent)")
    ap.add_argument("--detector-commit", default="unknown", help="git commit of recon_change.py, recorded in the results")
    args = ap.parse_args()

    if args.detector_dir:
        detector_dir = Path(args.detector_dir)
    else:
        detector_dir = HERE if (HERE / "recon_change.py").exists() else HERE.parent
    sys.path.insert(0, str(detector_dir))
    import recon_change as rc
    import torch

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    meta = {
        "dataset": "PASLCD", "instance": args.instance, "date": datetime.datetime.now().isoformat(timespec="seconds"),
        "detector_file": str(Path(rc.__file__).resolve()), "detector_commit": args.detector_commit,
        "detector_sha256": hashlib.sha256(Path(rc.__file__).read_bytes()).hexdigest(),
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "args": {k: v for k, v in vars(args).items() if k not in ("data", "out")},
    }
    print(json.dumps(meta, indent=2), flush=True)
    model = rc.load_model()
    tag = "_".join(args.pairs)
    results = []
    for scene in args.scenes:
        setting = "indoor" if scene in INDOOR else "outdoor" if scene in OUTDOOR else "other"
        try:
            baseline, query, masks, held_back = scene_pairs(args.data, scene, args.instance, args.n_baseline, args.n_query)
        except OSError as e:
            results.append({"pair": scene, "scene": scene, "setting": setting, "error": f"{type(e).__name__}: {e}"})
            print(f"{scene}: {results[-1]['error']}", flush=True)
            continue
        for kind in args.pairs:
            name = f"{scene}_inst{args.instance}_{kind}"
            print(f"\n{name}: {len(baseline)} baseline, {len(query if kind == 'changed' else held_back)} query", flush=True)
            try:
                if kind == "changed":
                    r = run_pair(rc, model, torch, name, baseline, query, masks, out / name, args)
                else:
                    r = run_pair(rc, model, torch, name, baseline, held_back, None, out / name, args)
                print(f"  {r['verdict']} | clusters {r['clusters_appeared']} appeared, {r['clusters_gone']} gone | "
                      f"P {fmt(r['precision'])} R {fmt(r['recall'])} F1 {fmt(r['f1'])} IoU {fmt(r['iou'])} | "
                      f"GT regions hit {r['gt_regions_hit']}/{r['gt_regions']} | noise between clips "
                      f"{r['depth_noise_between_clips']:.3f} | detect {r['detect_s']} s", flush=True)
            except Exception as e:  # keep going: one bad scene must not lose the rest
                r = {"pair": name, "error": f"{type(e).__name__}: {e}"}
                print(f"  FAILED {r['error']}", flush=True)
            r.update(scene=scene, setting=setting, kind=kind)
            results.append(r)
            (out / f"results_{tag}.json").write_text(json.dumps({"meta": meta, "results": results}, indent=2))
            (out / f"results_{tag}.md").write_text(tables(results))
    print("\n" + tables(results))


if __name__ == "__main__":
    main()
