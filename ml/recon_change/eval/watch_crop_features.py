#!/usr/bin/env python3
"""Side experiment: patch features computed on a close-up of the spot, not on the whole frame.

The model's own patch features (Recon.feat) come from the whole 518-pixel frame: one feature
per 14 x 14 pixels, so a small spot is less than one patch. Here the spot and its ring are cut
out (baseline patch and the new frame laid over it, as watch.compare makes them), blown up to
224 x 224 and put through the same DINOv2 backbone on their own, which gives 16 x 16 patches
over the close-up. This is done twice: with the patch cut from the 518-pixel frames, and with
the patch cut from the original photos. The difference between the two is what the extra pixels
buy. CPU only (the backbone on 224 x 224 crops is small work).

Reads the spots from the output of watch_eval.py (changed pairs only) and writes
<out>/<scene>_inst<k>.json with, per spot, the feature difference inside the spot and in the
ring, for the new frame and for one other baseline frame.

    python watch_crop_features.py --eval watch/out/eval --recon recall/out/base --data bench/data/PASLCD --scene Lounge --out watch/out/crop
"""
import argparse
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
import watch_eval as we  # noqa: E402

SIDE, GRID = 224, 16


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--eval", required=True)
    ap.add_argument("--recon", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--scene", required=True)
    ap.add_argument("--instance", type=int, default=1)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    import torch

    tag = f"{args.scene}_inst{args.instance}"
    rows = [r for r in json.loads((Path(args.eval) / f"{tag}.json").read_text())["rows"]
            if r["pair"] == "changed" and r.get("spot") and r.get("single", {}).get("x1") and r["single"]["x1"].get("c_spot") is not None]
    baseline, query, _, _ = rb.scene_pairs(args.data, args.scene, args.instance, 24, 12)
    recon, n_before, _, _ = we.load_recon(Path(args.recon) / f"{tag}_changed")
    scene = watch.Scene(recon, we.read_rgb(baseline + query))
    t0 = time.time()

    crops, jobs = [], []  # jobs: (row index, which frame, scale name, index of first crop, spot weights, ring weights)
    for n, row in enumerate(rows):
        geo = watch.spot_geometry(scene, {"name": "", **row["ref"]})  # the reference view watch_eval.py compared with
        usual, _ = watch.clear_views(scene, geo, list(range(n_before)))
        for which, j in (("new", row["q"]), ("own", usual[0]["frame"] if usual else None)):
            if j is None:
                continue
            for name, scale in (("x1", 1.0), ("full", None)):
                res = watch.compare(scene, geo, j, scale, keep=True)
                p = res.get("patches")
                if p is None or res["c_spot"] is None:
                    continue
                for img in (p["a"], p["b"]):
                    crops.append(cv2.resize(img, (SIDE, SIDE), interpolation=cv2.INTER_AREA if img.shape[1] > SIDE else cv2.INTER_CUBIC))
                spot = cv2.resize((p["spot"] & p["ok"]).astype(np.float32), (GRID, GRID), interpolation=cv2.INTER_AREA)
                ring = cv2.resize((~p["spot"] & p["ok"]).astype(np.float32), (GRID, GRID), interpolation=cv2.INTER_AREA)
                jobs.append((n, which, name, len(crops) - 2, spot.reshape(-1), ring.reshape(-1), res["scale"]))
    print(f"{tag}: {len(rows)} spots, {len(crops)} crops, {time.time() - t0:.0f} s", flush=True)

    model = rc.load_model(device="cpu")
    embed = model.aggregator.patch_embed
    mean = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)
    tokens = []
    with torch.no_grad():
        for i in range(0, len(crops), 32):
            x = torch.from_numpy(np.stack(crops[i:i + 32])).permute(0, 3, 1, 2).float().div(255)
            out = embed((x - mean) / std)
            out = out["x_norm_patchtokens"] if isinstance(out, dict) else out
            tokens.append(out.float().numpy().astype(np.float16))
            if i % 320 == 0:
                print(f"  {i}/{len(crops)} crops, {time.time() - t0:.0f} s", flush=True)
    tokens = np.concatenate(tokens)
    centre = tokens.reshape(-1, tokens.shape[-1]).astype(np.float32).mean(0)  # raw tokens share a large common part

    result = [{"scene": args.scene, "label": r["label"], "region_area": r["region_area"], "q": r["q"], "spot": r["spot"],
               "angle": r["view"]["angle"]} for r in rows]
    for n, which, name, first, spot, ring, scale in jobs:
        a = tokens[first].astype(np.float32) - centre
        b = tokens[first + 1].astype(np.float32) - centre
        apart = 1 - (a * b).sum(1) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1) + 1e-6)
        inner, outer = spot >= 0.5, ring >= 0.75
        result[n][f"{which}_{name}"] = {"f_spot": float(apart[inner].mean()) if inner.any() else None,
                                        "f_ring": float(apart[outer].mean()) if outer.any() else None, "scale": scale}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{tag}.json").write_text(json.dumps(we.clean(result)))
    print(f"{tag}: done, {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
