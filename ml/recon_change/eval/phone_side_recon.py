#!/usr/bin/env python3
"""The server's part of the phone-side position fix, step 1: VGGT once over a room's baseline photos.

For every room of the walking test (plan.json of locate_walk_eval.py) and for 12 and 24 baseline
photos, run VGGT on the baseline photos ALONE (no frame of the wearer) and save depth, confidence
and a camera per photo. phone_side.build_map() turns one of these files into a room map.

The photos are stored the way live/worker.py stores a baseline: JPEG quality 92.

    python phone_side_recon.py --data walk/data/tum --plan walk/out/plan.json --out phone/recon
        [--threads 32] [--sizes 12 24]

Runs on a GPU when there is one, else on CPU cores (about 40 s for 12 photos on 32 cores).
"""
import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent


def baseline_pick(room, n):
    """The baseline photos a fix with n photos uses: locate_walk_eval.run() and live/worker.py where()."""
    spread = np.linspace(0, len(room["baseline"]) - 1, min(n, len(room["baseline"]))).round().astype(int)
    return sorted({int(i) for i in spread} | {p["frame"] for p in room["places"]})


def stored_photo(data, entry):
    img = cv2.imread(str(Path(data) / entry["rec"] / entry["rgb"]))
    if img is None:
        raise RuntimeError(f"Cannot read {entry['rec']}/{entry['rgb']}")
    return cv2.imdecode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])[1], cv2.IMREAD_COLOR)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, help="folder written by fetch_tum.py")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sizes", nargs="*", type=int, default=[12, 24])
    ap.add_argument("--threads", type=int, default=0, help="CPU cores to use (0 = all)")
    ap.add_argument("--detector-dir", help="folder with recon_change.py (default: this script's parent)")
    args = ap.parse_args()
    sys.path[:0] = [str(Path(args.detector_dir) if args.detector_dir else HERE.parent)]
    import torch

    import recon_change as rc

    if args.threads:
        torch.set_num_threads(args.threads)
        cv2.setNumThreads(args.threads)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    plan = json.loads(Path(args.plan).read_text())
    model = rc.load_model()
    for n in args.sizes:
        for name, room in plan["rooms"].items():
            file = out / f"{name}_{n}.npz"
            if file.exists():
                continue
            pick = baseline_pick(room, n)
            photos = [stored_photo(args.data, room["baseline"][i]) for i in pick]
            t0 = time.time()
            r = rc.reconstruct(model, photos)
            seconds = time.time() - t0
            part = file.with_suffix(".part.npz")
            np.savez(part, depth=r.depth.astype(np.float32), conf=r.conf.astype(np.float32), K=r.K, E=r.E, pick=np.array(pick),
                     meta=json.dumps({"room": name, "n": n, "pick": pick, "seconds": round(seconds, 1),
                                      "device": str(next(model.parameters()).device), "threads": torch.get_num_threads()}))
            part.rename(file)
            print(f"{name} {n} photos: {seconds:.1f} s -> {file}", flush=True)


if __name__ == "__main__":
    main()
