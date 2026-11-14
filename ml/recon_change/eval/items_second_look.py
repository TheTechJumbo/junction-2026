#!/usr/bin/env python3
"""Second looks at weak item candidates, saved to disk so that every rule can be scored later
without a GPU (eval/items_recall.py).

The detector sees many items and scores them low (eval/ITEMS_RESULTS.md). For every candidate box
from a low limit up, in every test photo and every item-free photo, this saves
  zoom   what the same detector says about a square crop around the box (side = ZOOM x the box's
         long side), blown up to its input size: all its boxes there, per phrase
  clip   how much a zero-shot image classifier (CLIP) thinks a tighter crop looks like each of a
         list of texts: the items and a set of look-alikes
The candidates come from the boxes eval/items_eval.py already saved (raw_tiles1.json), so the
whole-picture passes are not run again.

  python eval/items_second_look.py candidates --raw out/eval/raw_tiles1.json --raw out/eval-indoor/raw_tiles1.json \
         --set data/lvis/set.json --img data/lvis/img --out out/recall [--clip openai/clip-vit-large-patch14 ...]
  python eval/items_second_look.py detect --model google/owlv2-large-patch14-ensemble --tiles 0 ... --out out/recall
         (another detector, or more phrases, over all the photos: saved like raw_tiles*.json)
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

FROM = 0.2  # candidates are saved from this score on; the rules tried later start higher
PER_NAME = 2  # at most this many candidates per item and photo, per phrase set
# more ways to say each item, for the zoomed look and for a wording test over whole pictures
MORE = {
    "keys": ["keys", "a key", "a bunch of keys", "a key ring", "car keys", "a house key", "a keychain"],
    "glasses": ["glasses", "eyeglasses", "spectacles", "a pair of glasses", "reading glasses"],
    "wallet": ["wallet", "a wallet", "a leather wallet", "a billfold"],
    "phone": ["phone", "a mobile phone", "a cell phone", "a smartphone"],
    "remote control": ["remote control", "a remote control", "a tv remote"],
}
EVERY = sorted({p for ps in MORE.values() for p in ps})
# texts for the classifier: the items, then things that get mistaken for them, then scenery
CLIP_TEXTS = [
    "keys", "a bunch of keys", "a key", "a key ring",
    "eyeglasses", "a pair of glasses", "spectacles",
    "a wallet", "a leather wallet",
    "a mobile phone", "a smartphone", "a cell phone",
    "a remote control", "a tv remote",
    "a pen", "a marker pen", "a bottle", "a tube", "a whiteboard eraser", "a computer keyboard", "a computer mouse",
    "a calculator", "a book", "a notebook", "a card", "coins", "a lock", "a door handle", "a knife", "a spoon", "a watch",
    "sunglasses", "a drinking glass", "a cup", "a hand", "a face", "a tablet computer", "a laptop", "a game controller",
    "a tv screen", "a cable", "a box", "a bag", "a piece of paper", "a toy", "food", "a plant", "a chair",
    "the floor", "a table", "a wall", "something else",
]
CLIP_SCALES = (1.3, 2.2)  # crop side as a multiple of the box's long side


def square(picture, box, scale, least=24):
    """A square crop around a box (0..1 coordinates): side = scale x the box's long side, kept inside the picture."""
    H, W = picture.shape[:2]
    x0, y0, x1, y1 = box[0] * W, box[1] * H, box[2] * W, box[3] * H
    side = int(round(min(max(scale * max(x1 - x0, y1 - y0), least), min(H, W))))
    x = int(round(min(max((x0 + x1) / 2 - side / 2, 0), W - side)))
    y = int(round(min(max((y0 + y1) / 2 - side / 2, 0), H - side)))
    return x, y, side


class Classifier:
    """CLIP as a zero-shot judge of a crop: cosine similarity with each text."""

    def __init__(self, model_id, device, texts):
        import torch
        from transformers import AutoTokenizer, CLIPModel

        self.torch, self.device, self.model_id = torch, device, model_id
        self.model = CLIPModel.from_pretrained(model_id, cache_dir=items.CACHE).to(device).eval()
        tokens = AutoTokenizer.from_pretrained(model_id, cache_dir=items.CACHE)(
            [items.TEMPLATE.format(t) for t in texts], padding=True, truncation=True, return_tensors="pt").to(device)
        with torch.no_grad():
            words = self.model.get_text_features(**tokens).float()
        self.words = words / words.norm(dim=-1, keepdim=True)
        self.side = int(self.model.config.vision_config.image_size)
        self.scale = float(self.model.logit_scale.exp())

    def look(self, crops):
        torch = self.torch
        pixels = np.stack([(cv2.cvtColor(cv2.resize(c, (self.side, self.side), interpolation=cv2.INTER_CUBIC), cv2.COLOR_BGR2RGB)
                            .astype(np.float32) / 255 - items.MEAN) / items.STD for c in crops])
        with torch.no_grad():
            seen = self.model.get_image_features(pixel_values=torch.from_numpy(pixels).permute(0, 3, 1, 2).float().to(self.device)).float()
        seen = seen / seen.norm(dim=-1, keepdim=True)
        return (seen @ self.words.T).cpu().numpy()


def candidates_of(entry, from_score=FROM, per_name=PER_NAME):
    """Candidate boxes of one photo from its saved raw boxes: for each item the best few under the
    shipped phrases and under the wider list, without letting one item's box push out another's."""
    raw = {p: (np.array(v)[:, :4], np.array(v)[:, 4]) for p, v in entry["raw"].items()}
    got = []
    for group in ("chosen", "wide"):
        for name in ev.NAMES:
            for d in items.merge(raw, [name], from_score, phrases=lambda n: ev.PHRASES[group][n])[:per_name]:
                if not any(c["name"] == name and items.iou(np.array(d["box"]), np.array(c["box"]))[0][0] >= 0.7 for c in got):
                    got.append(d)
    return got


def candidates(args):
    import torch

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    saved = {}
    for path in args.raw:
        saved.update(json.loads(Path(path).read_text())["boxes"])
    device = "cuda" if torch.cuda.is_available() else "cpu"
    detector = items.load(device=device, tiles=0)
    judges = [Classifier(m, device, CLIP_TEXTS) for m in args.clip or []]
    result, seconds, t_start = {}, {"zoom": [], "clip": []}, time.time()
    keys = sorted(saved)
    if args.only:
        keys = keys[:args.only]
    for n, key in enumerate(keys):
        group, file = key.split(":", 1)
        picture = cv2.imread(str(Path(args.img) / file) if group == "lvis" else file)
        if picture is None:
            continue
        picture = ev.shrink(picture)
        H, W = picture.shape[:2]
        found = candidates_of(saved[key])
        for c in found:
            x, y, side = square(picture, c["box"], args.zoom, least=64)
            t0 = time.time()
            raw = detector.detect(picture[y:y + side, x:x + side], EVERY, tiles=0, floor=0.1)
            seconds["zoom"].append(time.time() - t0)
            c["zoom_crop"] = [x, y, side]
            c["zoom"] = {}
            for phrase, (boxes, scores) in raw.items():  # back to 0..1 of the whole picture
                if len(scores):
                    whole = (boxes * side + [x, y, x, y]) / [W, H, W, H]
                    c["zoom"][phrase] = np.round(np.column_stack([whole, scores]), 4).tolist()
            if judges:
                t0 = time.time()
                crops = []
                for scale in CLIP_SCALES:
                    cx, cy, cs = square(picture, c["box"], scale)
                    crops.append(picture[cy:cy + cs, cx:cx + cs])
                c["clip"] = {j.model_id: np.round(j.look(crops), 4).tolist() for j in judges}
                seconds["clip"].append(time.time() - t0)
        result[key] = {"size": [W, H], "candidates": found}
        if n % 100 == 0:
            print(f"{n} of {len(keys)} photos, {sum(len(r['candidates']) for r in result.values())} candidates, "
                  f"{time.time() - t_start:.0f} s", flush=True)
    timing = {k: round(float(np.median(v[5:] or v or [0])), 3) for k, v in seconds.items()}
    timing.update(device=device, candidates=sum(len(r["candidates"]) for r in result.values()), photos=len(result))
    (out / "second_look.json").write_text(json.dumps({
        "zoom": args.zoom, "from": FROM, "texts": CLIP_TEXTS, "scales": CLIP_SCALES, "timing": timing,
        "clip_scale": {j.model_id: j.scale for j in judges}, "photos": result}))
    print(f"second looks saved: {timing}", flush=True)


def detect(args):
    """Whole-picture passes with another detector or more phrases, saved like items_eval's raw files."""
    import torch

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    todo = [("lvis", r["file"], str(Path(args.img) / r["file"])) for r in json.loads(Path(args.set).read_text())]
    for path in args.raw or []:  # the same item-free photos as before
        todo += [(k.split(":", 1)[0], k.split(":", 1)[1], k.split(":", 1)[1]) for k in json.loads(Path(path).read_text())["boxes"]
                 if not k.startswith("lvis:")]
    if args.only:
        todo = todo[:args.only]
    detector = items.Detector(model_id=args.model, device="cuda" if torch.cuda.is_available() else "cpu", tiles=args.tiles)
    boxes, seconds = {}, []
    for n, (group, key, file) in enumerate(todo):
        picture = cv2.imread(file)
        if picture is None:
            continue
        picture = ev.shrink(picture)
        raw = detector.detect(picture, EVERY, tiles=args.tiles)
        seconds.append(detector.last["seconds"])
        boxes[f"{group}:{key}"] = {"size": [picture.shape[1], picture.shape[0]],
                                   "raw": {p: np.round(np.column_stack([b, s]), 4).tolist() for p, (b, s) in raw.items() if len(s)}}
        if n % 100 == 0:
            print(f"{n} of {len(todo)} photos", flush=True)
    timing = {"median_seconds": round(float(np.median(seconds[5:] or seconds)), 3), "device": detector.device, "pictures": len(seconds),
              "model": args.model, "tiles": args.tiles, "input": detector.side}
    name = f"raw_{args.model.split('/')[-1]}_tiles{args.tiles}.json"
    (out / name).write_text(json.dumps({"tiles": args.tiles, "timing": timing, "boxes": boxes}))
    print(f"{name}: {timing}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["candidates", "detect"])
    ap.add_argument("--raw", action="append", help="raw_tiles*.json from eval/items_eval.py detect")
    ap.add_argument("--set")
    ap.add_argument("--img", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--clip", nargs="*", help="CLIP models to ask about each candidate")
    ap.add_argument("--zoom", type=float, default=4.0, help="side of the second-look crop, in box long sides")
    ap.add_argument("--model", default=items.MODEL_ID)
    ap.add_argument("--tiles", type=int, default=1)
    ap.add_argument("--only", type=int, default=0, help="only the first photos, for a quick try")
    args = ap.parse_args()
    {"candidates": candidates, "detect": detect}[args.step](args)


if __name__ == "__main__":
    main()
