#!/usr/bin/env python3
"""Show it once: does it help to learn the wearer's own keys from one picture, instead of asking
for "keys" in words in every frame? A synthetic test, in four steps.

  masks    cut-outs of labelled items: their LVIS outlines for the test photos (login node)
  make     build the test pictures (CPU): each "own thing" is one labelled item. It is enrolled
           from a crop of the photo it comes from, and the same cut-out is pasted, 40 to 120 px
           long, turned, brighter or darker, blurred, into four indoor photos without items,
           each time with two other things of the same kind next to it (somebody else's keys).
           The same four photos with only the two others are the "not there" pictures.
  detect   run the detector with the words and with the enrolled pictures as questions, over
           these pictures and over the item-free photos, and save every box; also save how much
           each text candidate looks like the enrolled crop to CLIP and to DINOv2 (GPU)
  score    the tables, from the saved boxes (no GPU)

This is kinder than real life and says so in the results: the pasted thing is the very pixels it
was enrolled from, only turned and resized, never seen from another side. A real clip is scored
by eval/items_enrol_real.py.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import items  # noqa: E402
import items_eval as ev  # noqa: E402
from items_fetch import CATEGORY  # noqa: E402

PER_KIND, OWN, VIEWS = 16, 8, 4  # cut-outs per kind of item, how many of them are "own things", pictures per own thing
JUDGES = {"clip": "openai/clip-vit-large-patch14", "dino": "facebook/dinov2-base"}


# ----------------------------------------------------------------------------- masks
def masks(args):
    lvis = json.loads(Path(args.lvis).read_text())
    ours = {c["id"]: item for item, name in CATEGORY.items() for c in lvis["categories"] if c["name"] == name}
    files = {r["id"]: r["file"] for r in json.loads(Path(args.set).read_text())}
    out = [{"file": files[a["image_id"]], "name": ours[a["category_id"]], "bbox": [round(v, 1) for v in a["bbox"]],
            "outline": [[round(v, 1) for v in poly] for poly in a["segmentation"]]}
           for a in lvis["annotations"] if a["category_id"] in ours and a["image_id"] in files and isinstance(a["segmentation"], list)]
    Path(args.out).mkdir(parents=True, exist_ok=True)
    (Path(args.out) / "masks.json").write_text(json.dumps(out))
    print(f"{len(out)} outlines", flush=True)


# ----------------------------------------------------------------------------- make
def cutout(photo, entry):
    """A labelled item as (pixels, mask) of its box."""
    x, y, w, h = (int(round(v)) for v in entry["bbox"])
    x, y = max(x, 0), max(y, 0)
    piece = photo[y:y + h, x:x + w]
    mask = np.zeros(piece.shape[:2], np.uint8)
    for poly in entry["outline"]:
        cv2.fillPoly(mask, [(np.array(poly).reshape(-1, 2) - [x, y]).round().astype(np.int32)], 255)
    return piece, mask


def jitter(piece, mask, rng):
    """The cut-out as it might lie somewhere else: 40 to 120 px long, turned any way, brighter or darker, a little blurred."""
    scale = rng.uniform(40, 120) / max(piece.shape[:2])
    piece = cv2.resize(piece, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
    mask = cv2.resize(mask, piece.shape[1::-1], interpolation=cv2.INTER_LINEAR)
    h, w = piece.shape[:2]
    angle = rng.uniform(0, 360)
    turn = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(turn[0, 0]), abs(turn[0, 1])
    W, H = int(np.ceil(w * cos + h * sin)), int(np.ceil(w * sin + h * cos))
    turn[:, 2] += [W / 2 - w / 2, H / 2 - h / 2]
    piece = cv2.warpAffine(piece, turn, (W, H), flags=cv2.INTER_LINEAR)
    mask = cv2.warpAffine(mask, turn, (W, H), flags=cv2.INTER_LINEAR)
    piece = np.clip(piece.astype(np.float32) * rng.uniform(0.7, 1.3), 0, 255).astype(np.uint8)
    blur = rng.uniform(0, 1.2)
    if blur > 0.3:
        piece = cv2.GaussianBlur(piece, (0, 0), blur)
    return piece, mask


def paste(picture, piece, mask, taken, rng):
    """Paste a cut-out where nothing else was pasted, in the lower two thirds. Returns its box (0..1) or None."""
    H, W = picture.shape[:2]
    h, w = piece.shape[:2]
    for _ in range(30):
        x, y = int(rng.uniform(10, W - w - 10)), int(rng.uniform(H / 3, H - h - 10))
        ys, xs = np.nonzero(mask > 127)
        if not len(xs):
            return None
        box = [(x + xs.min()) / W, (y + ys.min()) / H, (x + xs.max() + 1) / W, (y + ys.max() + 1) / H]
        grown = [box[0] - 0.03, box[1] - 0.05, box[2] + 0.03, box[3] + 0.05]
        if all(items.iou(np.array(grown), np.array(t))[0][0] == 0 for t in taken):
            alpha = cv2.GaussianBlur(mask, (0, 0), 0.8).astype(np.float32)[..., None] / 255
            picture[y:y + h, x:x + w] = (alpha * piece + (1 - alpha) * picture[y:y + h, x:x + w]).astype(np.uint8)
            taken.append(box)
            return [round(v, 4) for v in box]
    return None


def make(args):
    out = Path(args.out)
    (out / "pictures").mkdir(parents=True, exist_ok=True)
    (out / "enrol").mkdir(exist_ok=True)
    rng = np.random.default_rng(args.seed)
    outlines = json.loads((out / "masks.json").read_text())
    rooms = sorted(k.split(":", 1)[1] for k in json.loads(Path(args.rooms).read_text())["boxes"])  # item-free indoor photos
    things, pictures = [], {}
    for name in CATEGORY:
        pool = []
        for e in outlines:
            if e["name"] != name or max(e["bbox"][2:]) < 64 or min(e["bbox"][2:]) < 0.15 * max(e["bbox"][2:]):
                continue
            photo = cv2.imread(str(Path(args.img) / e["file"]))
            piece, mask = cutout(photo, e)
            if piece.size and (mask > 0).mean() >= 0.25:
                pool.append((max(e["bbox"][2:]), e, piece, mask, photo))
        pool.sort(key=lambda p: -p[0])
        seen, kept = {}, []
        for p in pool:  # at most two cut-outs per photo, the largest
            if seen.get(p[1]["file"], 0) < 2:
                seen[p[1]["file"]] = seen.get(p[1]["file"], 0) + 1
                kept.append(p)
        kept = kept[:PER_KIND]
        print(f"{name}: {len(kept)} cut-outs (of {len(pool)} that are at least 64 px long and mostly whole)", flush=True)
        for i, (_, e, piece, mask, photo) in enumerate(kept[:OWN]):
            thing = f"{name}~{i}"
            x, y, w, h = e["bbox"]
            mx, my = 0.2 * w, 0.2 * h
            crop = photo[int(max(y - my, 0)):int(y + h + my), int(max(x - mx, 0)):int(x + w + mx)]
            cv2.imwrite(str(out / "enrol" / f"{thing.replace(' ', '_')}.jpg"), crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
            things.append({"thing": thing, "name": name, "half": "tune" if i % 2 == 0 else "report", "from": e["file"]})
            others = [k for j, k in enumerate(kept) if j != i]
            for v in range(VIEWS):
                room = ev.shrink(cv2.imread(rooms[int(rng.integers(len(rooms)))]))
                without, taken, strangers = room.copy(), [], []
                for j in rng.choice(len(others), size=min(2, len(others)), replace=False):
                    p2, m2 = jitter(others[j][2], others[j][3], rng)
                    box = paste(without, p2, m2, taken, rng)
                    if box:
                        strangers.append(box)
                with_it = without.copy()
                own = paste(with_it, *jitter(piece, mask, rng), taken, rng)
                if own is None:
                    continue
                for kind, picture, truth in (("with", with_it, own), ("without", without, None)):
                    key = f"{thing.replace(' ', '_')}_{v}_{kind}.jpg"
                    cv2.imwrite(str(out / "pictures" / key), picture, [cv2.IMWRITE_JPEG_QUALITY, 90])
                    pictures[key] = {"thing": thing, "kind": kind, "own": truth, "others": strangers}
    (out / "truth.json").write_text(json.dumps({"things": things, "pictures": pictures}))
    print(f"{len(things)} own things, {len(pictures)} pictures in {out}", flush=True)


# ----------------------------------------------------------------------------- detect
class Judge:
    """An image encoder that says how alike two crops look (cosine similarity of its embeddings)."""

    def __init__(self, kind, device):
        import torch
        from transformers import AutoModel, CLIPModel

        self.torch, self.device, self.kind = torch, device, kind
        if kind == "clip":
            self.model = CLIPModel.from_pretrained(JUDGES[kind], cache_dir=items.CACHE).to(device).eval()
            self.mean, self.std = items.MEAN, items.STD
        else:
            self.model = AutoModel.from_pretrained(JUDGES[kind], cache_dir=items.CACHE).to(device).eval()
            self.mean, self.std = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)

    def embed(self, crops):
        torch = self.torch
        pixels = np.stack([(cv2.cvtColor(cv2.resize(c, (224, 224), interpolation=cv2.INTER_CUBIC), cv2.COLOR_BGR2RGB).astype(np.float32) / 255
                            - self.mean) / self.std for c in crops])
        pixels = torch.from_numpy(pixels).permute(0, 3, 1, 2).float().to(self.device)
        with torch.no_grad():
            seen = self.model.get_image_features(pixel_values=pixels) if self.kind == "clip" else self.model(pixel_values=pixels).pooler_output
        seen = seen.float()
        return (seen / seen.norm(dim=-1, keepdim=True)).cpu().numpy()


def boxes_of(raw):
    return {p: np.round(np.column_stack([b, s]), 4).tolist() for p, (b, s) in raw.items() if len(s)}


def detect(args):
    import torch

    out = Path(args.out)
    truth = json.loads((out / "truth.json").read_text())
    device = "cuda" if torch.cuda.is_available() else "cpu"
    detector = items.load(device=device, tiles=args.tiles)
    judges = [Judge(kind, device) for kind in args.judges]
    words = sorted({p for n in CATEGORY for p in items.phrases_for(n)})
    saved, asked, own_look, t0 = {}, {}, {}, time.time()
    for t in truth["things"]:  # enrol: the crop as it is, and turned three ways
        thing = t["thing"]
        items.PHRASES[thing] = items.PHRASES[t["name"]]  # the words to find its box in the enrolment picture
        crop = cv2.imread(str(out / "enrol" / f"{thing.replace(' ', '_')}.jpg"))
        used = detector.enrol(thing, [crop, cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE), cv2.rotate(crop, cv2.ROTATE_180),
                                      cv2.rotate(crop, cv2.ROTATE_90_COUNTERCLOCKWISE)])
        asked[thing] = list(detector.enrolled.get(thing, []))
        own_look[thing] = {j.kind: j.embed([crop])[0] for j in judges}
        if used < 4:
            print(f"{thing}: only {used} enrolment pictures gave a box", flush=True)
    print(f"{len(asked)} things enrolled in {time.time() - t0:.0f} s", flush=True)
    seconds = []
    todo = list(truth["pictures"].items())[:args.only or None]
    for n, (key, info) in enumerate(todo):
        picture = cv2.imread(str(out / "pictures" / key))
        thing, name = info["thing"], info["thing"].split("~")[0]
        raw = detector.detect(picture, words + asked[thing], tiles=args.tiles)
        seconds.append(detector.last["seconds"])
        record = {"raw": boxes_of(raw), "candidates": []}
        for d in items.merge(raw, [name], 0.2)[:6]:  # text candidates, and how much each looks like the enrolled crop
            x, y, side = items.crop_around(picture.shape, d["box"], 1.3)
            crop = picture[y:y + side, x:x + side]
            record["candidates"].append({**d, **{j.kind: round(float(j.embed([crop])[0] @ own_look[thing][j.kind]), 4) for j in judges}})
        saved[key] = record
        if n % 80 == 0:
            print(f"{n} of {len(truth['pictures'])} pictures, {time.time() - t0:.0f} s", flush=True)
    every = [q for qs in asked.values() for q in qs]
    free = {}
    for path in args.free or []:  # the item-free photos: every enrolled thing asked for in one pass
        for n, key in enumerate(sorted(k for k in json.loads(Path(path).read_text())["boxes"] if not k.startswith("lvis:"))):
            if args.free_max and n % max(1, round(324 / args.free_max)) or args.only and len(free) >= args.only:
                continue
            picture = cv2.imread(key.split(":", 1)[1])
            if picture is None:
                continue
            free[key] = {"raw": boxes_of(detector.detect(ev.shrink(picture), words + every, tiles=args.tiles))}
    timing = {"median_seconds": round(float(np.median(seconds[5:] or seconds)), 3), "device": device, "tiles": args.tiles}
    (out / "enrol_boxes.json").write_text(json.dumps({"asked": asked, "pictures": saved, "free": free, "timing": timing}))
    print(f"saved: {len(saved)} pictures, {len(free)} item-free photos, {timing}, {time.time() - t0:.0f} s", flush=True)


# ----------------------------------------------------------------------------- score
def arrays(raw):
    return {p: (np.array(v)[:, :4], np.array(v)[:, 4]) for p, v in raw.items()}


def answer(record, name, thing, asked, how, limit, also=0.0, own=None):
    """The best box a method gives for one thing in one picture, or None.
    how: "text" (the words, limit), "shown1" / "shown4" (the enrolled picture, or it and its three turns),
    "shown+text" (shown4, and the words must give that box at least `also`), "clip" / "dino" (a text
    candidate from 0.2 that looks like the enrolled crop at least `limit`)."""
    raw = arrays(record["raw"])
    if how == "text":
        got = items.merge(raw, [name], limit)
        if own is not None:  # the words cannot tell the own thing from another of its kind: any box on it counts
            got = sorted(got, key=lambda d: not items.same_thing(d["box"], own))
    elif how in ("clip", "dino"):
        got = sorted(({**c, "score": c[how]} for c in record["candidates"] if c.get(how, -1) >= limit), key=lambda c: -c["score"])
    else:
        mine = asked[thing][:1] if how == "shown1" else asked[thing]
        got = items.merge(raw, [thing], limit, phrases=lambda n: mine)
        if how == "shown+text":
            worded = items.merge(raw, [name], also)
            got = [d for d in got if any(items.same_thing(d["box"], w["box"]) for w in worded)]
    return got[0] if got else None


def tally(truth, saved, half, how, limit, also=0.0):
    """found: the answer is on the own thing. other: on somebody else's thing of the same kind.
    wrong: somewhere else. false: any answer in a picture without the own thing, or in an item-free photo."""
    c = {"with": 0, "found": 0, "other": 0, "wrong": 0, "without": 0, "false_without": 0, "free": 0, "false_free": 0,
         "by_kind": {n: [0, 0] for n in CATEGORY}}
    halves = {t["thing"]: t["half"] for t in truth["things"]}
    for key, info in truth["pictures"].items():
        thing = info["thing"]
        if half and halves[thing] != half or key not in saved["pictures"]:
            continue
        name = thing.split("~")[0]
        got = answer(saved["pictures"][key], name, thing, saved["asked"], how, limit, also, info["own"])
        if info["kind"] == "with":
            c["with"] += 1
            c["by_kind"][name][1] += 1
            if got is None:
                continue
            if items.same_thing(got["box"], info["own"]):
                c["found"] += 1
                c["by_kind"][name][0] += 1
            elif any(items.same_thing(got["box"], o) for o in info["others"]):
                c["other"] += 1
            else:
                c["wrong"] += 1
        else:
            c["without"] += 1
            c["false_without"] += got is not None
    if how != "text" and how not in ("clip", "dino"):
        for key, record in saved["free"].items():
            for thing, mine in saved["asked"].items():
                if half and halves[thing] != half:
                    continue
                c["free"] += 1
                fake = {"raw": {p: v for p, v in record["raw"].items() if p in mine or not p.startswith("@")}, "candidates": []}
                c["false_free"] += answer(fake, thing.split("~")[0], thing, saved["asked"], how, limit, also) is not None
    return c


def row(label, c):
    kinds = " | ".join(f"{f} of {n}" for f, n in c["by_kind"].values())
    free = f"{c['false_free']} of {c['free']}" if c["free"] else "see the tables above"
    return (f"| {label} | {c['found']} of {c['with']} ({100 * c['found'] / max(c['with'], 1):.0f} %) | {kinds} | {c['other']} | {c['wrong']} "
            f"| {c['false_without']} of {c['without']} | {free} |")


def score(args):
    out = Path(args.out)
    truth, saved = json.loads((out / "truth.json").read_text()), json.loads((out / "enrol_boxes.json").read_text())
    print(f"{len(truth['things'])} own things, {len(saved['pictures'])} pictures, {len(saved['free'])} item-free photos, {saved['timing']}\n")
    grid = [round(v, 3) for v in np.arange(0.05, 0.96, 0.025)]

    def pick(how, also=0.0, allowed=0.01):
        """The lowest limit on the tuning half with at most 1 false answer per 100 item-free photos and
        at most 1 answer in 100 pictures that is on neither the own thing nor another of its kind."""
        best = None
        for limit in sorted(grid, reverse=True):
            c = tally(truth, saved, "tune", how, limit, also)
            if c["false_free"] > allowed * c["free"] or c["wrong"] > allowed * c["with"]:
                break
            best = limit
        return best

    head = ("| method | own thing found | " + " | ".join(CATEGORY) + " | answer on somebody else's | answer elsewhere "
            "| answers when it is not there (others of its kind are) | answers on item-free photos |\n|---|---|" + "---|" * (len(CATEGORY) + 4))
    methods = [("words only, limit 0.5 (today)", "text", 0.5, 0.0), ("words only, limit 0.3", "text", 0.3, 0.0)]
    for how, label in (("shown1", "shown once (1 picture)"), ("shown4", "shown once, plus the picture turned 3 ways"),
                       ("shown+text", "shown (4), and the words give that box 0.1"), ("clip", "text candidate from 0.2 that CLIP finds alike"),
                       ("dino", "text candidate from 0.2 that DINOv2 finds alike")):
        if how in ("clip", "dino") and not any(how in c for r in saved["pictures"].values() for c in r["candidates"]):
            continue
        also = 0.1 if how == "shown+text" else 0.0
        limit = pick(how, also)
        if limit is not None:
            methods.append((f"{label}, limit {limit}", how, limit, also))
        else:
            print(f"{label}: no limit on the grid keeps false answers at 1 in 100 on the tuning half")
    for half in ("tune", "report"):
        print(f"\n### {'tuning' if half == 'tune' else 'held-out'} half of the own things\n\n{head}")
        for label, how, limit, also in methods:
            print(row(label, tally(truth, saved, half, how, limit, also)))
    print("\nlimit sweep, held-out half, shown once plus turns:")
    for limit in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        print(row(f"limit {limit}", tally(truth, saved, "report", "shown4", limit)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["masks", "make", "detect", "score"])
    ap.add_argument("--lvis")
    ap.add_argument("--set")
    ap.add_argument("--img")
    ap.add_argument("--rooms", help="a raw_tiles*.json of item-free indoor photos: the rooms the cut-outs are pasted into")
    ap.add_argument("--free", action="append", help="raw_tiles*.json files whose item-free photos are searched for every enrolled thing")
    ap.add_argument("--free-max", type=int, default=0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tiles", type=int, default=1)
    ap.add_argument("--judges", nargs="*", default=["clip", "dino"])
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--only", type=int, default=0, help="detect: only the first pictures, for a quick try")
    args = ap.parse_args()
    {"masks": masks, "make": make, "detect": detect, "score": score}[args.step](args)


if __name__ == "__main__":
    main()
