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


def best_per_name(detections):
    """At most one detection per name: the best one."""
    seen, best = set(), []
    for d in sorted(detections, key=lambda d: -d["score"]):
        if d["name"] not in seen:
            seen.add(d["name"])
            best.append(d)
    return best


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
        self.model_id = model_id
        t0 = time.time()
        self.tokenizer = AutoTokenizer.from_pretrained(model_id, cache_dir=CACHE)
        self.model = Owlv2ForObjectDetection.from_pretrained(model_id, cache_dir=CACHE).to(self.device).eval()
        self.side = int(self.model.config.vision_config.image_size)  # 960: every pass is one square of this size
        self.words = {}  # phrase -> its text embedding, computed once
        self.last = {}  # what the last find() did: passes, seconds
        print(f"Item detector {model_id} loaded in {time.time() - t0:.0f} s on {self.device}", flush=True)

    def settings(self, names):
        return {"model": self.model_id, "names": list(names), "score": self.score, "confirm_score": min(self.score, CONFIRM),
                "tiles": self.tiles, "input": self.side, "device": self.device}

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
                logits, _ = self.model.class_predictor(flat, query.unsqueeze(0).expand(len(part), -1, -1))
                where = self.model.box_predictor(flat, maps)
            scores.append(logits.float().sigmoid().cpu().numpy())
            boxes.append(where.float().cpu().numpy())
        scores, boxes = np.concatenate(scores), np.concatenate(boxes)  # (pieces, patches, phrases), (pieces, patches, 4)

        raw = {}
        for k, phrase in enumerate(phrases):
            got_boxes, got_scores = [], []
            for i, (x, y, w, h) in enumerate(pieces):
                hit = np.flatnonzero(scores[i, :, k] >= floor)
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

    def find(self, image_bgr, names, score=None, tiles=None):
        """Every box of every name at or above the score limit, best first."""
        phrases = sorted({p for name in names for p in phrases_for(name)})
        return merge(self.detect(image_bgr, phrases, tiles), names, self.score if score is None else score)


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
                "position": None, "distance_m": None, "bearing_deg": None}
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
    fields = {"items": [], "items_looked_for": list(names), "items_checked": []}
    picture, saw = big_frame(clip), BIG
    if picture is not None and abs(picture.shape[1] / picture.shape[0] / (newest.shape[1] / newest.shape[0]) - 1) > 0.03:
        picture = None  # not the same picture as the newest frame: its boxes could not be mapped into the fix
        fields["items_note"] = f"{BIG} has another shape than the newest frame, so the small frame was searched."
    if picture is None:
        picture, saw = newest, "newest frame"
    every = detector.find(picture, names, score=min(detector.score, CONFIRM))  # down to the look-again limit
    found = best_per_name([d for d in every if d["score"] >= detector.score])  # sightings: the full limit only
    room = locate.map_frame(r, base, valid)
    window = crop_window(newest.shape, like.shape)
    items, marks = place(found, r, base, q, valid, window, state, room=room)
    write_crops(picture, items, out)
    fields["items"] = items
    fields["items_frame"] = {"source": saw, "width": picture.shape[1], "height": picture.shape[0], **detector.last}
    if remembered and state != "lost":  # without a fix a remembered spot cannot be found in this frame
        try:
            rest, _ = place([d for d in every if d not in found], r, base, q, valid, window, state, room=room)
            fields["items_checked"], left_out = check(remembered, names, items + rest, r, q, valid, window, room, picture.shape,
                                                      detector.side)
            if left_out:
                fields["items_note"] = (fields.get("items_note", "") + " Remembered spots left out (not looked for, or unreadable): "
                                        + ", ".join(left_out[:10]) + ".").strip()
        except Exception as error:  # the fix and the items found must still go out
            fields["items_checked"] = []
            fields["items_note"] = (fields.get("items_note", "") + f" Remembered spots were not checked: {type(error).__name__}: {error}").strip()[:300]
    return fields, marks
