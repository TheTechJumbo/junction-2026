#!/usr/bin/env python3
"""How often does the item finder (OWLv2, items.py) fire on a thing that is not there? And how strongly on the thing
when it is? Runs the detector over folders of pictures for a few phrases and writes, per picture and phrase, the best
boxes with their score, the share of the box that has the name's colour (items.colour_share) and their size in pixels.

    python eval/find_measure.py OUT DIR [DIR ...] --phrases "orange box|an orange box" [--every 5] [--most 400]

On the GPU machine (never the laptop's GPU). OUT gets results.json and, with --crops, a small picture of the best box
per phrase and picture from --crop-from up, for a person to judge. Pictures and crops stay on that machine or in a
scratch folder: never commit them.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import items  # noqa: E402


def pictures(folders, every, most):
    out = []
    for folder in folders:
        found = sorted(p for p in Path(folder).rglob("*.jpg") if p.is_file())
        out += found[::every][:most]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("folders", nargs="+")
    ap.add_argument("--phrases", default="orange box|an orange box|an orange cardboard box")
    ap.add_argument("--colour-of", default="orange box", help="the name whose colour word is checked")
    ap.add_argument("--every", type=int, default=1)
    ap.add_argument("--most", type=int, default=10_000, help="pictures per folder")
    ap.add_argument("--floor", type=float, default=0.1)
    ap.add_argument("--crops", action="store_true")
    ap.add_argument("--crop-from", type=float, default=0.2)
    ap.add_argument("--tiles", type=int, default=None)
    args = ap.parse_args()
    out = Path(args.out)
    (out / "crops").mkdir(parents=True, exist_ok=True)
    phrases = [p.strip() for p in args.phrases.split("|") if p.strip()]
    detector = items.load()
    rows, t0 = [], time.time()
    files = pictures(args.folders, args.every, args.most)
    print(f"{len(files)} pictures, phrases {phrases}", flush=True)
    for n, path in enumerate(files):
        picture = cv2.imread(str(path))
        if picture is None:
            continue
        if max(picture.shape[:2]) > items.BIG_SIDE:
            scale = items.BIG_SIDE / max(picture.shape[:2])
            picture = cv2.resize(picture, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        raw = detector.detect(picture, phrases, tiles=args.tiles, floor=args.floor)
        H, W = picture.shape[:2]
        row = {"file": str(path), "size": [W, H], "phrases": {}}
        for k, phrase in enumerate(phrases):
            boxes, scores = raw.get(phrase, ([], []))
            best = sorted(zip(scores, boxes), key=lambda s: -s[0])[:5]
            row["phrases"][phrase] = [{"score": round(float(s), 3), "box": [round(float(v), 4) for v in b],
                                       "colour": items.colour_share(picture, b, args.colour_of),
                                       "px": int(round(max((b[2] - b[0]) * W, (b[3] - b[1]) * H)))} for s, b in best]
            if args.crops and best and best[0][0] >= args.crop_from:
                s, b = best[0]
                x, y, side = items.crop_around(picture.shape, b, 2.0, 96)
                crop = picture[y:y + side, x:x + side].copy()
                p0 = (int(b[0] * W) - x, int(b[1] * H) - y)
                p1 = (int(b[2] * W) - x, int(b[3] * H) - y)
                cv2.rectangle(crop, p0, p1, (255, 255, 255), 2)
                name = f"{s:.3f}_{k}_{n:05d}.jpg"
                cv2.imwrite(str(out / "crops" / name), cv2.resize(crop, (256, 256)), [cv2.IMWRITE_JPEG_QUALITY, 85])
                row["phrases"][phrase][0]["crop"] = name
        rows.append(row)
        if n % 50 == 0:
            print(f"{n}/{len(files)} {time.time() - t0:.0f} s", flush=True)
    (out / "results.json").write_text(json.dumps({"phrases": phrases, "settings": detector.settings(["orange box"]),
                                                  "seconds": round(time.time() - t0, 1), "pictures": rows}))
    print(f"done: {len(rows)} pictures in {time.time() - t0:.0f} s -> {out / 'results.json'}", flush=True)


if __name__ == "__main__":
    main()
