#!/usr/bin/env python3
"""Download the part of PASLCD that run_bench.py uses. Data only: JPG photos and PNG masks.

PASLCD (Pose-Agnostic Scene-Level Change Detection, CVPR 2025, MIT licence) is linked from the
paper's project page https://chumsy0725.github.io/MV-3DCD/ to
https://huggingface.co/datasets/ChamudithaJay/PASLCD. No account is needed.

Each scene instance has two captures of one place, taken on independent paths:
  images/Inst_<k>_IMG_*.jpg       one state of the scene (used here as the baseline)
  images/Inst_<k>_test_IMG_*.jpg  the other state, 25 photos (used here as the query)
  gt_mask/Inst_<k>_test_IMG_*.png hand-drawn change mask for each query photo

The whole dataset is 9 GB unpacked. This fetches an even sample per scene (36 baseline photos,
12 query photos and their masks, about 0.2 GB a scene), picked with the same rule run_bench.py
uses, so the benchmark gives the same numbers as it would on the full folders.

    python fetch_paslcd.py --out data/PASLCD            # run on a machine with internet
"""
import argparse
import concurrent.futures as cf
import json
import urllib.parse
import urllib.request
from pathlib import Path

REPO = "ChamudithaJay/PASLCD"
TREE = "https://huggingface.co/api/datasets/" + REPO + "/tree/main/"
RAW = "https://huggingface.co/datasets/" + REPO + "/resolve/main/"
INDOOR = ["Cantina", "Lounge", "Printing_area", "Lunch_room", "Meeting_room"]
OUTDOOR = ["Garden", "Pots", "Zen", "Playground", "Porch"]
IMG_EXT = (".jpg", ".jpeg", ".png")


def pick_even(items, n):
    """n items spread evenly over a sorted list (same rule as recon_change.sample_frames)."""
    if len(items) <= n:
        return list(items)
    step = (len(items) - 1) / (n - 1)
    return [items[i] for i in sorted({int(round(k * step)) for k in range(n)})]


def listing(path):
    with urllib.request.urlopen(TREE + urllib.parse.quote(path), timeout=60) as r:
        return sorted(e["path"] for e in json.load(r) if e["type"] == "file")


def download(path, out):
    dest = out / path
    if dest.exists() and dest.stat().st_size > 0:
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(RAW + urllib.parse.quote(path), timeout=300) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    tmp.rename(dest)
    return dest.stat().st_size


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/PASLCD")
    ap.add_argument("--scenes", nargs="*", default=INDOOR + OUTDOOR)
    ap.add_argument("--instances", nargs="*", type=int, default=[1], help="1 = same lighting, 2 = different lighting")
    ap.add_argument("--n-baseline", type=int, default=36, help="24 for the baseline plus 12 for the unchanged control")
    ap.add_argument("--n-query", type=int, default=12)
    ap.add_argument("--max-gb", type=float, default=4.5, help="stop before downloading more than this")
    args = ap.parse_args()
    out = Path(args.out)
    wanted = []
    for scene in args.scenes:
        for k in args.instances:
            base = f"{scene}/Instance_{k}"
            images = [p for p in listing(base + "/images") if p.lower().endswith(IMG_EXT)]
            masks = {Path(p).stem: p for p in listing(base + "/gt_mask") if p.lower().endswith(".png")}
            baseline = pick_even([p for p in images if "_test_" not in p], args.n_baseline)
            query = pick_even([p for p in images if "_test_" in p and Path(p).stem in masks], args.n_query)
            wanted += baseline + query + [masks[Path(p).stem] for p in query]
            print(f"{base}: {len(baseline)} baseline, {len(query)} query with masks", flush=True)
    total = 0
    with cf.ThreadPoolExecutor(8) as pool:
        for i, size in enumerate(pool.map(lambda p: download(p, out), wanted)):
            total += size
            if total > args.max_gb * 1e9:
                raise SystemExit(f"Stopped at {total / 1e9:.1f} GB (--max-gb)")
            if i % 50 == 0:
                print(f"  {i}/{len(wanted)} files, {total / 1e9:.2f} GB", flush=True)
    print(f"Done: {len(wanted)} files, {total / 1e9:.2f} GB downloaded into {out}")


if __name__ == "__main__":
    main()
