#!/usr/bin/env python3
"""One whole position fix with lost items, through live/worker.py's own where(), plus timings.

There is no recording of a real room with real keys in it yet, so the item is painted in: a
labelled phone or remote from a test photo is pasted into a full-size photo of the baseline room.
The wearer's picture is made 16:9 (wider than the baseline photos) by mirroring the sides, so the
worker has to crop it, and boxes found in big.jpg have to be mapped into the cropped frame.

What it checks
- placement: the same spot is also named as a place in the baseline photo of that view. The
  item's position comes from the new frame's depth, the place's from the baseline frame's depth,
  so the two should agree.
- a second item pasted on the mirrored side, outside the crop: it must come back without a position.
- another room: the fix is "lost" and nothing is placed.
- a detector that is not loaded: the fix still comes back, with "items": [] and a note.
- seconds per fix with and without items, and the detector alone for 0, 1 and 2 rows of tiles.

  python eval/items_fullpath.py --baselines <project>/baselines --zone kitchen \
      --originals <vggt>/examples/kitchen/images --set <data>/set.json --img <data>/img \
      --other <a photo of another place> --work <folder> [--device cpu]
"""
import argparse
import json
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "live"))


def paste(picture, piece, cx, cy, long_side):
    """Paste a small picture with soft edges, centred on (cx, cy). Returns its box (x0, y0, x1, y1)."""
    scale = long_side / max(piece.shape[:2])
    piece = cv2.resize(piece, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    h, w = piece.shape[:2]
    x, y = int(round(cx - w / 2)), int(round(cy - h / 2))
    ramp = np.minimum(np.minimum(np.arange(w) + 1, w - np.arange(w))[None, :], np.minimum(np.arange(h) + 1, h - np.arange(h))[:, None])
    alpha = np.clip(ramp / 4.0, 0, 1)[..., None]
    picture[y:y + h, x:x + w] = (alpha * piece + (1 - alpha) * picture[y:y + h, x:x + w]).astype(np.uint8)
    return x, y, x + w, y + h


def pieces(records, img, item, least=140, most=6):
    """Labelled boxes of an item that are big and clear enough to paste elsewhere."""
    got = []
    for r in records:
        label = r["items"].get(item)
        if not label or not label["there"]:
            continue
        for x, y, w, h in label["boxes"]:
            if max(w, h) >= least and min(w, h) >= 0.3 * max(w, h):
                photo = cv2.imread(str(Path(img) / r["file"]))
                if photo is not None:
                    got.append(photo[int(y):int(y + h), int(x):int(x + w)])
        if len(got) >= most:
            break
    return got


def zip_job(folder, frames, big=None):
    folder.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(folder / "out", ignore_errors=True)
    shutil.rmtree(folder / "frames", ignore_errors=True)
    with zipfile.ZipFile(folder / "clip.zip", "w") as bundle:
        for i, frame in enumerate(frames):
            bundle.writestr(f"{i:03d}.jpg", cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes())
        if big is not None:
            bundle.writestr("big.jpg", cv2.imencode(".jpg", big, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes())
    return folder


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baselines", required=True)
    ap.add_argument("--zone", default="kitchen")
    ap.add_argument("--frame", type=int, default=5, help="the baseline photo whose view the wearer has")
    ap.add_argument("--originals", required=True, help="the room's photos at full size")
    ap.add_argument("--set", required=True)
    ap.add_argument("--img", required=True)
    ap.add_argument("--other", required=True, help="a photo of another place")
    ap.add_argument("--work", required=True)
    ap.add_argument("--device", default=None)
    ap.add_argument("--spot", type=float, nargs=2, default=[0.33, 0.72], help="where the item is pasted, u v in the baseline photo")
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()
    if args.device:
        os.environ["RECON_DEVICE"] = args.device
    import torch
    import items
    import recon_change as rc
    import worker

    work = Path(args.work)
    zone = work / "baselines" / args.zone  # our own copy of the zone: the live one is never written to
    zone.mkdir(parents=True, exist_ok=True)
    for f in sorted((Path(args.baselines) / args.zone).glob("*.jpg")):
        shutil.copy(f, zone / f.name)
    u, v = args.spot
    (zone / "waypoints.json").write_text(json.dumps([{"name": "paste spot", "frame": args.frame, "u": u, "v": v}]))
    worker.BASELINES = work / "baselines"

    # the full-size photo of the same view as baseline photo --frame
    small = cv2.imread(str(sorted(zone.glob("*.jpg"))[args.frame]))
    best = None
    for f in sorted(Path(args.originals).glob("*")):
        photo = cv2.imread(str(f))
        if photo is None:
            continue
        error = float(np.abs(cv2.resize(worker.crop_like(photo, small), small.shape[1::-1], interpolation=cv2.INTER_AREA).astype(float) - small).mean())
        if best is None or error < best[0]:
            best = (error, f, photo)
    print(f"baseline photo {args.frame} is {best[1].name} (mean difference {best[0]:.1f} of 255)", flush=True)
    view = worker.crop_like(best[2], small)
    H = 720
    view = cv2.resize(view, (round(H * view.shape[1] / view.shape[0] / 2) * 2, H), interpolation=cv2.INTER_CUBIC)
    side = (1280 - view.shape[1]) // 2  # mirrored strips make the wearer's picture 16:9
    wide = np.concatenate([view[:, :side][:, ::-1], view, view[:, -side:][:, ::-1]], 1)
    print(f"wearer's picture {wide.shape[1]}x{wide.shape[0]}, of which the middle {view.shape[1]} px is the room photo", flush=True)

    if args.device == "cpu":
        torch.set_num_threads(int(os.environ.get("SLURM_CPUS_PER_TASK", os.cpu_count() or 4)))
    model = rc.load_model(device=args.device)
    on_gpu = next(model.parameters()).is_cuda
    detector = items.load(device="cuda" if on_gpu else "cpu")
    worker.ITEM_DETECTOR["model"] = detector
    names = worker.ITEMS or items.wanted()
    worker.ITEMS = names
    worker.INFO.update({"device": torch.cuda.get_device_name(0) if on_gpu else f"CPU, {torch.get_num_threads()} threads",
                        "settings": {"locate_baseline_frames": worker.N_LOCATE_BASE, "items": detector.settings(names)}})
    print(json.dumps(worker.INFO), flush=True)

    # paint the items in: one inside the part the fix uses, one on the mirrored strip outside it
    records = json.loads(Path(args.set).read_text())
    spot = (side + u * (view.shape[1] - 1), v * (H - 1))
    picture = None
    for inside_name, outside_name in (("remote control", "phone"), ("phone", "remote control")):
        for piece in pieces(records, args.img, inside_name):
            trial = wide.copy()
            box = paste(trial, piece, spot[0], spot[1], 80)
            found = [d for d in items.best_per_name(detector.find(trial, names)) if d["name"] == inside_name]
            if found and box[0] <= (found[0]["box"][0] + found[0]["box"][2]) / 2 * 1280 <= box[2]:
                picture = trial
                break
        if picture is not None:
            break
    if picture is None:
        raise SystemExit("The detector found none of the pasted items: nothing to place.")
    for piece in pieces(records, args.img, outside_name):
        trial = picture.copy()
        shape = piece.shape[1] / piece.shape[0]  # the piece must fit on the strip: at most side - 12 px wide
        box = paste(trial, piece, side / 2, 0.5 * H, min(80, side - 12) if shape >= 1 else min(80, (side - 12) / shape))
        found = [d for d in items.best_per_name(detector.find(trial, names)) if d["name"] == outside_name]
        if found and found[0]["box"][2] * 1280 <= side + 4:
            picture = trial
            break
    cv2.imwrite(str(work / "wearer_picture.jpg"), picture)
    small_frame = cv2.resize(picture, (640, 360), interpolation=cv2.INTER_AREA)
    request = {"op": "locate", "zone": args.zone, "file": "clip.zip"}

    def fix(name, frames, big, **more):
        job = zip_job(work / name, frames, big)
        t0 = time.time()
        result = worker.where(model, job, {**request, **more})
        result["seconds"]["total"] = round(time.time() - t0, 2)
        (job / "result.json").write_text(json.dumps(result))
        return result

    def brief(result):
        return {k: result.get(k) for k in ("state", "confidence", "position", "heading_deg", "waypoints", "items", "items_looked_for",
                                           "items_frame", "items_note", "seconds")}

    print("\n== 1. same room, big.jpg, items pasted in", flush=True)
    fix("warm", [small_frame], picture)  # the first pass on a device is slower: not timed
    with_items = [fix("same_room", [small_frame], picture) for _ in range(args.repeat)]
    result = with_items[-1]
    print(json.dumps(brief(result), indent=1), flush=True)
    place = result["waypoints"][0] if result["waypoints"] else None
    for item in result["items"]:
        if item["position"] and place:
            gap = float(np.linalg.norm(np.array(item["position"]) - place["position"]))
            print(f"PLACEMENT {item['name']}: item {item['position']} {item['distance_m']} m {item['bearing_deg']} deg | place at the same spot "
                  f"{place['position']} {place['distance_m']} m {place['bearing_deg']} deg | apart {gap:.2f} m", flush=True)
        else:
            print(f"NOT PLACED {item['name']}: {item.get('note')}", flush=True)
    print("crops written:", sorted(p.name for p in (work / "same_room" / "out").glob("item_*.jpg")), flush=True)
    viewed = fix("same_room_view", [small_frame], picture, view=True)
    print("with the 3D view asked for:", {"gaussians": viewed.get("gaussians"), "seconds": viewed["seconds"]}, flush=True)

    print("\n== 2. the same without big.jpg (detector looks at the 640 px frame)", flush=True)
    print(json.dumps(brief(fix("no_big", [small_frame], None)), indent=1), flush=True)

    print("\n== 3. the same with the items feature off: the time of a fix without it", flush=True)
    worker.ITEMS = []
    without = [fix("items_off", [small_frame], picture) for _ in range(args.repeat)]
    worker.ITEMS = names
    print(json.dumps(brief(without[-1])["seconds"]), flush=True)

    print("\n== 4. another place, with a pasted item: lost, so nothing is placed", flush=True)
    other = cv2.imread(args.other)
    other = cv2.resize(other, (1280, 720), interpolation=cv2.INTER_AREA)
    paste(other, pieces(records, args.img, inside_name)[0], 640, 500, 80)
    print(json.dumps(brief(fix("other_place", [cv2.resize(other, (640, 360), interpolation=cv2.INTER_AREA)], other)), indent=1), flush=True)

    print("\n== 5. detector not loaded: the fix must still come back", flush=True)
    worker.ITEM_DETECTOR.pop("model")
    worker.ITEM_DETECTOR["error"] = "ImportError: No module named 'transformers' (simulated)"
    print(json.dumps(brief(fix("no_detector", [small_frame], picture)), indent=1), flush=True)
    worker.ITEM_DETECTOR["model"] = detector

    print("\n== 6. broken big.jpg: the fix must still come back", flush=True)
    job = zip_job(work / "broken_big", [small_frame], None)
    with zipfile.ZipFile(job / "clip.zip", "a") as bundle:
        bundle.writestr("big.jpg", b"not a picture")
    broken = worker.where(model, job, request)
    print(json.dumps({k: broken.get(k) for k in ("state", "items", "items_frame", "items_note")}), flush=True)

    print("\n== timings", flush=True)

    def median(results, key):
        return round(float(np.median([r["seconds"].get(key, 0) for r in results])), 2)

    print(f"TIMING fix with items: total {median(with_items, 'total')} s, of which items {median(with_items, 'items')} s, "
          f"reconstruct {median(with_items, 'reconstruct')} s | fix without items: total {median(without, 'total')} s | device {worker.INFO['device']}", flush=True)
    for tiles in (0, 1, 2):
        seconds = []
        for _ in range(4):
            detector.find(picture, names, tiles=tiles)
            seconds.append(detector.last["seconds"])
        print(f"TIMING detector alone on 1280x720, tiles {tiles}: {detector.last['passes']} passes, {np.median(seconds[1:]):.2f} s", flush=True)


if __name__ == "__main__":
    main()
