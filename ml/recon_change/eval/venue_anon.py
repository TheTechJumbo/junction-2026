#!/usr/bin/env python3
"""Anonymise a recording's frames more strongly than live/anonymise.py can on a laptop, before a splat of them is made
public: OWLv2 (the item finder's detector, already in the cluster's model cache, nothing downloaded) looks for people,
heads and faces in the whole frame and in 2 x 2 tiles, as well as anonymise.py's own detectors (YuNet, Haar, HOG).
Every person box is pixelated on its own to about 8 blocks along its longer side, every head and face box (grown by 35%)
is filled with one flat colour. Run on the GPU (iridis/venue_anon.slurm), on frames that are already on the cluster.

    python eval/venue_anon.py FRAMES OUT_FRAMES [--score 0.15] [--tiles 2]

OUT_FRAMES gets the frames under the same names, and OUT_FRAMES/../anon_report.json the counts per frame. A second
pass over the output (YuNet, and OWLv2 "a human face") counts what still looks like a face: the report's "left".
Limits: a person OWLv2 scores under --score, or hidden behind something else, is not blurred; nor are reflections
and photos on screens. People far away are only a few pixels in a 518 px splat; look at the renders before publishing.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "live")]
import anonymise  # noqa: E402
import items  # noqa: E402

PEOPLE = ["a person"]
HEADS = ["a human head", "a human face"]


def boxes_of(found, phrases, score, W, H, pad):
    out = []
    for p in phrases:
        b, s = found.get(p, (np.zeros((0, 4)), np.zeros(0)))
        for (x0, y0, x1, y1), sc in zip(np.asarray(b).reshape(-1, 4), np.asarray(s).ravel()):
            if sc >= score:
                dx, dy = pad * (x1 - x0) * W, pad * (y1 - y0) * H
                out.append([max(0, int(x0 * W - dx)), max(0, int(y0 * H - dy)), min(W, int(np.ceil(x1 * W + dx))), min(H, int(np.ceil(y1 * H + dy)))])
    return [b for b in out if b[2] > b[0] and b[3] > b[1]]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("frames")
    ap.add_argument("out")
    ap.add_argument("--score", type=float, default=0.15)
    ap.add_argument("--tiles", type=int, default=2)
    args = ap.parse_args()
    src, out = Path(args.frames), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    det = items.Detector(score=args.score)
    report, t0 = [], time.time()
    for path in sorted(src.glob("*.jpg")):
        picture = cv2.imread(str(path))
        H, W = picture.shape[:2]
        found = det.detect(picture, PEOPLE + HEADS, tiles=args.tiles)
        people = boxes_of(found, PEOPLE, args.score, W, H, 0.10)
        heads = boxes_of(found, HEADS, args.score, W, H, 0.35)
        faces, bodies = anonymise.detect(picture)  # YuNet, Haar and HOG, already padded
        heads += faces
        # Each box on its own, largest first (merging overlapping boxes turns a crowd into one big blurred rectangle that
        # also covers the room between people): coarse, about 8 blocks along the box's longer side.
        bodies = sorted([list(b[:4]) for b in people] + [list(b[:4]) for b in bodies], key=lambda b: -(b[2] - b[0]) * (b[3] - b[1]))
        for x0, y0, x1, y1 in bodies:
            region = picture[y0:y1, x0:x1]
            region[:] = anonymise._pixelate(region, max(x1 - x0, y1 - y0) / 8)
        heads = anonymise._merge(heads)
        anonymise.blur(picture, heads, (), strength="solid")
        cv2.imwrite(str(out / path.name), picture, [cv2.IMWRITE_JPEG_QUALITY, 92])
        left_found = det.detect(picture, ["a human face"], tiles=args.tiles)
        left = len(boxes_of(left_found, ["a human face"], 0.3, W, H, 0))
        yunet_left = len(anonymise.detect(picture, people=False)[0])
        report.append({"frame": path.name, "people": len(people), "heads": len(heads), "bodies": len(bodies),
                       "left_owl_face_0.3": left, "left_yunet_haar_faces": yunet_left})
        print(report[-1], flush=True)
    total = {k: sum(r[k] for r in report) for k in ("people", "heads", "bodies", "left_owl_face_0.3", "left_yunet_haar_faces")}
    summary = {"frames": len(report), "seconds": round(time.time() - t0, 1), "score": args.score, "tiles": args.tiles,
               "detector": det.model_id, "totals": total, "per_frame": report}
    (out.parent / "anon_report.json").write_text(json.dumps(summary, indent=1))
    print("totals", total, flush=True)


if __name__ == "__main__":
    main()
