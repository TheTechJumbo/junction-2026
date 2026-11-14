#!/usr/bin/env python3
"""How well does the lost-items detector (items.py) find small things? Two steps.

  detect   run the detector over the test photos (eval/items_fetch.py) and over photos that
           should hold none of the items, and save every raw box down to score 0.05, for the
           whole picture alone and with 1 and 2 rows of tiles. Needs the model (a GPU job).
  score    read the saved boxes and print the tables: found per item and object size, wrong
           answers, false detections on the item-free photos, for any score limit and any choice
           of phrases. No model, runs anywhere in seconds.

  python eval/items_eval.py detect --set <folder>/set.json --img <folder>/img --out <out> \
         [--clean name=glob ...]
  python eval/items_eval.py score --out <out> [--limit 0.3] [--tiles 1] [--phrases chosen]

Counting rules
- A box is "on" a labelled object of its name when the two overlap with intersection over union
  of at least 0.5 (the usual strict rule), or, more loosely, when the middle of the box lies
  inside the label, or the middle of the label lies inside the box and the box is at most 6 times
  the area of the label. The loose rule is what an arrow needs, and it is needed for keys: the
  labels box every single key, the detector boxes the bunch. Tables give the loose count and, in
  brackets, the strict one.
- A labelled object is "found" when a box of its name at or above the limit is on it.
- The glasses give one answer per item and photo (the best box). That answer is "right" when it
  is on a labelled object of that item, "wrong" when it is not: the photo was checked not to
  have the item, or has it with every instance boxed and the answer is on none of them.
  Photos that say nothing about an item, and photos where not every instance is boxed and the
  answer misses the boxed ones, are not counted either way.
- Object size is the long side of the labelled box in pixels as the detector sees the whole
  picture (long side scaled to 960).
"""
import argparse
import glob
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import items  # noqa: E402

# every phrase tried, per item; "chosen" is what items.py ships with
PHRASES = {
    "plain": {"keys": ["keys"], "glasses": ["glasses"], "wallet": ["wallet"], "phone": ["phone"],
              "remote control": ["remote control"]},
    "chosen": dict(items.PHRASES),
    "wide": {"keys": ["keys", "a key", "a bunch of keys"], "glasses": ["glasses", "eyeglasses", "spectacles", "a pair of glasses"],
             "wallet": ["wallet", "a wallet"], "phone": ["phone", "a mobile phone", "a cell phone", "a smartphone"],
             "remote control": ["remote control", "a remote control", "a tv remote"]},
}
NAMES = list(PHRASES["plain"])
ALL = sorted({p for group in PHRASES.values() for ps in group.values() for p in ps})
SIZES = [("under 32 px", 0, 32), ("32 to 64 px", 32, 64), ("over 64 px", 64, 1e9)]


def shrink(picture, side=items.BIG_SIDE):
    scale = side / max(picture.shape[:2])
    return picture if scale >= 1 else cv2.resize(picture, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)


def detect(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    detector = items.load(tiles=0)
    todo = []
    if args.set:
        todo += [("lvis", r["file"], str(Path(args.img) / r["file"])) for r in json.loads(Path(args.set).read_text())
                 if args.only_half in (None, r["half"])]
    for spec in args.clean or []:
        name, pattern = spec.split("=", 1)
        files = sorted(glob.glob(pattern, recursive=True))
        if args.clean_max and len(files) > args.clean_max:
            files = [files[i] for i in np.linspace(0, len(files) - 1, args.clean_max).round().astype(int)]
        todo += [(name, f, f) for f in files]
    print(f"{len(todo)} pictures, {len(ALL)} phrases", flush=True)
    for tiles in args.tiles:
        path = out / f"raw_tiles{tiles}.json"
        if path.exists():
            continue
        boxes, seconds, passes = {}, [], []
        for group, key, file in todo:
            picture = cv2.imread(file)
            if picture is None:
                continue
            picture = shrink(picture)  # what the relay sends: long side at most 1280
            raw = detector.detect(picture, ALL, tiles=tiles)
            seconds.append(detector.last["seconds"])
            passes.append(detector.last["passes"])
            boxes[f"{group}:{key}"] = {"size": [picture.shape[1], picture.shape[0]],
                                        "raw": {p: np.round(np.column_stack([b, s]), 4).tolist() for p, (b, s) in raw.items() if len(s)}}
        skip = min(5, len(seconds) - 1)  # the first calls warm the GPU up
        timing = {"median_seconds": round(float(np.median(seconds[skip:])), 3), "mean_passes": round(float(np.mean(passes)), 2),
                  "device": detector.device, "pictures": len(seconds)}
        path.write_text(json.dumps({"tiles": tiles, "timing": timing, "boxes": boxes}))
        print(f"tiles {tiles}: {timing}", flush=True)


def answers(entry, groups, limit):
    """items.merge on saved raw boxes -> detections per name."""
    raw = {p: (np.array(v)[:, :4], np.array(v)[:, 4]) for p, v in entry["raw"].items()}
    return items.merge(raw, NAMES, limit, phrases=lambda name: groups[name])


def overlap(box, others):
    return items.iou(np.asarray(box, float), np.asarray(others, float))[0] if len(others) else np.zeros(0)


def on(box, truth):
    """Is a box on each labelled box? Returns (strict, loose) arrays; see the counting rules above."""
    box, truth = np.asarray(box, float), np.atleast_2d(np.asarray(truth, float))
    strict = overlap(box, truth) >= 0.5
    bx, by = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    tx, ty = (truth[:, 0] + truth[:, 2]) / 2, (truth[:, 1] + truth[:, 3]) / 2
    mine_in = (truth[:, 0] <= bx) & (bx <= truth[:, 2]) & (truth[:, 1] <= by) & (by <= truth[:, 3])
    area = (box[2] - box[0]) * (box[3] - box[1])
    theirs_in = ((box[0] <= tx) & (tx <= box[2]) & (box[1] <= ty) & (ty <= box[3])
                 & (area <= 6 * (truth[:, 2] - truth[:, 0]) * (truth[:, 3] - truth[:, 1])))
    return strict, strict | mine_in | theirs_in


def tally(records, saved, groups, limit, half=None):
    """Counts for one setting. Returns (found[name][size] = [found, there], answer[name] = {right, wrong, none...})."""
    found = {n: {s[0]: [0, 0, 0] for s in SIZES} for n in NAMES}  # [found, there, found by the strict rule]
    answer = {n: {"right": 0, "right_strict": 0, "wrong_absent": 0, "wrong_other": 0, "silent_there": 0, "silent_absent": 0,
                  "unclear": 0, "missed": []} for n in NAMES}
    for r in records:
        if half and r["half"] != half:
            continue
        entry = saved.get(f"lvis:{r['file']}")
        if entry is None:
            continue
        W, H = r["width"], r["height"]
        to_input = 960 / max(W, H)
        got = answers(entry, groups, limit)
        for name, label in r["items"].items():
            mine = [d for d in got if d["name"] == name]
            best = mine[0] if mine else None
            if not label["there"]:
                answer[name]["wrong_absent" if best else "silent_absent"] += 1
                continue
            truth = np.array([[x / W, y / H, (x + w) / W, (y + h) / H] for x, y, w, h in label["boxes"]])
            for box, (x, y, w, h) in zip(truth, label["boxes"]):
                size = max(w, h) * to_input
                bin_name = next(s[0] for s in SIZES if s[1] <= size < s[2])
                found[name][bin_name][1] += 1
                hits = [on(d["box"], box) for d in mine]
                found[name][bin_name][0] += int(any(h[1][0] for h in hits))
                found[name][bin_name][2] += int(any(h[0][0] for h in hits))
            if best is None:
                answer[name]["silent_there"] += 1
            elif on(best["box"], truth)[1].any():
                answer[name]["right"] += 1
                answer[name]["right_strict"] += int(on(best["box"], truth)[0].any())
            else:
                answer[name]["wrong_other" if label["all_boxed"] else "unclear"] += 1
                answer[name]["missed"].append((r["file"], best["box"], truth.tolist(), best["score"]))
    return found, answer


def clean_hits(saved, groups, limit):
    """Answers on photos that should hold none of the items: {group: (pictures, [(file, name, score, box)])}."""
    hits = {}
    for key, entry in saved.items():
        group, file = key.split(":", 1)
        if group == "lvis":
            continue
        pictures, listed = hits.setdefault(group, [0, []])
        hits[group][0] = pictures + 1
        listed += [(file, d["name"], d["score"], d["box"]) for d in items.best_per_name(answers(entry, groups, limit))]
    return hits


def table(found, answer):
    lines = ["| item | " + " | ".join(f"found, {s[0]}" for s in SIZES) + " | found, all | photos: right answer | wrong: item not there | wrong: on something else | said nothing, item there |",
             "|---|" + "---|" * (len(SIZES) + 5)]
    total = {s[0]: [0, 0, 0] for s in SIZES}
    sums = dict.fromkeys(["right", "wrong_absent", "wrong_other", "silent_there", "absent"], 0)
    for name in NAMES:
        cells, every = [], [0, 0, 0]
        for s in SIZES:
            f, n, strict = found[name][s[0]]
            cells.append(f"{f} ({strict}) of {n}")
            for i, value in enumerate((f, n, strict)):
                total[s[0]][i] += value
                every[i] += value
        a = answer[name]
        absent = a["wrong_absent"] + a["silent_absent"]
        for k in ("right", "wrong_absent", "wrong_other", "silent_there"):
            sums[k] += a[k]
        sums["absent"] += absent
        lines.append(f"| {name} | " + " | ".join(cells) + f" | {every[0]} ({every[2]}) of {every[1]} | {a['right']} | {a['wrong_absent']} of {absent} | {a['wrong_other']} | {a['silent_there']} |")
    all_found, all_there, all_strict = (sum(v[i] for v in total.values()) for i in range(3))
    lines.append("| **all** | " + " | ".join(f"{total[s[0]][0]} ({total[s[0]][2]}) of {total[s[0]][1]}" for s in SIZES)
                 + f" | {all_found} ({all_strict}) of {all_there} | {sums['right']} | {sums['wrong_absent']} of {sums['absent']} | {sums['wrong_other']} | {sums['silent_there']} |")
    return "\n".join(lines)


def score(args):
    out = Path(args.out)
    records = json.loads(Path(args.set).read_text())
    saved = {t: json.loads((out / f"raw_tiles{t}.json").read_text()) for t in (0, 1, 2) if (out / f"raw_tiles{t}.json").exists()}
    for t, s in saved.items():
        print(f"tiles {t}: {s['timing']}")
    if args.sweep:  # one line per setting on one half: for choosing
        print(f"\nsweep on the '{args.half}' half: found = labelled objects found; wrong = wrong answers (item not there + on something else)")
        print("| phrases | tiles | limit | found under 32 | found 32 to 64 | found over 64 | right answers | wrong answers | wrong on clean photos |")
        print("|---|---|---|---|---|---|---|---|---|")
        for phrases in PHRASES:
            for t, s in saved.items():
                for limit in args.sweep:
                    found, answer = tally(records, s["boxes"], PHRASES[phrases], limit, args.half)
                    by_size = [[sum(found[n][z[0]][i] for n in NAMES) for i in (0, 1)] for z in SIZES]
                    right = sum(a["right"] for a in answer.values())
                    wrong = sum(a["wrong_absent"] + a["wrong_other"] for a in answer.values())
                    clean = clean_hits(s["boxes"], PHRASES[phrases], limit)
                    n_clean = sum(len(v[1]) for v in clean.values())
                    print(f"| {phrases} | {t} | {limit} | " + " | ".join(f"{f} of {n}" for f, n in by_size) + f" | {right} | {wrong} | {n_clean} |")
        return
    s = saved[args.tiles]
    groups = PHRASES[args.phrases]
    for half in ("tune", "report", None):
        found, answer = tally(records, s["boxes"], groups, args.limit, half)
        print(f"\n### {half or 'both halves'}: phrases '{args.phrases}', tiles {args.tiles}, limit {args.limit}\n")
        print(table(found, answer))
    if args.misses:  # pictures of the answers counted as "on something else": label green, answer red
        folder = Path(args.misses)
        folder.mkdir(parents=True, exist_ok=True)
        _, answer = tally(records, s["boxes"], groups, args.limit, None)
        for name in NAMES:
            for i, (file, box, truth, value) in enumerate(answer[name]["missed"][:12]):
                picture = cv2.imread(str(Path(args.img) / file))
                H, W = picture.shape[:2]
                for t in truth:
                    cv2.rectangle(picture, (int(t[0] * W), int(t[1] * H)), (int(t[2] * W), int(t[3] * H)), (0, 220, 0), 2)
                cv2.rectangle(picture, (int(box[0] * W), int(box[1] * H)), (int(box[2] * W), int(box[3] * H)), (0, 0, 255), 2)
                cv2.imwrite(str(folder / f"{items.slug(name)}_{i:02d}_{int(value * 100)}.jpg"), picture)
    print("\n### photos that should hold none of the items\n")
    for group, (pictures, listed) in sorted(clean_hits(s["boxes"], groups, args.limit).items()):
        print(f"- {group}: {pictures} photos, {len(listed)} false detections in {len({h[0] for h in listed})} photos")
        for file, name, value, box in sorted(listed, key=lambda h: -h[2])[:args.show]:
            print(f"    {name} {value:.2f} {Path(file).parent.parent.parent.name}/{Path(file).name} box {box}")
    if args.crops:  # pictures of the false detections, to look at
        folder = Path(args.crops)
        folder.mkdir(parents=True, exist_ok=True)
        for group, (pictures, listed) in clean_hits(s["boxes"], groups, args.limit).items():
            for i, (file, name, value, box) in enumerate(sorted(listed, key=lambda h: -h[2])[:40]):
                picture = shrink(cv2.imread(file))
                found = [{"name": name, "box": box}]
                items.write_crops(picture, found, folder)
                (folder / f"{found[0]['image']}.jpg").replace(folder / f"{group}_{i:02d}_{items.slug(name)}_{int(value * 100)}.jpg")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["detect", "score"])
    ap.add_argument("--set")
    ap.add_argument("--img")
    ap.add_argument("--out", required=True)
    ap.add_argument("--clean", action="append", help="name=glob of photos that should hold none of the items")
    ap.add_argument("--clean-max", type=int, default=0, help="at most this many photos per --clean, spread evenly")
    ap.add_argument("--tiles", type=int, nargs="*", default=None)
    ap.add_argument("--limit", type=float, default=items.SCORE)
    ap.add_argument("--phrases", default="chosen", choices=list(PHRASES))
    ap.add_argument("--half", default="tune")
    ap.add_argument("--only-half", default=None, help="detect: only this half of the test photos (the 2-row tiles are slow)")
    ap.add_argument("--sweep", type=float, nargs="*")
    ap.add_argument("--show", type=int, default=12)
    ap.add_argument("--crops", help="score: folder for pictures of the false detections on the item-free photos")
    ap.add_argument("--misses", help="score: folder for pictures of the answers that are on something else")
    args = ap.parse_args()
    if args.step == "detect":
        args.tiles = [0, 1, 2] if args.tiles is None else args.tiles
        detect(args)
    else:
        args.tiles = items.TILES if args.tiles is None else args.tiles[0]
        score(args)


if __name__ == "__main__":
    main()
