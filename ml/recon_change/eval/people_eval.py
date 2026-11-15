#!/usr/bin/env python3
"""How well are people found (people.py), and how tight is the mask? Three steps.

  truth    the COCO labels of the test photos: every person's box, outline and body points
           (which also tell when only an arm or a hand is in the frame). Login node.
  detect   the item detector asked for people, hands and arms over the test photos and the
           item-free photos: every box, and the score of every patch, saved to disk. GPU.
           Also times the item pass with and without the three extra phrases.
  score    the tables, from what was saved. No GPU.

  curl -O http://images.cocodataset.org/annotations/annotations_trainval2017.zip   (no sign-up)
  unzip annotations_trainval2017.zip 'annotations/instances_*2017.json' 'annotations/person_keypoints_*2017.json'
  python eval/people_eval.py truth --set <data>/set.json --coco annotations --out <out>
  python eval/people_eval.py detect --set <data>/set.json --img <data>/img --free raw_tiles1.json ... --out <out>
  python eval/people_eval.py score --set <data>/set.json --out <out>

Counting rules
- A labelled person is "found" when a person box at or above the limit overlaps their box with
  intersection over union of at least 0.5. People that COCO only marks as a crowd are not counted
  either way, and a box mostly inside a crowd region is not an invented person.
- "Invented": a person box at or above the limit that is on no labelled person.
- Per photo, the question the glasses ask: is anybody in view (any person, hand or arm box)?
- The mask is compared with the labelled outlines pixel by pixel: how much of the people it
  covers, and how much of it is people.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import items  # noqa: E402
import people  # noqa: E402

ASKED = ["a person", "a hand", "an arm", "a man", "a woman", "people", "a human hand", "a human arm"]
FILLS = [("under 2 % of the frame", 0, 0.02), ("2 to 10 %", 0.02, 0.10), ("10 to 30 %", 0.10, 0.30), ("over 30 %", 0.30, 2)]
ARM = (7, 8, 9, 10)  # COCO body points: elbows and wrists
REST = (0, 1, 2, 3, 4, 11, 12, 13, 14, 15, 16)  # nose, eyes, ears, hips, knees, ankles


def shrink(picture):
    scale = items.BIG_SIDE / max(picture.shape[:2])
    return picture if scale >= 1 else cv2.resize(picture, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)


# ----------------------------------------------------------------------------- truth
def truth(args):
    wanted = {r["id"] for r in json.loads(Path(args.set).read_text())}
    found = {str(i): [] for i in wanted}
    points = {}
    for part in ("val2017", "train2017"):
        path = Path(args.coco) / f"person_keypoints_{part}.json"
        if path.exists():
            for a in json.loads(path.read_text())["annotations"]:
                if a["image_id"] in wanted:
                    points[a["id"]] = a["keypoints"]
        coco = json.loads((Path(args.coco) / f"instances_{part}.json").read_text())
        person = next(c["id"] for c in coco["categories"] if c["name"] == "person")
        for a in coco["annotations"]:
            if a["image_id"] in wanted and a["category_id"] == person:
                outline = a["segmentation"] if isinstance(a["segmentation"], list) else None
                found[str(a["image_id"])].append({"box": [round(v, 1) for v in a["bbox"]], "crowd": int(a["iscrowd"]),
                                                  "outline": outline and [[round(v, 1) for v in poly] for poly in outline],
                                                  "points": points.get(a["id"])})
        del coco
    Path(args.out).mkdir(parents=True, exist_ok=True)
    (Path(args.out) / "people_truth.json").write_text(json.dumps(found))
    print(f"{sum(bool(v) for v in found.values())} of {len(found)} photos have a labelled person; {sum(len(v) for v in found.values())} people, "
          f"{sum(1 for v in found.values() for p in v if p['points'])} with body points", flush=True)


# ----------------------------------------------------------------------------- detect
def detect(args):
    import torch

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    todo = [(f"lvis:{r['file']}", str(Path(args.img) / r["file"])) for r in json.loads(Path(args.set).read_text())]
    for path in args.free or []:
        todo += [(k, k.split(":", 1)[1]) for k in sorted(json.loads(Path(path).read_text())["boxes"]) if not k.startswith("lvis:")]
    todo = todo[:args.only or None]
    detector = items.load(device="cuda" if torch.cuda.is_available() else "cpu", tiles=0)
    boxes, grids, seconds = {}, {}, []
    for n, (key, file) in enumerate(todo):
        picture = cv2.imread(file)
        if picture is None:
            continue
        picture = shrink(picture)
        raw = detector.detect(picture, ASKED, tiles=0)
        seconds.append(detector.last["seconds"])
        boxes[key] = {"size": [picture.shape[1], picture.shape[0]],
                      "raw": {p: np.round(np.column_stack([b, s]), 4).tolist() for p, (b, s) in raw.items() if len(s)}}
        grids[key] = np.round(detector.grid[1] * 255).astype(np.uint8)  # (patches, phrases)
        if n % 200 == 0:
            print(f"{n} of {len(todo)}", flush=True)
    # what the three extra phrases cost in the item pass, as the position fix runs it (one row of tiles)
    names, cost = items.wanted(), {"items only": [], "items and people": []}
    for key, file in todo[:60:2]:
        picture = shrink(cv2.imread(file))
        for label, also in (("items only", []), ("items and people", people.phrases())):
            detector.also = also
            t0 = time.time()
            detector.candidates(picture, names, tiles=1)
            cost[label].append(time.time() - t0)
    timing = {"whole_picture_pass": round(float(np.median(seconds[5:] or seconds)), 3), "device": detector.device,
              **{label: round(float(np.median(v[2:] or v)), 3) for label, v in cost.items()}}
    (out / "people_boxes.json").write_text(json.dumps({"asked": ASKED, "timing": timing, "boxes": boxes}))
    np.savez_compressed(out / "people_grids.npz", **{k.replace("/", "|"): v for k, v in grids.items()})
    print(f"saved {len(boxes)} photos: {timing}", flush=True)


# ----------------------------------------------------------------------------- score
def iou(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    inter = max(w, 0) * max(h, 0)
    return inter / max((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter, 1e-9)


def arm_only(person, W, H):
    """Only an arm or a hand of this person is in the picture: elbow or wrist labelled, no head, hips or legs."""
    pts = person.get("points")
    if not pts:
        return False
    there = [pts[3 * i + 2] > 0 for i in range(17)]
    return any(there[i] for i in ARM) and not any(there[i] for i in REST)


def score(args):
    out = Path(args.out)
    records = {f"lvis:{r['file']}": r for r in json.loads(Path(args.set).read_text())}
    labelled = json.loads((out / "people_truth.json").read_text())
    saved = json.loads((out / "people_boxes.json").read_text())
    grids = np.load(out / "people_grids.npz")
    asked = saved["asked"]
    print("seconds:", saved["timing"], "\n")
    kinds = {"person": args.person, "hand": ["a hand"], "arm": ["an arm"]}

    def found_in(key, limit, part):
        raw = {p: (np.array(v)[:, :4], np.array(v)[:, 4]) for p, v in saved["boxes"][key]["raw"].items()}
        old = dict(people.KINDS)
        people.KINDS.update(kinds)
        try:
            return people.from_raw(raw, limit, part)
        finally:
            people.KINDS.clear()
            people.KINDS.update(old)

    def run(limit, part, show=True):
        by_fill = {f[0]: [0, 0] for f in FILLS}
        arms, invented, with_people, without, free = [0, 0], 0, [0, 0], [0, 0], {}
        for key, rec in records.items():
            if key not in saved["boxes"]:
                continue
            W, H = rec["width"], rec["height"]
            got = found_in(key, limit, part)
            whole = [g for g in got if g["kind"] == "person"]
            truth_boxes = [(p, [p["box"][0] / W, p["box"][1] / H, (p["box"][0] + p["box"][2]) / W, (p["box"][1] + p["box"][3]) / H])
                           for p in labelled[str(rec["id"])]]
            real = [(p, b) for p, b in truth_boxes if not p["crowd"]]
            for p, b in real:
                fill = (b[2] - b[0]) * (b[3] - b[1])
                hit = any(iou(g["box"], b) >= 0.5 for g in whole)
                band = next(f[0] for f in FILLS if f[1] <= fill < f[2])
                by_fill[band][0] += hit
                by_fill[band][1] += 1
                if arm_only(p, W, H):
                    arms[0] += any(people.overlap(g["box"], b) >= 0.5 or iou(g["box"], b) >= 0.3 for g in got)
                    arms[1] += 1
            for g in whole:
                if not any(iou(g["box"], b) >= 0.5 for _, b in truth_boxes) and not any(people.overlap(g["box"], b) >= 0.5 for p, b in truth_boxes if p["crowd"]):
                    invented += 1
            if truth_boxes:
                with_people[0] += bool(got)
                with_people[1] += 1
            else:
                without[0] += bool(got)
                without[1] += 1
        for key in saved["boxes"]:
            if not key.startswith("lvis:"):
                group = key.split(":", 1)[0]
                c = free.setdefault(group, [0, 0, []])
                got = found_in(key, limit, part)
                c[0] += bool(got)
                c[1] += 1
                c[2] += [(Path(key).name, g["kind"], g["score"]) for g in got]
        if show:
            print(f"### person limit {limit}, hand and arm limit {part}, person phrases {kinds['person']}\n")
            print("| the person fills | labelled people | found |\n|---|---|---|")
            for band, (hit, n) in by_fill.items():
                print(f"| {band} | {n} | {hit} ({100 * hit / max(n, 1):.0f} %) |")
            print(f"\nonly an arm or a hand in the frame (from the body points): {arms[0]} of {arms[1]} noticed by any box")
            print(f"invented people (a person box on nobody): {invented} in {len(records)} photos")
            print(f"photos with a labelled person where somebody was reported: {with_people[0]} of {with_people[1]}")
            print(f"photos with no labelled person where somebody was reported: {without[0]} of {without[1]}")
            for group, (hit, n, listed) in sorted(free.items()):
                print(f"item-free photos, {group}: somebody reported in {hit} of {n}", sorted(listed, key=lambda x: -x[2])[:6] if hit else "")
            print()
        return by_fill, arms, invented, with_people, without, free

    if args.sweep:
        print("| person limit | part limit | found under 2 % | 2 to 10 % | 10 to 30 % | over 30 % | arm or hand only | invented | somebody reported, people there | nobody there (test photos) | item-free photos |")
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        for limit in args.sweep:
            for part in args.parts:
                by_fill, arms, invented, with_people, without, free = run(limit, part, show=False)
                cells = " | ".join(f"{h} of {n}" for h, n in by_fill.values())
                n_free = sum(v[0] for v in free.values()), sum(v[1] for v in free.values())
                print(f"| {limit} | {part} | {cells} | {arms[0]} of {arms[1]} | {invented} | {with_people[0]} of {with_people[1]} "
                      f"| {without[0]} of {without[1]} | {n_free[0]} of {n_free[1]} |")
        print()
    run(args.limit, args.part)

    # how tight is the mask: boxes against patches, on the photos with outlined people
    print("| mask | photos | people pixels covered | of the mask, people | photos without people: share of the picture masked |\n|---|---|---|---|---|")
    for label, boxes_only, patch in (("the boxes", True, None), ("patches that say person, inside the boxes", False, args.patch)):
        covered = masked = both = 0
        photos, empty = 0, []
        for key, rec in records.items():
            if key not in saved["boxes"]:
                continue
            W, H = saved["boxes"][key]["size"]
            got = found_in(key, args.limit, args.part)
            grid = (asked, grids[key.replace("/", "|")].astype(np.float32) / 255)
            if patch is not None:
                people.PATCH = patch
            mask = people.mask_of(got, (H, W), grid, boxes_only=boxes_only)
            real = [p for p in labelled[str(rec["id"])]]
            if not real:
                empty.append(float(mask.mean()))
                continue
            if any(p["crowd"] or not p["outline"] for p in real):
                continue
            truth_mask = np.zeros((H, W), np.uint8)
            sx, sy = W / rec["width"], H / rec["height"]
            for p in real:
                for poly in p["outline"]:
                    cv2.fillPoly(truth_mask, [(np.array(poly).reshape(-1, 2) * [sx, sy]).round().astype(np.int32)], 1)
            truth_mask = truth_mask > 0
            photos += 1
            covered += int(truth_mask.sum())
            masked += int(mask.sum())
            both += int((mask & truth_mask).sum())
        print(f"| {label} | {photos} | {100 * both / max(covered, 1):.0f} % | {100 * both / max(masked, 1):.0f} % | {100 * float(np.mean(empty or [0])):.2f} % |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["truth", "detect", "score"])
    ap.add_argument("--set", required=True)
    ap.add_argument("--coco")
    ap.add_argument("--img")
    ap.add_argument("--free", action="append")
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", type=int, default=0)
    ap.add_argument("--limit", type=float, default=people.SCORE)
    ap.add_argument("--part", type=float, default=people.SCORE_PART)
    ap.add_argument("--patch", type=float, default=people.PATCH)
    ap.add_argument("--person", nargs="*", default=["a person"])
    ap.add_argument("--sweep", type=float, nargs="*")
    ap.add_argument("--parts", type=float, nargs="*", default=[0.3])
    args = ap.parse_args()
    {"truth": truth, "detect": detect, "score": score}[args.step](args)


if __name__ == "__main__":
    main()
