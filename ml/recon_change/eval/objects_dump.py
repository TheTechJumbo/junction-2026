#!/usr/bin/env python3
"""Object map evaluation, step 1: run the two heavy models once per scene and save what they gave,
so that everything after it (eval/objects_eval.py) is plain numpy and can be redone at will.

Per PASLCD scene (Instance_1), with the same split as eval/run_bench.py:
  24 "before" photos   the room's baseline: the inventory is built from these
  12 "before" photos   held out: the unchanged control (nothing changed, so every event is false)
  12 "after" photos    with hand-drawn change masks

Saved under <out>/<scene>/:
  meta.json            photo names, phrases, settings, seconds per stage
  inv.npz              the reconstruction of the 24 baseline photos (depth, conf, K, E, rgb)
  det/<photo>.npz      raw detector boxes of every vocabulary phrase, down to --floor (tiles as deployed)
  det2/<photo>.npz     the same for the baseline photos with 2 rows of tiles (a finer look, inventory only)
  fix/<photo>.npz      one position fix per held-out and per after photo: 12 baseline photos + that photo
                       through VGGT, as live/worker.py where() does it; every frame's depth, trusted
                       pixels and camera (the new frame is the last), the room map transform, the fix's score

--timing does only the timed part on one scene (for the GPU numbers) and saves nothing.

  python eval/objects_dump.py --data <PASLCD> --scene Lounge --out dump [--device cpu]
"""
import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

SIDE = 1280  # the long side of the picture the detector sees: what the relay sends as big.jpg (items.BIG_SIDE)
N_FIX_BASE = 12  # baseline photos in a position fix (worker.N_LOCATE_BASE)


def pick_even(things, n):
    if len(things) <= n:
        return list(things)
    step = (len(things) - 1) / (n - 1)
    return [things[i] for i in sorted({int(round(k * step)) for k in range(n)})]


def scene_photos(root, scene, n_baseline=24, n_query=12):
    """Baseline, held-out and after photos of one scene: the split of eval/run_bench.py scene_pairs."""
    base = Path(root) / scene / "Instance_1"
    images = sorted(p for p in (base / "images").iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    masks = {p.stem: p for p in (base / "gt_mask").glob("*.png")}
    pool = pick_even([p for p in images if "_test_" not in p.name], n_baseline * 3 // 2)
    held = pool[1::3][:n_query]
    baseline = [p for p in pool if p not in held]
    after = pick_even([p for p in images if "_test_" in p.name and p.stem in masks], n_query)
    return baseline, held, after, masks


def read(path):
    picture = cv2.imread(str(path))
    if picture is None:
        raise RuntimeError(f"Cannot read {path}")
    scale = SIDE / max(picture.shape[:2])
    return cv2.resize(picture, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else picture


def fix_base(n):
    """Which baseline photos a position fix uses (worker.where: spread evenly)."""
    return sorted({int(i) for i in np.linspace(0, n - 1, min(N_FIX_BASE, n)).round()})


def save_boxes(path, raw, phrases):
    boxes = [raw[p][0] for p in phrases]
    np.savez_compressed(path, boxes=np.concatenate(boxes).astype(np.float32),
                        scores=np.concatenate([raw[p][1] for p in phrases]).astype(np.float32),
                        phrase=np.concatenate([np.full(len(b), i, np.int16) for i, b in enumerate(boxes)]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--scene", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", default=None)
    ap.add_argument("--floor", type=float, default=0.1, help="keep raw boxes down to this score")
    ap.add_argument("--timing", action="store_true")
    args = ap.parse_args()
    import os
    import torch
    import items
    import locate
    import objects
    import recon_change as rc

    if args.device == "cpu":
        torch.set_num_threads(int(os.environ.get("SLURM_CPUS_PER_TASK", os.cpu_count() or 4)))
    baseline, held, after, masks = scene_photos(args.data, args.scene)
    print(f"{args.scene}: {len(baseline)} baseline, {len(held)} held out, {len(after)} after", flush=True)
    phrases = objects.all_phrases()
    seconds = {}
    t0 = time.time()
    model = rc.load_model(device=args.device)
    on_gpu = next(model.parameters()).is_cuda
    detector = items.load(device="cuda" if on_gpu else "cpu")
    seconds["load_models"] = round(time.time() - t0, 1)
    device = torch.cuda.get_device_name(0) if on_gpu else f"CPU, {torch.get_num_threads()} threads"
    base_pictures = [read(p) for p in baseline]
    pick = fix_base(len(baseline))

    def one_fix(picture):
        t = {}
        t0 = time.time()
        r = rc.reconstruct(model, [base_pictures[i] for i in pick] + [picture])
        t["reconstruct"] = time.time() - t0
        t0 = time.time()
        base, new = list(range(len(pick))), [len(pick)]
        result, _, valid = locate.locate(r, base, new)
        room = locate.map_frame(r, base, valid)
        t["locate"] = time.time() - t0
        t0 = time.time()
        raw = detector.detect(picture, phrases, floor=args.floor)
        t["detect"] = time.time() - t0
        return r, valid, room, result, raw, t

    if args.timing:
        one_fix(base_pictures[1])  # the first pass on a device is slower: not timed
        t0 = time.time()
        rc.reconstruct(model, base_pictures)
        inv_recon = time.time() - t0
        t0 = time.time()
        for picture in base_pictures:
            detector.detect(picture, phrases, floor=args.floor)
        inv_detect = time.time() - t0
        t0 = time.time()
        for picture in base_pictures[:6]:
            detector.detect(picture, phrases, floor=args.floor, tiles=2)
        inv_detect2 = (time.time() - t0) * len(base_pictures) / 6
        print(f"TIMING inventory on {device}: VGGT on {len(base_pictures)} photos {inv_recon:.1f} s, detector on them "
              f"{inv_detect:.1f} s ({len(phrases)} phrases, tiles {detector.tiles}), with 2 rows of tiles {inv_detect2:.1f} s", flush=True)
        runs = [one_fix(read(p))[-1] for p in (held + after)[:5]]
        for key in ("reconstruct", "locate", "detect"):
            print(f"TIMING update on {device}: {key} median {np.median([t[key] for t in runs]):.2f} s over {len(runs)} fixes", flush=True)
        few = items.wanted()
        t0 = time.time()
        for picture in base_pictures[:5]:
            detector.find(picture, few)
        print(f"TIMING detector with the 5 deployed names: {(time.time() - t0) / 5:.2f} s per photo", flush=True)
        return

    out = Path(args.out) / args.scene
    for sub in ("det", "det2", "fix"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    inv = rc.reconstruct(model, base_pictures)
    seconds["inventory_reconstruct"] = round(time.time() - t0, 1)
    np.savez_compressed(out / "inv.npz", depth=inv.depth, conf=inv.conf, K=inv.K, E=inv.E, rgb=inv.rgb)
    t0 = time.time()
    for path, picture in zip(baseline, base_pictures):
        save_boxes(out / "det" / f"{path.stem}.npz", detector.detect(picture, phrases, floor=args.floor), phrases)
    seconds["inventory_detect"] = round(time.time() - t0, 1)
    t0 = time.time()
    for path, picture in zip(baseline, base_pictures):
        save_boxes(out / "det2" / f"{path.stem}.npz", detector.detect(picture, phrases, floor=args.floor, tiles=2), phrases)
    seconds["inventory_detect_2_rows"] = round(time.time() - t0, 1)
    print("inventory saved", seconds, flush=True)

    fixes = []
    for kind, paths in (("held", held), ("after", after)):
        for path in paths:
            picture = read(path)
            r, valid, room, result, raw, t = one_fix(picture)
            q = len(pick)
            save_boxes(out / "det" / f"{path.stem}.npz", raw, phrases)
            np.savez_compressed(out / "fix" / f"{path.stem}.npz", depth=r.depth, valid=valid, K=r.K, E=r.E, rgb=r.rgb[q],
                                origin=room["origin"], east=room["east"], north=room["north"], up=room["up"], metres=room["metres"])
            fixes.append({"photo": path.stem, "kind": kind, "state": result["state"], "confidence": result["confidence"],
                          "position": result["position"], "heading_deg": result["heading_deg"],
                          "seconds": {k: round(v, 2) for k, v in t.items()}})
            print(fixes[-1], flush=True)
    meta = {"scene": args.scene, "device": device, "side": SIDE, "floor": args.floor, "tiles": detector.tiles, "phrases": phrases,
            "baseline": [p.stem for p in baseline], "held": [p.stem for p in held], "after": [p.stem for p in after],
            "masks": {p.stem: str(masks[p.stem]) for p in after}, "photos": {p.stem: str(p) for p in baseline + held + after},
            "fix_base": pick, "fixes": fixes, "seconds": seconds, "picture_shape": list(base_pictures[0].shape)}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    print("done", seconds, flush=True)


if __name__ == "__main__":
    main()
