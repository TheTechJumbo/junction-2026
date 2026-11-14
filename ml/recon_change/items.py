#!/usr/bin/env python3
"""Lost items: find a short list of small personal things ("keys", "glasses", ...) in the
wearer's newest picture, and put each one on the room map.

Finding: an open-vocabulary detector (OWLv2, Apache 2.0, through `transformers`) is asked for
each name in words, so "keys" needs no training of ours. Small things need pixels, so the
detector looks at the full-size picture (big.jpg in the job's zip) and, besides the whole
picture, at overlapping square tiles of it (TILES).

Placing: the position fix already has depth and a camera for the newest frame. A found box is
mapped from the picture the detector saw into that frame (which is a centre crop of it), the
depth in the middle of the box gives a 3D point, and that point goes on the same map as the
named places in locate.py: east, north, up in rough metres, distance along the floor, bearing
0 = straight ahead of the wearer and positive = to the right.

When in doubt it says nothing: a wrong "your keys are over there" is worse than "I have not
seen them". The score limit comes from eval/ITEMS_RESULTS.md.

Needs `transformers` (not in the base environment: see iridis/setup_items.sh). Importing this
file does not: only load() does.
"""
import os
import re
import time
import zipfile

import cv2
import numpy as np

import locate

MODEL_ID = os.environ.get("RECON_ITEMS_MODEL", "google/owlv2-base-patch16-ensemble")
# Where the detector's weights are, if not in the Hugging Face cache that HF_HOME names (the
# worker's HF_HOME is where VGGT is): a hub cache folder, e.g. <folder>/hf/hub from iridis/setup_items.sh.
CACHE = os.environ.get("RECON_ITEMS_CACHE") or None
DEFAULT_ITEMS = "keys, glasses, wallet, phone, remote control"
# A box counts from this score on. Chosen in eval/ITEMS_RESULTS.md; higher = fewer wrong answers, more misses.
SCORE = float(os.environ.get("RECON_ITEMS_SCORE", "0.5"))
# A lower limit for one question only: is a remembered item still at its spot (check)? There the place
# is already known, so a weaker box of that name within NEAR_M of it is enough, and the lower limit
# misses a present item far less often. It never creates a sighting.
CONFIRM = min(SCORE, float(os.environ.get("RECON_ITEMS_CONFIRM", "0.3")))


def per_item(text):
    """ "keys=0.45, glasses=0.3" -> {"keys": 0.45, "glasses": 0.3}. Anything unreadable is left out."""
    values = {}
    for part in (text or "").split(","):
        name, _, value = part.partition("=")
        try:
            values[" ".join(name.lower().split())] = float(value)
        except ValueError:
            continue
    return {name: value for name, value in values.items() if name}


# Finding more (eval/ITEMS_RESULTS.md, "Finding more"). All of it is off unless set: without these the
# finder does exactly what it did before.
# RECON_ITEMS_LIMITS="glasses=0.3, phone=0.425": a limit of its own for some items; the rest keep SCORE.
LIMITS = per_item(os.environ.get("RECON_ITEMS_LIMITS"))
# A second look at a box that scores below its item's limit but at least LOOK_FROM (0 = no second
# looks): the detector looks again at a square crop around the box, LOOK_ZOOM box sides wide and blown
# up to its input size. The box is accepted when its item scores at least the item's look limit there
# (RECON_ITEMS_LOOK_LIMITS, written like LIMITS; an item without one is never accepted this way).
# Each second look is one more pass, so there are at most LOOK_PER_ITEM per item and LOOK_MAX per picture.
LOOK_FROM = float(os.environ.get("RECON_ITEMS_LOOK_FROM", "0"))
LOOK_LIMITS = per_item(os.environ.get("RECON_ITEMS_LOOK_LIMITS"))
LOOK_MAX = int(os.environ.get("RECON_ITEMS_LOOK_MAX", "6"))
LOOK_ZOOM, LOOK_PER_ITEM, LOOK_LEAST = 4.0, 2, 64
# A second opinion on such a weak box from another model: a zero-shot classifier (CLIP, MIT licence) looks
# at two crops around the box and says how much they look like the item rather than like one of its
# look-alikes or like scenery (JUDGE_TEXTS). RECON_ITEMS_JUDGE names the model (empty = no judge), and
# RECON_ITEMS_JUDGE_LIMITS, written like LIMITS, the share an item needs. An item with both a look limit
# and a judge limit needs both to agree. The judge only knows the five default items.
JUDGE = os.environ.get("RECON_ITEMS_JUDGE", "")
JUDGE_LIMITS = per_item(os.environ.get("RECON_ITEMS_JUDGE_LIMITS"))
JUDGE_CROPS = (1.3, 2.2)  # crop sides, in box long sides
JUDGE_OF = {"keys": ["keys", "a bunch of keys", "a key", "a key ring"], "glasses": ["eyeglasses", "a pair of glasses", "spectacles"],
            "wallet": ["a wallet", "a leather wallet"], "phone": ["a mobile phone", "a smartphone", "a cell phone"],
            "remote control": ["a remote control", "a tv remote"]}
JUDGE_TEXTS = [t for texts in JUDGE_OF.values() for t in texts] + [
    "a pen", "a marker pen", "a bottle", "a tube", "a whiteboard eraser", "a computer keyboard", "a computer mouse",
    "a calculator", "a book", "a notebook", "a card", "coins", "a lock", "a door handle", "a knife", "a spoon", "a watch",
    "sunglasses", "a drinking glass", "a cup", "a hand", "a face", "a tablet computer", "a laptop", "a game controller",
    "a tv screen", "a cable", "a box", "a bag", "a piece of paper", "a toy", "food", "a plant", "a chair",
    "the floor", "a table", "a wall", "something else"]
# Show it once (enrolment). A folder of pictures of the wearer's own things, <folder>/<item name>/NN.jpg
# (close-ups or crops, up to 1280 px, written by the gateway). When it is there, every fix also looks for
# those very things: what the detector saw in each picture becomes one more question to it, next to the
# words ("image-guided detection"). A box that looks alike to a shown thing at ENROLLED_SCORE or more (cosine
# similarity of what the detector sees in the two boxes) is accepted
# and marked "matched": "enrolled". No folder: nothing changes. The folder is read again when a file in it changes.
ENROLLED = os.environ.get("RECON_ITEMS_ENROLLED") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "enrolled")
ENROLLED_SCORE = float(os.environ.get("RECON_ITEMS_ENROLLED_SCORE", "0.75"))
ENROLLED_PER_ITEM, ENROLLED_ITEMS = 4, 12  # pictures used per item, and items
ENROLLED_FLOOR = 0.4  # boxes less alike than this to a shown thing are not even kept as candidates
# RECON_ITEMS_CANDIDATES=0.25: also list every box from that score up that could be placed, accepted or
# not, as "items_candidates", so that the relay can add up weak sightings of one thing at one place over
# several looks. 0 = no such list.
CANDIDATES = float(os.environ.get("RECON_ITEMS_CANDIDATES", "0"))
# The stated confidence of an answer ("confidence_pct"): of the answers with the same kind of evidence on
# the tuning half of the test photos, the share that were right. The evidence is the item, the band its
# score falls in (bands start at these scores) and, for a weak box the judge was asked about, whether the
# judge agreed. Fitted by eval/items_recall.py --confidence; eval/ITEMS_RESULTS.md has the check on the
# other half and says where it is thin. A share on test photos, where the item is there in about half of
# them: at home, where it mostly is not, weak boxes will be right less often than stated.
CONFIDENCE = {"bands": [0.25, 0.35, 0.5, 0.6], "of": {
    "keys": {"any": [55, 72, 99, 97], "agreed": [94, 80, 98, 96], "disagreed": [32, 49, 94, 93]},
    "glasses": {"any": [62, 96, 99, 98], "agreed": [95, 98, 99, 97], "disagreed": [18, 65, 95, 93]},
    "wallet": {"any": [40, 82, 98, 98], "agreed": [90, 93, 97, 97], "disagreed": [22, 48, 92, 93]},
    "phone": {"any": [51, 85, 99, 99], "agreed": [92, 92, 99, 98], "disagreed": [36, 56, 96, 93]},
    "remote control": {"any": [14, 48, 99, 100], "agreed": [90, 93, 97, 99], "disagreed": [10, 38, 96, 98]},
    "other": {"any": [40, 77, 97, 97], "agreed": [90, 91, 96, 96], "disagreed": [22, 48, 92, 93]}}}
# A box that only an enrolled picture matched (the words did not find it): in the synthetic test in
# eval/ITEMS_RESULTS.md 3 of 35 such answers were right at similarity 0.7. Nothing real has measured it.
CONFIDENCE_ENROLLED = 10
# Rows of square tiles across the picture's short side, looked at besides the whole picture.
# 0 = whole picture only (1 pass), 1 = tiles as high as the picture (16:9 and 4:3: 3 passes), 2 = 2 rows.
TILES = int(os.environ.get("RECON_ITEMS_TILES", "1"))
OVERLAP = 0.2  # neighbouring tiles share at least this much of their side
FLOOR = 0.05  # boxes below this score are never kept, whatever the limit
BIG, BIG_SIDE, BIG_BYTES = "big.jpg", 1280, 12_000_000
MEAN, STD = (0.48145466, 0.4578275, 0.40821073), (0.26862954, 0.26130258, 0.27577711)  # CLIP's, RGB
TEMPLATE = "a photo of {}"
# What the detector is asked for, per name. "glasses" alone also means drinking glasses, "phone" a desk phone.
PHRASES = {
    "keys": ["keys", "a key"],
    "glasses": ["eyeglasses", "spectacles"],
    "wallet": ["a wallet"],
    "phone": ["a mobile phone", "a cell phone"],
    "remote control": ["a remote control"],
}
ITEM_COLOUR = (255, 90, 200)  # an item in the 3D view: pink (RGB)
# Looking again at a remembered spot (check): rough real size of each item's long side in metres,
# and the size in pixels (long side, in the whole picture scaled to the detector's 960) from which
# the detector is worth trusting to see it. Below that size it misses most things (eval/ITEMS_RESULTS.md).
REAL_SIZE_M = {"keys": 0.08, "glasses": 0.14, "wallet": 0.10, "phone": 0.15, "remote control": 0.18}
OTHER_SIZE_M = 0.10
CLOSE_PX = 64.0
VIEW_MARGIN = 0.03  # a spot this close to the frame's edge (share of the frame) does not count as in view
BLOCKED_SHARE, BLOCKED_M = 0.12, 0.10  # "something is in front": seen depth nearer than the spot by 12 % and by 10 cm
NEAR_M = 0.5  # a detection this close to the remembered spot is "the item is still there"
# A thing that is in the wearer's picture but not in the baseline photos often gets depth the model
# is not sure of (eval/ITEMS_RESULTS.md). Small things lie on something, so then the trusted depth of
# the surface right around the box is asked: the item is placed only if the model's depth on the item
# is within SURFACE_SHARE of it. RECON_ITEMS_SURFACE=0 turns this off (untrusted depth = no position).
SURFACE = os.environ.get("RECON_ITEMS_SURFACE", "1") != "0"
SURFACE_SHARE = 0.15


def wanted(text=None):
    """The item names to look for, from RECON_ITEMS (comma separated). Unset: the default list.
    Empty: nothing, which turns the feature off."""
    text = os.environ.get("RECON_ITEMS", DEFAULT_ITEMS) if text is None else text
    names = []
    for name in text.split(","):
        name = " ".join(name.lower().split())
        if name and name not in names:
            names.append(name)
    return names[:12]


def phrases_for(name):
    return PHRASES.get(name, [name])


def slug(name):
    """keys -> item_keys, remote control -> item_remote_control (a file name matching \\w+)."""
    return "item_" + (re.sub(r"\W+", "_", name.lower()).strip("_") or "thing")


# ----------------------------------------------------------------------------- boxes
def tile_layout(h, w, rows):
    """Square tiles (x, y, side) covering an h x w picture, `rows` of them across its short side."""
    if rows <= 0:
        return []
    side = int(round(min(h, w) / (rows - (rows - 1) * OVERLAP)))

    def starts(size):
        n = 1 if size <= side else int(np.ceil((size - side) / (side * (1 - OVERLAP)) - 1e-6)) + 1
        return sorted({int(round(v)) for v in np.linspace(0, size - side, n)})

    tiles = [(x, y, side) for y in starts(h) for x in starts(w)]
    return [] if len(tiles) == 1 and side >= max(h, w) else tiles


def iou(a, b):
    """Overlap of box a with each box in b (x0, y0, x1, y1): intersection over union, and the
    share of box a that lies inside each b."""
    b = np.atleast_2d(b)
    w = np.clip(np.minimum(a[2], b[:, 2]) - np.maximum(a[0], b[:, 0]), 0, None)
    h = np.clip(np.minimum(a[3], b[:, 3]) - np.maximum(a[1], b[:, 1]), 0, None)
    inter = w * h
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / np.maximum(area_a + area_b - inter, 1e-12), inter / max(area_a, 1e-12)


def thin(boxes, scores, same=0.5, inside=0.8):
    """Keep the best box of each thing: drop a box that overlaps a better one, or lies inside it."""
    keep = []
    for i in np.argsort(-np.asarray(scores)):
        if keep:
            overlap, within = iou(boxes[i], boxes[keep])
            if (overlap >= same).any() or (within >= inside).any():
                continue
        keep.append(int(i))
    return keep


def merge(raw, names, limit=None, phrases=phrases_for):
    """Raw boxes per phrase -> detections per name, best first.
    raw: {phrase: (boxes (n, 4) in 0..1, scores (n,))}. A name's boxes are those of all its phrases.
    One thing gets one name: a box that sits on a better-scored box of another name is dropped."""
    limit = SCORE if limit is None else limit
    found = []
    for name in names:
        parts = [raw[p] for p in phrases(name) if p in raw and len(raw[p][1])]
        if not parts:
            continue
        boxes, scores = np.concatenate([p[0] for p in parts]), np.concatenate([p[1] for p in parts])
        ok = scores >= limit
        boxes, scores = boxes[ok], scores[ok]
        found += [(float(scores[i]), name, boxes[i]) for i in thin(boxes, scores)]
    found.sort(key=lambda d: -d[0])
    kept = []
    for score, name, box in found:
        if any(other != name and iou(box, theirs)[0][0] >= 0.6 for _, other, theirs in kept):
            continue
        kept.append((score, name, box))
    return [{"name": name, "score": round(score, 3), "box": [round(float(v), 4) for v in box]} for score, name, box in kept]


def same_thing(box, other):
    """Are two boxes on the same thing? They overlap well (intersection over union 0.5), or the
    middle of `box` lies inside `other`, or the middle of `other` lies inside `box` and `box` is at
    most 6 times its area. (The looser rule of eval/items_eval.py: a bunch of keys and one key of it.)"""
    box, other = np.asarray(box, float), np.asarray(other, float)
    if iou(box, other)[0][0] >= 0.5:
        return True
    bx, by, ox, oy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2, (other[0] + other[2]) / 2, (other[1] + other[3]) / 2
    if other[0] <= bx <= other[2] and other[1] <= by <= other[3]:
        return True
    area, theirs = (box[2] - box[0]) * (box[3] - box[1]), (other[2] - other[0]) * (other[3] - other[1])
    return bool(box[0] <= ox <= box[2] and box[1] <= oy <= box[3] and area <= 6 * theirs)


def crop_around(shape, box, zoom, least=24):
    """A square (x, y, side) in pixels around a box (0..1): `zoom` times the box's long side, at
    least `least` pixels, kept inside the picture."""
    H, W = shape[:2]
    x0, y0, x1, y1 = box[0] * W, box[1] * H, box[2] * W, box[3] * H
    side = int(round(min(max(zoom * max(x1 - x0, y1 - y0), least), min(H, W))))
    x = int(round(min(max((x0 + x1) / 2 - side / 2, 0), W - side)))
    y = int(round(min(max((y0 + y1) / 2 - side / 2, 0), H - side)))
    return x, y, side


def confidence_pct(d, judge_limits=None):
    """How often an answer with this kind of evidence was right in the measurements, 0 to 100 (see CONFIDENCE)."""
    if d.get("only_enrolled"):
        return CONFIDENCE_ENROLLED
    table = CONFIDENCE["of"].get(d["name"], CONFIDENCE["of"]["other"])
    band = max([i for i, edge in enumerate(CONFIDENCE["bands"]) if d["score"] >= edge], default=0)
    said = "any"
    if d.get("judge") is not None and judge_limits and d["name"] in judge_limits:
        said = "agreed" if d["judge"] >= judge_limits[d["name"]] else "disagreed"
    return int(table[said][band])


def best_per_name(detections):
    """At most one detection per name: the best one."""
    seen, best = set(), []
    for d in sorted(detections, key=lambda d: (bool(d.get("only_enrolled")), -d["score"])):  # a box the words did not find comes last
        if d["name"] not in seen:
            seen.add(d["name"])
            best.append(d)
    return best


# ----------------------------------------------------------------------------- a second opinion
class Judge:
    """CLIP as a zero-shot classifier of a crop. share(picture, box, name): how much of its belief
    goes to the item's texts among all of JUDGE_TEXTS, averaged over two crops around the box."""

    def __init__(self, model_id=JUDGE, device=None):
        import torch
        from transformers import AutoTokenizer, CLIPModel

        self.torch, self.model_id = torch, model_id
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        t0 = time.time()
        self.model = CLIPModel.from_pretrained(model_id, cache_dir=CACHE).to(self.device).eval()
        tokens = AutoTokenizer.from_pretrained(model_id, cache_dir=CACHE)(
            [TEMPLATE.format(t) for t in JUDGE_TEXTS], padding=True, truncation=True, return_tensors="pt").to(self.device)
        with torch.no_grad():
            words = self.model.get_text_features(**tokens).float()
        self.words = words / words.norm(dim=-1, keepdim=True)
        self.side, self.scale = int(self.model.config.vision_config.image_size), float(self.model.logit_scale.exp())
        print(f"Item judge {model_id} loaded in {time.time() - t0:.0f} s on {self.device}", flush=True)

    def share(self, picture, box, name):
        if name not in JUDGE_OF:
            return 0.0
        torch = self.torch
        crops = []
        for zoom in JUDGE_CROPS:
            x, y, side = crop_around(picture.shape, box, zoom)
            crop = cv2.resize(picture[y:y + side, x:x + side], (self.side, self.side), interpolation=cv2.INTER_CUBIC)
            crops.append((cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32) / 255 - MEAN) / STD)
        with torch.no_grad():
            seen = self.model.get_image_features(pixel_values=torch.from_numpy(np.stack(crops)).permute(0, 3, 1, 2).float().to(self.device)).float()
            seen = seen / seen.norm(dim=-1, keepdim=True)
            belief = (self.scale * seen @ self.words.T).softmax(-1)
        mine = [JUDGE_TEXTS.index(t) for t in JUDGE_OF[name]]
        return float(belief[:, mine].sum(-1).mean())


# ----------------------------------------------------------------------------- detector
class Detector:
    """OWLv2, loaded once. find(image_bgr, names) -> [{"name", "score", "box": [u0, v0, u1, v1] in 0..1}]."""

    def __init__(self, model_id=MODEL_ID, device=None, tiles=None, score=None):
        import torch
        from transformers import AutoTokenizer, Owlv2ForObjectDetection

        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.tiles = TILES if tiles is None else tiles
        self.score = SCORE if score is None else score
        self.limits, self.look_from, self.look_limits = dict(LIMITS), LOOK_FROM, dict(LOOK_LIMITS)
        self.enrolled, self.enrolled_files, self.enrolled_score = {}, None, ENROLLED_SCORE  # item -> its picture questions
        self.judge, self.judge_limits = None, dict(JUDGE_LIMITS)
        self.model_id = model_id
        t0 = time.time()
        self.tokenizer = AutoTokenizer.from_pretrained(model_id, cache_dir=CACHE)
        self.model = Owlv2ForObjectDetection.from_pretrained(model_id, cache_dir=CACHE).to(self.device).eval()
        self.side = int(self.model.config.vision_config.image_size)  # 960: every pass is one square of this size
        self.words = {}  # phrase -> its text embedding, computed once
        self.last = {}  # what the last find() did: passes, seconds
        print(f"Item detector {model_id} loaded in {time.time() - t0:.0f} s on {self.device}", flush=True)
        if JUDGE and self.judge_limits and self.look_from > 0:
            self.judge = Judge(JUDGE, self.device)

    def settings(self, names):
        told = {"model": self.model_id, "names": list(names), "score": self.score, "confirm_score": min(self.score, CONFIRM),
                "tiles": self.tiles, "input": self.side, "device": self.device}
        if self.limits:
            told["limits"] = {n: self.limit(n) for n in names}
        if self.look_from > 0 and (self.look_limits or self.judge):
            told["second_look"] = {"from": self.look_from, "limits": self.look_limits, "most": LOOK_MAX}
            if self.judge:
                told["second_look"].update(judge=self.judge.model_id, judge_limits=self.judge_limits)
        if self.enrolled:
            told["enrolled"] = {"pictures": {n: len(q) for n, q in self.enrolled.items()}, "score": self.enrolled_score}
        if CANDIDATES > 0:
            told["candidates_from"] = CANDIDATES
        return told

    def limit(self, name):
        """The score from which a box of this item is accepted outright."""
        return self.limits.get(name, self.score)

    def text(self, phrases):
        torch = self.torch
        new = [p for p in phrases if p not in self.words]
        if new:
            tokens = self.tokenizer([TEMPLATE.format(p) for p in new], padding="max_length", max_length=16,
                                    truncation=True, return_tensors="pt").to(self.device)
            with torch.no_grad():
                found = self.model.owlv2.get_text_features(input_ids=tokens.input_ids, attention_mask=tokens.attention_mask)
            self.words.update(zip(new, found.float()))
        return torch.stack([self.words[p] for p in phrases])

    def square(self, piece):
        """A BGR piece of the picture as the model wants it: on a grey square (top left), resized, normalised."""
        h, w = piece.shape[:2]
        side = max(h, w)
        canvas = np.full((side, side, 3), 128, np.uint8)
        canvas[:h, :w] = piece
        how = cv2.INTER_AREA if side > self.side else cv2.INTER_LINEAR
        rgb = cv2.cvtColor(cv2.resize(canvas, (self.side, self.side), interpolation=how), cv2.COLOR_BGR2RGB)
        return (rgb.astype(np.float32) / 255 - MEAN) / STD

    def detect(self, image_bgr, phrases, tiles=None, floor=FLOOR, batch=4):
        """Raw boxes for each phrase: {phrase: (boxes (n, 4) as x0, y0, x1, y1 in 0..1 of the picture, scores)}.
        Boxes of one phrase that show the same thing are already thinned to the best one."""
        torch = self.torch
        t0 = time.time()
        H, W = image_bgr.shape[:2]
        pieces = [(0, 0, W, H)] + [(x, y, s, s) for x, y, s in tile_layout(H, W, self.tiles if tiles is None else tiles)]
        query = self.text(phrases)
        shown = [k for k, phrase in enumerate(phrases) if phrase.startswith("@")]  # questions that are pictures (enrol)
        boxes, scores = [], []
        for start in range(0, len(pieces), batch):
            part = pieces[start:start + batch]
            pixels = np.stack([self.square(image_bgr[y:y + h, x:x + w]) for x, y, w, h in part])
            pixels = torch.from_numpy(pixels).permute(0, 3, 1, 2).float().to(self.device)
            with torch.no_grad():
                if self.device == "cuda":
                    dtype = torch.bfloat16 if torch.cuda.get_device_capability()[0] >= 8 else torch.float16
                    with torch.autocast("cuda", dtype=dtype):
                        maps, _ = self.model.image_embedder(pixel_values=pixels)
                else:
                    maps, _ = self.model.image_embedder(pixel_values=pixels)
                maps = maps.float()  # the two small heads run in float32: box corners need the precision
                flat = maps.reshape(maps.shape[0], -1, maps.shape[-1])
                logits, looks = self.model.class_predictor(flat, query.unsqueeze(0).expand(len(part), -1, -1))
                where = self.model.box_predictor(flat, maps)
                score = logits.float().sigmoid()
                if shown:  # a picture is answered by how alike a box looks to it (cosine): on the words' scale every box would score 1
                    looks = looks.float() / (looks.float().norm(dim=-1, keepdim=True) + 1e-6)
                    asked = query[shown] / (query[shown].norm(dim=-1, keepdim=True) + 1e-6)
                    score[..., shown] = (looks @ asked.T).clamp(0, 1)
            scores.append(score.cpu().numpy())
            boxes.append(where.float().cpu().numpy())
        scores, boxes = np.concatenate(scores), np.concatenate(boxes)  # (pieces, patches, phrases), (pieces, patches, 4)

        raw = {}
        for k, phrase in enumerate(phrases):
            got_boxes, got_scores = [], []
            for i, (x, y, w, h) in enumerate(pieces):
                hit = np.flatnonzero(scores[i, :, k] >= (max(floor, ENROLLED_FLOOR) if k in shown else floor))
                if k in shown:  # most boxes look a little like anything: only the most alike are worth keeping
                    hit = hit[np.argsort(-scores[i, hit, k])[:30]]
                if not len(hit):
                    continue
                cx, cy, bw, bh = (boxes[i, hit] * max(w, h)).T  # the model's boxes are centre and size, in the square
                box = np.stack([cx - bw / 2 + x, cy - bh / 2 + y, cx + bw / 2 + x, cy + bh / 2 + y], 1)
                ok = (box[:, 0] < x + w - 1) & (box[:, 1] < y + h - 1)  # not a box out on the grey padding
                if i > 0:  # a tile: a box on a cut edge is half a thing; the next tile or the whole picture has all of it
                    edge = 2.0
                    ok &= ~(((box[:, 0] <= x + edge) & (x > 0)) | ((box[:, 1] <= y + edge) & (y > 0))
                            | ((box[:, 2] >= x + w - edge) & (x + w < W)) | ((box[:, 3] >= y + h - edge) & (y + h < H)))
                box = np.clip(box, [x, y, x, y], [x + w, y + h, x + w, y + h]) / [W, H, W, H]
                ok &= (box[:, 2] - box[:, 0] > 0) & (box[:, 3] - box[:, 1] > 0)
                got_boxes.append(box[ok])
                got_scores.append(scores[i, hit, k][ok])
            if got_boxes:
                b, s = np.concatenate(got_boxes), np.concatenate(got_scores)
                keep = thin(b, s)
                raw[phrase] = (b[keep], s[keep])
            else:
                raw[phrase] = (np.zeros((0, 4)), np.zeros(0))
        if self.device == "cuda":
            torch.cuda.synchronize()
        self.last = {"passes": len(pieces), "seconds": round(time.time() - t0, 3), "size": [W, H]}
        return raw

    def describe(self, picture, phrases):
        """One pass over a whole picture: every box the model proposes (x0, y0, x1, y1 in 0..1), what
        each looks like (a unit vector) and its score for each phrase."""
        torch = self.torch
        H, W = picture.shape[:2]
        pixels = torch.from_numpy(self.square(picture)[None]).permute(0, 3, 1, 2).float().to(self.device)
        query = self.text(list(phrases))
        with torch.no_grad():
            if self.device == "cuda":
                dtype = torch.bfloat16 if torch.cuda.get_device_capability()[0] >= 8 else torch.float16
                with torch.autocast("cuda", dtype=dtype):
                    maps, _ = self.model.image_embedder(pixel_values=pixels)
            else:
                maps, _ = self.model.image_embedder(pixel_values=pixels)
            maps = maps.float()
            flat = maps.reshape(1, -1, maps.shape[-1])
            logits, looks = self.model.class_predictor(flat, query.unsqueeze(0))
            where = self.model.box_predictor(flat, maps)
        looks = looks[0].float()
        looks = (looks / (looks.norm(dim=-1, keepdim=True) + 1e-6)).cpu().numpy()
        cx, cy, bw, bh = (where[0].float().cpu().numpy() * max(H, W)).T
        boxes = np.stack([(cx - bw / 2) / W, (cy - bh / 2) / H, (cx + bw / 2) / W, (cy + bh / 2) / H], 1)
        return boxes, looks, logits[0].float().sigmoid().cpu().numpy()

    def enrol(self, name, pictures):
        """Learn one of the wearer's own things from pictures of it. Each picture gives one question
        for later: what its box in the picture looks like. The box is the one the words find best
        there; if they find nothing, the box that fills the picture (as OWLv2's own image-guided
        detection picks it). Returns how many pictures were used."""
        for old in self.enrolled.pop(name, []):
            self.words.pop(old, None)
        asked = []
        for picture in pictures[:ENROLLED_PER_ITEM]:
            boxes, looks, scores = self.describe(picture, phrases_for(name))
            inside = (boxes[:, 0] > -0.03) & (boxes[:, 1] > -0.03) & (boxes[:, 2] < 1.03) & (boxes[:, 3] < 1.03)  # not out on the grey padding
            if not inside.any():
                continue
            named = scores.max(1) * inside
            if named.max() >= 0.15:
                pick = int(np.argmax(named))
            else:
                cover = iou(np.array([0.0, 0.0, 1.0, 1.0]), boxes)[0] * inside
                near = np.flatnonzero(cover >= 0.8 * cover.max())
                pick = int(near[np.argmin(looks[near] @ looks.mean(0))])
            key = f"@{name}#{len(asked)}"
            self.words[key] = self.torch.from_numpy(looks[pick]).float().to(self.device)
            asked.append(key)
        if asked:
            self.enrolled[name] = asked
        return len(asked)

    def refresh_enrolled(self, folder=None):
        """Read the folder of enrolled pictures again if anything in it changed. Returns True if it did."""
        folder = ENROLLED if folder is None else folder
        files = []
        if os.path.isdir(folder):
            for name in sorted(os.listdir(folder))[:ENROLLED_ITEMS]:
                inside = os.path.join(folder, name)
                if os.path.isdir(inside):
                    files += [os.path.join(inside, f) for f in sorted(os.listdir(inside)) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
        seen = tuple((f, os.path.getmtime(f), os.path.getsize(f)) for f in files)
        if seen == self.enrolled_files:
            return False
        self.enrolled_files = seen
        for name in list(self.enrolled):
            for old in self.enrolled.pop(name):
                self.words.pop(old, None)
        by_item = {}
        for f in files:
            by_item.setdefault(" ".join(os.path.basename(os.path.dirname(f)).lower().replace("_", " ").split()), []).append(f)
        for name, paths in by_item.items():
            try:
                pictures = [cv2.imread(f) for f in paths[:ENROLLED_PER_ITEM]]
                pictures = [cv2.resize(q, None, fx=BIG_SIDE / max(q.shape[:2]), fy=BIG_SIDE / max(q.shape[:2]), interpolation=cv2.INTER_AREA)
                            if max(q.shape[:2]) > BIG_SIDE else q for q in pictures if q is not None]
                used = self.enrol(name, pictures)
                print(f"Enrolled '{name}' from {used} of {len(paths)} pictures", flush=True)
            except Exception as error:  # one bad picture must not stop the fixes
                print(f"Could not enrol '{name}': {type(error).__name__}: {error}", flush=True)
        return True

    def candidates(self, image_bgr, names, floor=None, tiles=None):
        """Every box of every name, best first, each marked "accepted" or not, and "matched": "text"
        (found by the words) or "enrolled" (found by a picture of the wearer's own thing).
        Accepted: a text box that scores at least its item's limit, or at least look_from with a
        second, zoomed look that agrees (it then carries "second_look", the zoomed score, or "judge", the second opinion; "score" stays the first look's); an
        enrolled box that scores at least enrolled_score. Boxes that are not accepted are listed
        down to `floor`, for the look-again check and the candidates list; they are never sightings."""
        phrases = sorted({p for name in names for p in phrases_for(name)})
        shown = [q for name in names for q in self.enrolled.get(name, [])]
        raw = self.detect(image_bgr, phrases + shown, tiles)
        first = dict(self.last)
        H, W = image_bgr.shape[:2]
        looking = self.look_from > 0
        got, looks = [], []
        for name in names:
            if self.enrolled.get(name):  # this very thing, as it was shown
                for d in merge(raw, [name], max(ENROLLED_FLOOR, self.enrolled_score - 0.1), phrases=lambda n: self.enrolled[n]):
                    d["matched"], d["accepted"], d["only_enrolled"] = "enrolled", d["score"] >= self.enrolled_score, True
                    got.append(d)
            limit = self.limit(name)
            start = self.look_from if looking and (name in self.look_limits or (self.judge and name in self.judge_limits)) else limit
            for d in merge(raw, [name], min(limit, start, limit if floor is None else floor)):
                d["matched"], d["accepted"] = "text", d["score"] >= limit
                got.append(d)
                if not d["accepted"] and d["score"] >= start and sum(1 for other in looks if other["name"] == name) < LOOK_PER_ITEM:
                    looks.append(d)
        seconds = first.get("seconds", 0)
        sure = [d for d in got if d["accepted"]]  # a thing that already has a name gets no second look under another
        looks = [d for d in looks if not any(k["name"] != d["name"] and iou(np.array(d["box"]), np.array(k["box"]))[0][0] >= 0.6 for k in sure)]
        looks = sorted(looks, key=lambda d: -d["score"])[:max(0, LOOK_MAX)]
        passes = 0
        for d in looks:
            agreed, t0 = True, time.time()
            if self.judge and d["name"] in self.judge_limits:  # the other model's opinion: cheap, so it is asked first
                d["judge"] = round(self.judge.share(image_bgr, d["box"], d["name"]), 3)
                agreed = d["judge"] >= self.judge_limits[d["name"]]
                seconds += time.time() - t0
            if agreed and d["name"] in self.look_limits:  # one more pass: the crop around the box, as large as the detector takes it
                x, y, side = crop_around(image_bgr.shape, d["box"], LOOK_ZOOM, LOOK_LEAST)
                again = self.detect(image_bgr[y:y + side, x:x + side], phrases_for(d["name"]), tiles=0, floor=0.1)
                seconds += self.last["seconds"]
                passes += 1
                seen = 0.0
                for boxes, scores in again.values():
                    for box, score in zip(boxes, scores):
                        whole = (box * side + [x, y, x, y]) / [W, H, W, H]
                        if score > seen and same_thing(whole, d["box"]):
                            seen = float(score)
                d["second_look"] = round(seen, 3)
                agreed = seen >= self.look_limits[d["name"]]
            d["accepted"] = bool(agreed)
        if looks:
            self.last = {**first, "passes": first.get("passes", 0) + passes, "seconds": round(seconds, 3), "second_looks": len(looks)}
        else:
            self.last = first
        for d in got:
            d["confidence_pct"] = confidence_pct(d, self.judge_limits)
        got.sort(key=lambda d: (-d["accepted"], "only_enrolled" in d, -d["score"]))  # boxes the words found come first
        kept = []
        for d in got:  # one thing gets one name and one entry; a box that is not accepted never pushes an accepted one out
            if any(k["name"] != d["name"] and iou(np.array(d["box"]), np.array(k["box"]))[0][0] >= 0.6 for k in kept):
                continue
            twin = next((i for i, k in enumerate(kept) if k["name"] == d["name"] and ("only_enrolled" in k) != ("only_enrolled" in d)
                         and same_thing(d["box"], k["box"])), None)
            if twin is not None:  # found by the words and by its picture: one entry, with the words' box, score and confidence
                worded, shown = (d, kept[twin]) if "only_enrolled" in kept[twin] else (kept[twin], d)
                worded["accepted"] = worded["accepted"] or shown["accepted"]
                if shown["score"] >= self.enrolled_score:
                    worded["matched"] = "enrolled"
                kept[twin] = worded
                continue
            kept.append(d)
        return kept

    def find(self, image_bgr, names, score=None, tiles=None):
        """Every accepted box of every name, best first. With `score`, plainly every box at or above
        that one limit, with no limits per item and no second looks."""
        if score is not None:
            phrases = sorted({p for name in names for p in phrases_for(name)})
            return merge(self.detect(image_bgr, phrases, tiles), names, score)
        return [d for d in self.candidates(image_bgr, names, tiles=tiles) if d["accepted"]]


def load(device=None, **settings):
    """Load the detector once, at worker start."""
    return Detector(device=device, **settings)


# ----------------------------------------------------------------------------- the picture to look at
def big_frame(clip):
    """The wearer's newest picture at full size, if the relay put it in the job's zip as big.jpg.
    Decoded in memory: nothing is written to disk. None when there is none or it cannot be read."""
    try:
        if str(clip).lower().endswith(".zip"):
            with zipfile.ZipFile(clip) as bundle:
                if BIG in bundle.namelist() and bundle.getinfo(BIG).file_size <= BIG_BYTES:
                    picture = cv2.imdecode(np.frombuffer(bundle.read(BIG), np.uint8), cv2.IMREAD_COLOR)
                    if picture is not None and min(picture.shape[:2]) >= 16:
                        scale = BIG_SIDE / max(picture.shape[:2])
                        if scale < 1:
                            picture = cv2.resize(picture, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                        return picture
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, cv2.error):
        pass
    return None


def crop_window(shape, like):
    """Which part of a picture worker.crop_like keeps, as (x0, y0, x1, y1) in 0..1 of that picture."""
    h, w = shape[:2]
    target = like[1] / like[0]
    if w / h > target:
        keep = max(1, round(h * target))
        return (w - keep) // 2 / w, 0.0, ((w - keep) // 2 + keep) / w, 1.0
    keep = max(1, round(w / target))
    return 0.0, (h - keep) // 2 / h, 1.0, ((h - keep) // 2 + keep) / h


# ----------------------------------------------------------------------------- on the map
def surface_depth(depth, valid, x, y, half_w, half_h, middle):
    """Depth for an item whose own depth is not trusted: the model's depth in the middle of its box,
    but only when the trusted surface in a band around the box lies at the same depth. None otherwise."""
    H, W = depth.shape
    grow_x, grow_y = max(4, half_w // 2), max(4, half_h // 2)
    top, left = max(0, y - half_h - grow_y), max(0, x - half_w - grow_x)
    piece, trusted = depth[top:y + half_h + grow_y + 1, left:x + half_w + grow_x + 1], valid[top:y + half_h + grow_y + 1, left:x + half_w + grow_x + 1]
    band = np.ones(piece.shape, bool)
    band[max(0, y - half_h - top):y + half_h + 1 - top, max(0, x - half_w - left):x + half_w + 1 - left] = False
    around = trusted & band & (piece > 0)
    middle = middle[middle > 0]
    if around.sum() < max(8, 0.25 * band.sum()) or not len(middle):
        return None
    surface, own = float(np.median(piece[around])), float(np.median(middle))
    return own if abs(own - surface) <= SURFACE_SHARE * surface else None


def place(found, r, base, q, valid, window, state, typical_m=2.0, room=None):
    """Put detections on the room map, with the same axes and the same distance and bearing as the
    named places in locate.locate (the map transform is locate.map_frame, or `room` if given).
    found    detections, boxes in 0..1 of the picture the detector saw
    r, base  the reconstruction of this fix and the indices of its baseline frames
    q        index in r of the frame that picture became (the wearer's newest frame)
    valid    locate's trusted-pixel mask
    window   the part of the detector's picture that frame q shows (crop_window)
    state    the fix: "found", "unsure" or "lost". Without a fix nothing is placed.
    Returns (items, marks): JSON-ready dicts, and balls for the 3D view. An item that cannot be
    placed keeps position None and says why in "note"."""
    room = locate.map_frame(r, base, valid, typical_m) if room is None else room
    metres = room["metres"]
    eye, forward = locate.camera(r.E[q])
    heading = float(np.degrees(np.arctan2(forward @ room["east"], forward @ room["north"])))

    def on_map(point):
        return locate.to_map(room, point)[0]

    here = on_map(eye)
    H, W = r.depth[q].shape
    x0, y0, x1, y1 = window
    K, E = r.K[q], r.E[q]
    items, marks = [], []
    for d in found:
        item = {"name": d["name"], "score": round(float(d["score"]), 2), "box": [round(float(v), 4) for v in d["box"]],
                "position": None, "distance_m": None, "bearing_deg": None, "matched": d.get("matched", "text"),
                "confidence_pct": d["confidence_pct"] if "confidence_pct" in d else confidence_pct(d)}
        items.append(item)
        if state == "lost":
            item["note"] = "No position fix, so it is not on the map."
            continue
        u0, v0, u1, v1 = d["box"]
        cu, cv = ((u0 + u1) / 2 - x0) / (x1 - x0), ((v0 + v1) / 2 - y0) / (y1 - y0)
        if not (0 <= cu <= 1 and 0 <= cv <= 1):
            item["note"] = "Seen at the edge of the picture, outside the part the position fix used."
            continue
        x, y = int(round(cu * (W - 1))), int(round(cv * (H - 1)))
        # depth: the middle half of the box, at least 5 x 5 pixels, trusted pixels only
        rx = max(2, int(round((u1 - u0) / (x1 - x0) * (W - 1) / 4)))
        ry = max(2, int(round((v1 - v0) / (y1 - y0) * (H - 1) / 4)))
        rows, cols = slice(max(0, y - ry), y + ry + 1), slice(max(0, x - rx), x + rx + 1)
        ok = valid[q][rows, cols] & (r.depth[q][rows, cols] > 0)
        if ok.sum() >= max(3, 0.25 * ok.size):
            depth = float(np.median(r.depth[q][rows, cols][ok]))
        else:
            depth = surface_depth(r.depth[q], valid[q], x, y, 2 * rx, 2 * ry, r.depth[q][rows, cols]) if SURFACE else None
            if depth is None:
                item["note"] = (f"The depth there is not trusted ({int(ok.sum())} of {ok.size} pixels in the middle of the box), "
                                "so it is not on the map.")
                continue
            item["note"] = "Placed with the help of the surface around it: the depth on the item itself is not trusted."
        cam = np.array([(x - K[0, 2]) / K[0, 0] * depth, (y - K[1, 2]) / K[1, 1] * depth, depth])
        world = (cam - E[:, 3]) @ E[:, :3]
        at = on_map(world)
        east_m, north_m = at[0] - here[0], at[1] - here[1]
        bearing = (np.degrees(np.arctan2(east_m, north_m)) - heading + 180) % 360 - 180  # 0 ahead, + to the right
        item.update(position=[round(float(v), 2) for v in at], distance_m=round(float(np.hypot(east_m, north_m)), 1),
                    bearing_deg=int(round(float(bearing))))
        marks.append((world, 1.2 * 0.07 / metres, ITEM_COLOUR))
    return items, marks


def check(remembered, names, seen, r, q, valid, window, room, picture_shape, input_side=960):
    """Look again at spots where items were seen before, in the wearer's newest frame. Four facts
    per spot, no verdict: the detector misses small things, so "gone" is for the caller to decide
    over several looks.
    remembered  [{"name", "position": [east, north, up]}] in map coordinates (as place() gives them)
    names       what the detector looked for in this frame
    seen        every detection of this frame after place(): its "box" and, when placed, "position"
    r, q, valid, window, room   as for place(); picture_shape: the picture the detector saw
    Returns (checked, left_out). checked: one dict per usable remembered spot with
      in_view        in front of the camera and inside the frame the position fix used, off its edge
      blocked        the depth seen at that pixel is clearly nearer than the spot, or not trusted
      close_enough   at that distance the item would be at least CLOSE_PX long in the detector's picture
      detected_near  the detector found that item within NEAR_M of the spot (or, for a detection
                     that could not be placed, with the spot's pixel inside its box)
      pixel          [u, v] in 0..1 of the picture the detector saw (like "box"); None if behind the camera
      distance_m     along the floor from the wearer, like every other distance_m
      size_px, depth_gap_m   what close_enough and blocked were decided from (None when unknown):
                     expected long side in pixels; seen depth minus the spot's depth in metres
    left_out: names of remembered entries that were skipped (malformed, or not looked for)."""
    H, W = r.depth[q].shape
    K, E = r.K[q], r.E[q]
    x0, y0, x1, y1 = window
    metres = room["metres"]
    eye, _ = locate.camera(E)
    here = locate.to_map(room, eye)[0]
    # one pixel of the model's frame is this many pixels of the detector's whole-picture pass
    zoom = (x1 - x0) * picture_shape[1] * (input_side / max(picture_shape[:2])) / W
    checked, left_out = [], []
    for entry in list(remembered)[:50]:
        try:
            name = " ".join(str(entry["name"]).lower().split())
            spot = np.array([float(v) for v in entry["position"]])
            usable = spot.shape == (3,) and bool(np.isfinite(spot).all()) and name in names
        except (KeyError, TypeError, ValueError):
            name, usable = str(entry.get("name", "?") if isinstance(entry, dict) else "?"), False
        if not usable:
            left_out.append(name)
            continue
        world = locate.from_map(room, spot)[0]
        cam = E[:, :3] @ world + E[:, 3]
        depth = float(cam[2])
        floor = float(np.hypot(spot[0] - here[0], spot[1] - here[1]))
        fact = {"name": name, "in_view": False, "blocked": False, "close_enough": False, "detected_near": False,
                "pixel": None, "distance_m": round(floor, 1), "size_px": None, "depth_gap_m": None}
        checked.append(fact)
        for d in seen:  # is the item itself still there?
            if d["name"] != name:
                continue
            if d.get("position") is not None:
                fact["detected_near"] |= bool(np.linalg.norm(np.array(d["position"]) - spot) <= NEAR_M)
        if depth <= 1e-6:
            continue  # behind the wearer
        x, y = K[0, 0] * cam[0] / depth + K[0, 2], K[1, 1] * cam[1] / depth + K[1, 2]
        cu, cv = x / (W - 1), y / (H - 1)
        u, v = x0 + cu * (x1 - x0), y0 + cv * (y1 - y0)
        fact["pixel"] = [round(float(u), 4), round(float(v), 4)]
        for d in seen:  # a detection that has no position: is the spot inside its box?
            if d["name"] == name and d.get("position") is None:
                bu0, bv0, bu1, bv1 = d["box"]
                grow_u, grow_v = 0.25 * (bu1 - bu0), 0.25 * (bv1 - bv0)
                fact["detected_near"] |= bool(bu0 - grow_u <= u <= bu1 + grow_u and bv0 - grow_v <= v <= bv1 + grow_v)
        size = REAL_SIZE_M.get(name, OTHER_SIZE_M) / max(float(np.linalg.norm(cam)) * metres, 1e-6) * float(K[0, 0]) * zoom
        fact["size_px"] = int(round(min(size, 9999)))
        fact["close_enough"] = bool(size >= CLOSE_PX)
        fact["in_view"] = bool(VIEW_MARGIN <= cu <= 1 - VIEW_MARGIN and VIEW_MARGIN <= cv <= 1 - VIEW_MARGIN)
        if not fact["in_view"]:
            continue
        px, py = int(round(x)), int(round(y))
        rows, cols = slice(max(0, py - 2), py + 3), slice(max(0, px - 2), px + 3)
        ok = valid[q][rows, cols] & (r.depth[q][rows, cols] > 0)
        if ok.sum() < max(3, 0.25 * ok.size):
            fact["blocked"] = True  # the depth there is not trusted: we cannot say the spot was seen
            continue
        gap = float(np.median(r.depth[q][rows, cols][ok])) - depth  # negative: something nearer than the spot
        fact["depth_gap_m"] = round(gap * metres, 2)
        fact["blocked"] = bool(-gap > max(BLOCKED_SHARE * depth, BLOCKED_M / metres))
    return checked, left_out


def write_crops(picture, items, out):
    """A small picture of each item with some room around it, as out/item_<name>.jpg, so a person
    can check what was found. Sets item["image"] to the file name without ".jpg"."""
    H, W = picture.shape[:2]
    for item in items:
        u0, v0, u1, v1 = item["box"]
        bx0, by0, bx1, by1 = u0 * W, v0 * H, u1 * W, v1 * H
        side = min(max(2.5 * max(bx1 - bx0, by1 - by0), 0.2 * min(H, W)), min(H, W))
        x = int(round(min(max((bx0 + bx1) / 2 - side / 2, 0), W - side)))
        y = int(round(min(max((by0 + by1) / 2 - side / 2, 0), H - side)))
        side = int(side)
        crop = picture[y:y + side, x:x + side].copy()
        scale = 320 / max(crop.shape[:2])
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
        corner = (int((bx0 - x) * scale) - 2, int((by0 - y) * scale) - 2), (int((bx1 - x) * scale) + 2, int((by1 - y) * scale) + 2)
        cv2.rectangle(crop, corner[0], corner[1], (255, 255, 255), 3)
        cv2.rectangle(crop, corner[0], corner[1], (200, 90, 255), 1)
        name = slug(item["name"])
        if cv2.imwrite(str(out / f"{name}.jpg"), crop, [cv2.IMWRITE_JPEG_QUALITY, 85]):
            item["image"] = name


def look(detector, names, clip, newest, like, r, base, q, valid, state, out, remembered=None):
    """The whole lost-items step of one position fix. newest: the wearer's newest frame before it
    was cropped for the model; like: a baseline frame (the shape it was cropped to); remembered:
    spots where items were seen before, to look at again (see check).
    Returns (fields to add to the locate result, balls for the 3D view)."""
    if detector.refresh_enrolled():
        print(f"Enrolled items now: {sorted(detector.enrolled)}", flush=True)
    names = list(names) + [n for n in detector.enrolled if n not in names]  # a shown thing is looked for, listed or not
    fields = {"items": [], "items_looked_for": list(names), "items_checked": [], "items_candidates": []}
    picture, saw = big_frame(clip), BIG
    if picture is not None and abs(picture.shape[1] / picture.shape[0] / (newest.shape[1] / newest.shape[0]) - 1) > 0.03:
        picture = None  # not the same picture as the newest frame: its boxes could not be mapped into the fix
        fields["items_note"] = f"{BIG} has another shape than the newest frame, so the small frame was searched."
    if picture is None:
        picture, saw = newest, "newest frame"
    lowest = min([detector.score, CONFIRM] + ([CANDIDATES] if CANDIDATES > 0 else []))
    every = detector.candidates(picture, names, floor=lowest)  # down to the look-again and candidates limits
    found = best_per_name([d for d in every if d["accepted"]])  # sightings: accepted boxes only
    room = locate.map_frame(r, base, valid)
    window = crop_window(newest.shape, like.shape)
    items, marks = place(found, r, base, q, valid, window, state, room=room)
    write_crops(picture, items, out)
    fields["items"] = items
    fields["items_frame"] = {"source": saw, "width": picture.shape[1], "height": picture.shape[0], **detector.last}
    if CANDIDATES > 0 and state != "lost":  # every box that could be placed, weak ones too, for the relay to add up over looks
        try:
            listed, _ = place([d for d in every if d["accepted"] or d.get("only_enrolled") or d["score"] >= CANDIDATES],
                              r, base, q, valid, window, state, room=room)
            fields["items_candidates"] = [{k: c[k] for k in ("name", "score", "confidence_pct", "position", "box", "matched", "distance_m", "bearing_deg")}
                                          for c in listed if c["position"] is not None]
        except Exception as error:
            fields["items_note"] = (fields.get("items_note", "") + f" Candidates were not listed: {type(error).__name__}: {error}").strip()[:300]
    if remembered and state != "lost":  # without a fix a remembered spot cannot be found in this frame
        try:
            floor = min(detector.score, CONFIRM)  # a weaker box than that is no sign the item is still there
            rest, _ = place([d for d in every if d not in found and (d["accepted"] or (d["score"] >= floor and not d.get("only_enrolled")))],
                            r, base, q, valid, window, state, room=room)
            fields["items_checked"], left_out = check(remembered, names, items + rest, r, q, valid, window, room, picture.shape,
                                                      detector.side)
            if left_out:
                fields["items_note"] = (fields.get("items_note", "") + " Remembered spots left out (not looked for, or unreadable): "
                                        + ", ".join(left_out[:10]) + ".").strip()
        except Exception as error:  # the fix and the items found must still go out
            fields["items_checked"] = []
            fields["items_note"] = (fields.get("items_note", "") + f" Remembered spots were not checked: {type(error).__name__}: {error}").strip()[:300]
    return fields, marks
