#!/usr/bin/env python3
"""A room as a Gaussian splat from one ordinary video: record normally (the phone or the glasses, no app running), then
this goes through every frame afterwards. Heavy: run it on a GPU with room to spare (iridis/video_map.slurm).

    python video_map.py VIDEO OUT [--keyframes 96] [--change 0.9] [--steps 7000] [--no-train]
    python video_map.py FRAMES_FOLDER OUT ...    (a recording: frames NNNNNN_<ms>.jpg, relay POST /recording)

1. Every frame is read once: its sharpness and a small grey copy. A keyframe is the sharpest of the frames since the
   last keyframe, taken once the view has moved on enough (cosine similarity of the small copies under --change), so
   neighbouring keyframes overlap a lot. At most --keyframes, spread over the whole video.
2. VGGT puts all keyframes together in one pass (cameras and depth), each cropped to its middle square
   (worker.square_up: VGGT reads a tall picture far worse).
3. Points that two other keyframes confirm (same point, same depth within 3%; worker.agreeing) start the splat:
   OUT/untrained.splat, the training-free kind the room map shows.
4. With gsplat installed (iridis: gsplat-env), the splat is trained against the keyframes for --steps steps
   (L1 + D-SSIM, gsplat's default densify and prune): OUT/trained.splat, which looks like the pictures.
5. Labels (--labels): OWLv2 proposes boxes for the names in each keyframe, CLIP checks each box against the names and
   their usual look-alikes, and what both agree on is placed in 3D: OUT/labels.json, name tags on the 3D page.
   --labels-only OUT labels a finished map again in about a minute (no VGGT, no training).

Coordinates are VGGT's: the first keyframe's camera at the origin, x right, y down, z ahead (viewer.html ?src=...).
OUT/keyframes/ holds the pictures used, OUT/summary.json the numbers.
"""
import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE / "live")]
import recon_change as rc  # noqa: E402
from worker import agreeing, pick_keyframes, square_up  # noqa: E402

SIDE = 518  # VGGT's input size


def keyframes(source, most, change):
    """[(frame number, picture)] (see worker.pick_keyframes) and how many frames there were. source: a video file, or a
    folder of frames (a recording, relay POST /recording) taken in name order."""
    source = Path(source)
    if source.is_dir():
        paths = sorted(source.glob("*.jpg"))
        frames = ((i, cv2.imread(str(f))) for i, f in enumerate(paths))
        return pick_keyframes(frames, most, change), len(paths)
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise SystemExit(f"Cannot read {source}")
    count = [0]

    def frames():
        while True:
            ok, frame = cap.read()
            if not ok:
                return
            yield count[0], frame
            count[0] += 1

    picked = pick_keyframes(frames(), most, change)
    cap.release()
    return picked, count[0]


LABELS = "crisp packet, 3D printer, laptop, bottle, cup, backpack, monitor, chair"  # --labels: things to find and name
LABEL_SCORE = 0.3  # the detector (OWLv2, items.py: the vision transformer the workers use) proposes boxes from this score
# A second opinion on every box: CLIP (ViT-L/14, in the workers' model cache) is shown two crops around it and shares its
# belief between the names (what each looks like: LABEL_TEXTS) and the things most often mistaken for them (NOT_LABELS).
# A box keeps the detector's name when CLIP gives that name LABEL_OWN of its belief, or LABEL_TOP and no other class more.
LABEL_JUDGE = os.environ.get("RECON_LABEL_JUDGE", "openai/clip-vit-large-patch14")
LABEL_TEXTS = {
    "crisp packet": ["a crisp packet", "a bag of crisps", "a bag of potato chips", "a snack packet"],
    "3D printer": ["a 3D printer", "a desktop 3D printer", "a 3D printing machine", "a 3D printer with spools of filament"],
    "laptop": ["a laptop", "an open laptop computer"],
    "bottle": ["a bottle", "a plastic bottle", "a glass bottle", "a water bottle"],
    "cup": ["a cup", "a mug", "a coffee cup", "a paper cup"],
    "backpack": ["a backpack", "a rucksack"],
    "monitor": ["a computer monitor", "a computer screen on a stand"],
    "chair": ["a chair", "an office chair", "a folding chair", "a chair at a table"],
}
NOT_LABELS = ["a person", "a person's legs", "a hand", "a jacket", "clothes", "a bin bag", "a rubbish bin", "a recycling bin",
              "a cardboard box", "a plastic box", "cables", "a table", "a desk", "the floor", "a wall", "a shelf", "a door",
              "a window", "a speaker", "a drink can", "a carton", "a plastic bag", "a paper bag", "a computer keyboard",
              "a phone", "a lamp", "a tool", "a spool of filament", "a packet of wet wipes", "papers", "a blurry photo",
              "something else"]
LABEL_OWN = float(os.environ.get("RECON_LABEL_OWN", "0.35"))
LABEL_TOP = float(os.environ.get("RECON_LABEL_TOP", "0.15"))
LABEL_ONCE = (0.5, 0.6)  # a thing only one keyframe saw stays only from this detector score and this belief of CLIP's
# The detector's score a box of each name needs (others: LABEL_SCORE). Chairs score low and are real; monitors and
# backpacks at those scores were mostly something else.
LABEL_MIN = {"chair": 0.35, "3D printer": 0.4, "laptop": 0.4, "crisp packet": 0.35, "monitor": 0.45, "bottle": 0.35,
             "cup": 0.35, "backpack": 0.45}
# Each name's size in metres, the longest side a box shows of it: (smallest, typical, largest), and how close the
# sightings of one of them can be (radius). The typical sizes also give the map its scale: VGGT's units are not metres.
LABEL_SIZE = {
    "crisp packet": (0.08, 0.25, 0.5, 0.15),
    "3D printer": (0.25, 0.5, 1.0, 0.5),
    "laptop": (0.2, 0.35, 0.6, 0.4),
    "bottle": (0.1, 0.25, 0.45, 0.15),
    "cup": (0.05, 0.1, 0.2, 0.1),
    "backpack": (0.25, 0.45, 0.7, 0.4),
    "monitor": (0.3, 0.55, 0.9, 0.4),
    "chair": (0.4, 0.85, 1.3, 0.6),
}


class LabelJudge:
    """CLIP as a zero-shot classifier of boxes: judge(picture, boxes) -> its belief in each of self.classes per box."""

    def __init__(self, names, device="cuda"):
        import items
        import torch
        from transformers import AutoTokenizer, CLIPModel

        self.torch, self.device = torch, device
        self.model = CLIPModel.from_pretrained(LABEL_JUDGE, cache_dir=items.CACHE).to(device).eval()
        tokenizer = AutoTokenizer.from_pretrained(LABEL_JUDGE, cache_dir=items.CACHE)
        self.classes = list(names) + NOT_LABELS
        groups = [LABEL_TEXTS.get(n, [f"a {n}"]) for n in names] + [[t] for t in NOT_LABELS]
        flat = [f"a photo of {t}" for g in groups for t in g]
        with torch.no_grad():
            words = self.model.get_text_features(**tokenizer(flat, padding=True, return_tensors="pt").to(device)).float()
        words = words / words.norm(dim=-1, keepdim=True)
        at, means = 0, []
        for g in groups:  # each class: the mean of its texts
            means.append(words[at:at + len(g)].mean(0))
            at += len(g)
        means = torch.stack(means)
        self.words = means / means.norm(dim=-1, keepdim=True)
        self.side, self.scale = int(self.model.config.vision_config.image_size), float(self.model.logit_scale.exp())

    def __call__(self, picture, boxes):
        """An array (boxes, classes), each row summing to 1, averaged over two crops: the thing, and the thing with
        what is around it."""
        import items
        torch = self.torch
        if not boxes:
            return np.zeros((0, len(self.classes)))
        crops = []
        for box in boxes:
            for zoom in (1.3, 2.2):
                x, y, side = items.crop_around(picture.shape, box, zoom, least=32)
                crop = cv2.resize(picture[y:y + side, x:x + side], (self.side, self.side), interpolation=cv2.INTER_CUBIC)
                crops.append((cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32) / 255 - items.MEAN) / items.STD)
        with torch.no_grad():
            seen = self.model.get_image_features(pixel_values=torch.from_numpy(np.stack(crops)).permute(0, 3, 1, 2).float().to(self.device)).float()
            seen = seen / seen.norm(dim=-1, keepdim=True)
            return (self.scale * seen @ self.words.T).softmax(-1).reshape(len(boxes), 2, -1).mean(1).cpu().numpy()


def agrees(box):
    """Does CLIP agree with the detector's name for this box (judge: its belief in that name; judge_top: its first choice)?"""
    return box["judge"] >= LABEL_OWN or (box["judge_top"] == box["name"] and box["judge"] >= LABEL_TOP)


def box_depth(depth, box):
    """The depth of the thing in a box (0..1) on a depth map that is 0 where not trusted: the near side (30th percentile)
    of the box's middle half, which is the thing rather than what shows through it (a folding chair); when none of
    that is trusted, of the whole box, or of the box twice as big (what it stands on). None when none of it is."""
    h, w = depth.shape
    u0, v0, u1, v1 = box
    cu, cv = (u0 + u1) / 2, (v0 + v1) / 2
    for grow in (0.25, 0.5, 1.0):
        x0, x1 = max(0, cu - grow * (u1 - u0)), min(1, cu + grow * (u1 - u0))
        y0, y1 = max(0, cv - grow * (v1 - v0)), min(1, cv + grow * (v1 - v0))
        window = depth[int(y0 * h):max(int(y0 * h) + 1, int(y1 * h)), int(x0 * w):max(int(x0 * w) + 1, int(x1 * w))]
        if (window > 0).any():
            return float(np.percentile(window[window > 0], 30))
    return None


def sighting(box, depth, K, E, size):
    """Where a box (0..1) of one keyframe is in 3D: {"depth", "position" (the splat's axes), "extent" (its longest side,
    in the same units)}, or {} without a trusted depth. K for a picture of size (W, H), E world to camera."""
    d = box_depth(depth, box)
    if d is None:
        return {}
    W, H = size
    u0, v0, u1, v1 = box
    cu, cv = (u0 + u1) / 2, (v0 + v1) / 2
    cam = np.array([(cu * W - K[0, 2]) / K[0, 0] * d, (cv * H - K[1, 2]) / K[1, 1] * d, d])
    return {"depth": round(d, 4), "position": [round(float(x), 4) for x in (cam - E[:, 3]) @ E[:, :3]],
            "extent": round(max((u1 - u0) * W / K[0, 0], (v1 - v0) * H / K[1, 1]) * d, 4)}


def at_edge(box, margin=0.01):
    """Is a box cut by the picture's edge (so it shows only part of the thing)?"""
    return min(box[0], box[1]) < margin or max(box[2], box[3]) > 1 - margin


def cluster(points, frames, radius):
    """Sightings of one name as groups, one per thing: the two closest groups merge while their middles (medians) are
    within radius, and, unless they are within half of it (two boxes of one keyframe on one thing: a chair, and the
    chair with its table), no keyframe saw both: one keyframe shows two things of a name as two boxes."""
    groups = [[i] for i in range(len(points))]
    middles = [np.asarray(points[i], float) for i in range(len(points))]
    seen = [{frames[i]} for i in range(len(points))]
    while len(groups) > 1:
        m = np.stack(middles)
        apart = np.linalg.norm(m[:, None] - m[None], axis=-1)
        np.fill_diagonal(apart, np.inf)
        for a in range(len(groups)):
            for b in range(a + 1, len(groups)):
                if radius / 2 <= apart[a, b] < radius and seen[a] & seen[b]:
                    apart[a, b] = apart[b, a] = np.inf
        a, b = np.unravel_index(int(np.argmin(apart)), apart.shape)
        if apart[a, b] >= radius:
            break
        a, b = min(a, b), max(a, b)
        groups[a] += groups.pop(b)
        seen[a] |= seen.pop(b)
        middles.pop(b)
        middles[a] = np.median(np.asarray(points, float)[groups[a]], axis=0)
    return groups


def place_labels(every, log):
    """From every box (detector and CLIP numbers, and sighting()) to the things: the boxes that pass (LABEL_MIN, agrees,
    a trusted depth, a size that fits the name once the map has a scale), grouped per name in 3D (cluster: within the
    name's LABEL_SIZE radius), each group one thing. A thing needs two keyframes, or LABEL_ONCE. CPU only.
    Returns (things, left out with why, metres per map unit or None)."""
    kept, dropped = [], []
    for b in every:
        why = ("score" if b["score"] < LABEL_MIN.get(b["name"], LABEL_SCORE) else "judge" if not agrees(b)
               else "no depth" if "position" not in b else None)
        if why:
            dropped.append({**b, "why": why})
        else:
            kept.append(dict(b))
    ratios = [LABEL_SIZE[b["name"]][1] / b["extent"] for b in kept if b["name"] in LABEL_SIZE and b["extent"] > 0 and not at_edge(b["box"])]
    scale = float(np.median(ratios)) if len(ratios) >= 5 else None
    if scale:
        fits = []
        for b in kept:
            if b["name"] in LABEL_SIZE:
                small, _, large, _ = LABEL_SIZE[b["name"]]
                b["size_m"] = round(b["extent"] * scale, 3)
                if b["size_m"] > 1.5 * large or (b["size_m"] < 0.5 * small and not at_edge(b["box"])):
                    dropped.append({**b, "why": "size"})
                    continue
            fits.append(b)
        kept = fits
    reach = 0.15 * float(np.median([b["depth"] for b in kept])) if kept else 0.15
    things = []
    for name in dict.fromkeys(b["name"] for b in kept):
        mine = [b for b in kept if b["name"] == name]
        radius = LABEL_SIZE[name][3] / scale if scale and name in LABEL_SIZE else reach
        for group in cluster([b["position"] for b in mine], [b["keyframe"] for b in mine], radius):
            seen = [mine[i] for i in group]
            best = max(seen, key=lambda b: b["score"] + b["judge"])
            frames = sorted({b["keyframe"] for b in seen})
            judge = float(np.mean([b["judge"] for b in seen]))
            thing = {"name": name, "position": [round(float(x), 4) for x in np.median([b["position"] for b in seen], axis=0)],
                     "views": len(frames), "keyframes": frames, "score": best["score"], "judge": round(judge, 3),
                     # how sure: CLIP's mean belief, less for few keyframes (1 keyframe: half, 2: three quarters, ...)
                     "confidence": round(judge * (1 - 0.5 ** len(frames)), 3), "keyframe": best["keyframe"], "box": best["box"]}
            if "size_m" in best:
                thing["size_m"] = round(float(np.median([b["size_m"] for b in seen])), 3)
            if len(frames) >= 2 or (best["score"] >= LABEL_ONCE[0] and best["judge"] >= LABEL_ONCE[1]):
                things.append(thing)
            else:
                dropped.append({**thing, "why": "one view"})
    return things, dropped, scale


def write_labels(out, phrases, every, log):
    """place_labels on every box, written as OUT/labels.json: "objects" (the things: name, position in the splat's
    axes, views, keyframes, confidence, size_m), "boxes" (every box) and "dropped" (what was left out, with why)."""
    things, dropped, scale = place_labels(every, log)
    depths = [b["depth"] for b in every if "depth" in b]
    (out / "labels.json").write_text(json.dumps({
        "names": phrases, "score": LABEL_SCORE, "min": LABEL_MIN, "judge": LABEL_JUDGE, "own": LABEL_OWN, "top": LABEL_TOP,
        "once": LABEL_ONCE, "metres_per_unit": round(scale, 4) if scale else None,
        "median_depth_m": round(scale * float(np.median(depths)), 2) if scale and depths else None,
        "objects": things, "boxes": every, "dropped": dropped}))
    counts, why = {}, {}
    for m in things:
        counts[m["name"]] = counts.get(m["name"], 0) + 1
    for d in dropped:
        why[d["why"]] = why.get(d["why"], 0) + 1
    log(f"labels: {len(things)} things ({', '.join(f'{n} {c}' for n, c in counts.items()) or 'none'}) from {len(every)} boxes"
        f"{f', {scale:.2f} m per map unit' if scale else ''}; left out {', '.join(f'{c} ({k})' for k, c in why.items()) or 'none'}")
    return things


def label_objects(depth, K, E, size, pictures, out, names, log, score=LABEL_SCORE):
    """The named things in the keyframes, placed in 3D: OUT/labels.json (write_labels). pictures: each keyframe's middle
    square at full size (worker.square_up), as VGGT saw it; depth (S, h, w) at any size, 0 where not trusted; K for a
    picture of size (W, H); E world to camera. For each keyframe the detector proposes boxes (one name per box: the
    best scored), CLIP gives its belief in each box's name (LabelJudge), and the box is placed in 3D (sighting).
    None without the models' packages."""
    phrases = [n.strip() for n in names.split(",") if n.strip()]
    try:
        import items
        detector = items.load(device="cuda")
        judge = LabelJudge(phrases)
    except Exception as err:  # noqa: BLE001  the splat does not need labels
        log(f"no labels: {type(err).__name__}: {err}"[:200])
        return None
    every = []
    for q, picture in enumerate(pictures):
        boxes = items.merge(detector.detect(picture, phrases, floor=score), phrases, limit=score, phrases=lambda n: [n])
        belief = judge(picture, [b["box"] for b in boxes])
        for b, row in zip(boxes, belief):
            top = int(row.argmax())
            every.append({"name": b["name"], "score": b["score"], "judge": round(float(row[judge.classes.index(b["name"])]), 3),
                          "judge_top": judge.classes[top], "judge_top_belief": round(float(row[top]), 3), "keyframe": q,
                          "box": b["box"], **sighting(b["box"], depth[q], K[q], E[q], size)})
    del detector, judge
    return write_labels(out, phrases, every, log)


def load_views(out):
    """A finished map's cameras and trusted depth (views.json, depths.bin): depth (S, h, w), K, E, picture size."""
    v = json.loads((out / "views.json").read_text())
    w, h = v["depth_size"]
    return np.fromfile(out / v["depths"], np.float16).reshape(-1, h, w).astype(np.float32), np.array(v["K"]), np.array(v["E"]), v["size"]


def place_again(out, log):
    """--labels-place OUT: place OUT/labels.json's boxes again (new sizes, radii or minimums) without any model: CPU
    only, a second or two."""
    out = Path(out)
    depth, K, E, size = load_views(out)
    found = json.loads((out / "labels.json").read_text())
    every = [{k: b[k] for k in ("name", "score", "judge", "judge_top", "judge_top_belief", "keyframe", "box") if k in b} for b in found["boxes"]]
    for b in every:
        b.update(sighting(b["box"], depth[b["keyframe"]], K[b["keyframe"]], E[b["keyframe"]], size))
    write_labels(out, found["names"], every, log)


def relabel(out, source, names, log):
    """--labels-only OUT [SOURCE]: label a finished map again (other names, other rules) without VGGT or training: the
    cameras and depth from OUT/views.json and depths.bin, the keyframes at full size from SOURCE (the frames folder or
    video it was made from; summary.json keyframe_numbers), or else OUT/keyframes at VGGT's size."""
    out = Path(out)
    depth, K, E, size = load_views(out)
    summary = json.loads((out / "summary.json").read_text())
    numbers = summary.get("keyframe_numbers")
    if source and numbers and Path(source).is_dir():
        paths = sorted(Path(source).glob("*.jpg"))
        pictures = [square_up(cv2.imread(str(paths[n]))) for n in numbers]
    elif source and numbers:
        cap = cv2.VideoCapture(str(source))
        pictures = []
        for n in numbers:
            cap.set(cv2.CAP_PROP_POS_FRAMES, n)
            pictures.append(square_up(cap.read()[1]))
        cap.release()
    else:
        pictures = [cv2.imread(str(out / "keyframes" / f"{q:03d}.jpg")) for q in range(len(K))]
    log(f"{len(pictures)} keyframes from {'the source' if source and numbers else 'OUT/keyframes'}")
    objects = label_objects(depth, K, E, size, pictures, out, names, log)
    summary["labels"] = None if objects is None else len(objects)
    (out / "summary.json").write_text(json.dumps(summary, indent=1))


DEPTH_STEP = 4  # the depth of each keyframe is exported at a quarter of its size: 130 x 130 points for 518 x 518


def export_views(r, out, keep):
    """Each keyframe as a surface for the 3D page's playback: views.json (cameras, sizes) and depths.bin (float16,
    keyframe after keyframe, DEPTH_STEP times smaller than the picture, 0 where the depth is not trusted: depth edges and
    pixels no other keyframe confirms). The page lays the keyframe's own photo on its surface, so from where it was taken
    it is exactly as sharp as the photo; between keyframes it fades from one to the next."""
    S, H, W = r.depth.shape
    w, h = (W + DEPTH_STEP - 1) // DEPTH_STEP, (H + DEPTH_STEP - 1) // DEPTH_STEP
    depths = np.zeros((S, h, w), np.float16)
    for q in range(S):
        d = np.where(keep[q], r.depth[q], 0).astype(np.float32)
        depths[q] = cv2.resize(d, (w, h), interpolation=cv2.INTER_NEAREST)
    depths.tofile(out / "depths.bin")
    views = {"size": [W, H], "depth_size": [w, h], "K": np.round(r.K, 4).tolist(), "E": np.round(r.E, 6).tolist(),
             "files": [f"kf{q:03d}.jpg" for q in range(S)], "depths": "depths.bin", "median_depth": float(np.median(r.depth[keep])) if keep.any() else 1.0}
    (out / "views.json").write_text(json.dumps(views))
    return S


def ssim(a, b):
    """Structural similarity of two (1, 3, H, W) pictures, 11 px Gaussian window."""
    import torch
    import torch.nn.functional as F
    g = torch.exp(-((torch.arange(11, device=a.device) - 5.0) ** 2) / (2 * 1.5 ** 2))
    w = (g[:, None] * g[None, :])
    w = (w / w.sum()).expand(3, 1, 11, 11)
    mu_a, mu_b = F.conv2d(a, w, padding=5, groups=3), F.conv2d(b, w, padding=5, groups=3)
    va = F.conv2d(a * a, w, padding=5, groups=3) - mu_a ** 2
    vb = F.conv2d(b * b, w, padding=5, groups=3) - mu_b ** 2
    cov = F.conv2d(a * b, w, padding=5, groups=3) - mu_a * mu_b
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    return (((2 * mu_a * mu_b + c1) * (2 * cov + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (va + vb + c2))).mean()


def write_splat(path, pos, scale, rgb, alpha, quat):
    """The common .splat format (see splat.py): 32 bytes per Gaussian."""
    rec = np.zeros(len(pos), dtype=[("pos", "<f4", 3), ("scale", "<f4", 3), ("rgba", "u1", 4), ("rot", "u1", 4)])
    rec["pos"], rec["scale"] = pos, scale
    rec["rgba"] = np.concatenate([np.clip(rgb, 0, 255), np.clip(alpha, 0, 255)[:, None]], 1).astype(np.uint8)
    q = quat / (np.linalg.norm(quat, axis=1, keepdims=True) + 1e-12)
    rec["rot"] = np.clip(q * 128 + 128, 0, 255).astype(np.uint8)
    rec.tofile(path)
    return len(pos)


def start_points(r, keep, most=600_000):
    """The confirmed pixels as 3D points with colour and a size (the pixel's footprint at that depth)."""
    pts, cols, sizes = [], [], []
    S, H, W = r.depth.shape
    v, u = np.mgrid[0:H, 0:W]
    for q in range(S):
        m = keep[q]
        d = r.depth[q][m]
        K = r.K[q]
        cam = np.stack([(u[m] - K[0, 2]) / K[0, 0] * d, (v[m] - K[1, 2]) / K[1, 1] * d, d], 1)
        pts.append((cam - r.E[q][:, 3]) @ r.E[q][:, :3])
        cols.append(r.rgb[q][m])
        sizes.append(d / K[0, 0])
    pts, cols, sizes = np.concatenate(pts), np.concatenate(cols), np.concatenate(sizes)
    if len(pts) > most:
        pick = np.random.default_rng(0).choice(len(pts), most, replace=False)
        pts, cols, sizes = pts[pick], cols[pick], sizes[pick]
    return pts.astype(np.float32), cols.astype(np.float32), sizes.astype(np.float32)


def train(r, pts, cols, sizes, steps, out, log):
    """Optimise the splat against the keyframes with gsplat. Returns (Gaussians, seconds, PSNR on the keyframes)."""
    import torch
    from gsplat import rasterization
    from gsplat.strategy import DefaultStrategy

    dev = "cuda"
    S, H, W = r.depth.shape
    images = torch.tensor(r.rgb, device=dev, dtype=torch.float32) / 255.0  # (S, H, W, 3)
    viewmats = torch.eye(4, device=dev).repeat(S, 1, 1)
    viewmats[:, :3, :] = torch.tensor(r.E, device=dev, dtype=torch.float32)
    Ks = torch.tensor(r.K, device=dev, dtype=torch.float32)
    centres = -torch.einsum("sji,sj->si", viewmats[:, :3, :3], viewmats[:, :3, 3])
    scene_scale = float(torch.linalg.norm(centres - centres.mean(0), dim=1).max().clamp(min=float(np.median(r.depth)))) * 1.1
    n = len(pts)
    params = torch.nn.ParameterDict({
        "means": torch.nn.Parameter(torch.tensor(pts, device=dev)),
        "scales": torch.nn.Parameter(torch.log(torch.tensor(sizes, device=dev)).unsqueeze(1).repeat(1, 3)),
        "quats": torch.nn.Parameter(torch.tensor([[1.0, 0, 0, 0]], device=dev).repeat(n, 1)),
        "opacities": torch.nn.Parameter(torch.logit(torch.full((n,), 0.5, device=dev))),
        "sh0": torch.nn.Parameter(((torch.tensor(cols, device=dev) / 255.0 - 0.5) / 0.28209479).unsqueeze(1)),
    })
    rates = {"means": 1.6e-4 * scene_scale, "scales": 5e-3, "quats": 1e-3, "opacities": 5e-2, "sh0": 2.5e-3}
    optimizers = {k: torch.optim.Adam([{"params": params[k], "lr": rates[k], "name": k}], eps=1e-15) for k in params}
    strategy = DefaultStrategy(refine_start_iter=500, refine_stop_iter=int(steps * 0.7), reset_every=3000, refine_every=100,
                               verbose=False)
    strategy.check_sanity(params, optimizers)
    state = strategy.initialize_state(scene_scale=scene_scale)
    decay = 0.01 ** (1 / max(1, steps))  # the positions' learning rate falls to 1% over the run
    rng = np.random.default_rng(0)
    t0 = time.time()
    for step in range(steps):
        i = int(rng.integers(S))
        render, _, info = rasterization(params["means"], params["quats"], torch.exp(params["scales"]), torch.sigmoid(params["opacities"]),
                                        params["sh0"], viewmats[i:i + 1], Ks[i:i + 1], W, H, sh_degree=0, packed=False)
        strategy.step_pre_backward(params, optimizers, state, step, info)
        pred, true = render[0].permute(2, 0, 1)[None], images[i].permute(2, 0, 1)[None]
        loss = 0.8 * (pred - true).abs().mean() + 0.2 * (1 - ssim(pred, true))
        loss.backward()
        strategy.step_post_backward(params, optimizers, state, step, info, packed=False)
        for opt in optimizers.values():
            opt.step()
            opt.zero_grad(set_to_none=True)
        for group in optimizers["means"].param_groups:
            group["lr"] *= decay
        if step % 1000 == 0 or step == steps - 1:
            log(f"   step {step}: loss {float(loss):.4f}, {len(params['means'])} Gaussians, {time.time() - t0:.0f} s")
    with torch.no_grad():  # how close the trained splat comes to the keyframes
        def draw(view, K):
            render, _, _ = rasterization(params["means"], params["quats"], torch.exp(params["scales"]), torch.sigmoid(params["opacities"]),
                                         params["sh0"], view[None], K[None], W, H, sh_degree=0, packed=False)
            return render[0].clamp(0, 1)

        psnr = []
        for i in range(S):
            mse = float(((draw(viewmats[i], Ks[i]) - images[i]) ** 2).mean())
            psnr.append(10 * math.log10(1 / max(mse, 1e-10)))
        # For a person to judge: the splat drawn from three keyframes next to the photo, and from halfway between two
        # keyframes (no photo was taken there), in OUT/renders/.
        (out / "renders").mkdir(exist_ok=True)
        for i in sorted({0, S // 2, S - 1}):
            pair = torch.cat([draw(viewmats[i], Ks[i]), images[i]], 1).cpu().numpy()
            cv2.imwrite(str(out / "renders" / f"keyframe{i:03d}_splat_vs_photo.jpg"), cv2.cvtColor((pair * 255).astype(np.uint8), cv2.COLOR_RGB2BGR))
        if S > 1:
            a, b = S // 2 - 1 if S > 2 else 0, S // 2
            mid = viewmats[a].clone()
            mid[:3, 3] = (viewmats[a][:3, 3] + viewmats[b][:3, 3]) / 2
            u, _, vt = torch.linalg.svd(viewmats[a][:3, :3] + viewmats[b][:3, :3])  # the rotation halfway between
            mid[:3, :3] = u @ vt
            between = torch.cat([images[a], draw(mid, Ks[a]), images[b]], 1).cpu().numpy()
            cv2.imwrite(str(out / "renders" / f"between_{a:03d}_{b:03d}.jpg"), cv2.cvtColor((between * 255).astype(np.uint8), cv2.COLOR_RGB2BGR))
        rgb = (0.5 + 0.28209479 * params["sh0"][:, 0]).clamp(0, 1).cpu().numpy() * 255
        count = write_splat(out / "trained.splat", params["means"].cpu().numpy(), torch.exp(params["scales"]).cpu().numpy(), rgb,
                            torch.sigmoid(params["opacities"]).cpu().numpy() * 255, params["quats"].cpu().numpy())
    return count, round(time.time() - t0, 1), round(float(np.median(psnr)), 2)


def bird_view(out, side=1024, log=print):
    """The trained splat seen from straight above, its ceiling left out, as a map: OUT/birdseye.jpg, and
    OUT/birdseye.json to place things on it (viewer.html draws it in a corner with the labels, the recorded path and
    where the view is). Up is the cameras' mean up; the first keyframe looks up the picture. Kept: Gaussians between the
    floor (the lowest 2% of them) and a little above the cameras (30% of their height above the floor). For each pixel
    the highest of them shows; gaps between them are filled from their neighbours. CPU only, a few seconds."""
    out = Path(out)
    rec = np.fromfile(out / "trained.splat", dtype=[("pos", "<f4", 3), ("scale", "<f4", 3), ("rgba", "u1", 4), ("rot", "u1", 4)])
    E = np.load(out / "cameras.npz")["E"]
    R, t = E[:, :, :3], E[:, :, 3]
    centres = -np.einsum("nji,nj->ni", R, t)
    up = -np.einsum("nji,j->ni", R, np.array([0.0, 1.0, 0.0])).mean(0)
    up /= np.linalg.norm(up)
    ahead = R[0].T @ np.array([0.0, 0.0, 1.0])
    ahead -= up * (ahead @ up)
    ahead /= np.linalg.norm(ahead)
    right = np.cross(ahead, up)
    pos, rgb, alpha = rec["pos"].astype(np.float64), rec["rgba"][:, :3], rec["rgba"][:, 3]
    height = pos @ up
    solid = alpha > 60
    floor = float(np.percentile(height[solid], 2))
    eye = float(np.median(centres @ up))
    cut = eye + 0.3 * (eye - floor)
    keep = solid & (height <= cut) & (height >= floor - 0.1 * (eye - floor))
    x, y = pos[keep] @ right, pos[keep] @ ahead
    cx, cy = centres @ right, centres @ ahead
    x0, x1 = min(np.percentile(x, 0.5), cx.min()), max(np.percentile(x, 99.5), cx.max())
    y0, y1 = min(np.percentile(y, 0.5), cy.min()), max(np.percentile(y, 99.5), cy.max())
    pad = 0.03 * max(x1 - x0, y1 - y0)
    x0, x1, y0, y1 = x0 - pad, x1 + pad, y0 - pad, y1 + pad
    k = side / max(x1 - x0, y1 - y0)  # pixels per map unit
    W, H = int(np.ceil((x1 - x0) * k)), int(np.ceil((y1 - y0) * k))
    u, v = ((x - x0) * k).astype(np.int64), ((y1 - y) * k).astype(np.int64)
    inside = (u >= 0) & (u < W) & (v >= 0) & (v < H)
    u, v, h, colour = u[inside], v[inside], height[keep][inside], rgb[keep][inside]
    pixel = v * W + u
    order = np.lexsort((h, pixel))  # by pixel, then height: the last of each pixel is its highest
    last = np.r_[pixel[order][1:] != pixel[order][:-1], True]
    top = order[last]
    image = np.zeros((H * W, 3), np.float32)
    filled = np.zeros(H * W, np.float32)
    image[pixel[top]] = colour[top]
    filled[pixel[top]] = 1
    image, filled = image.reshape(H, W, 3), filled.reshape(H, W)
    picture = image.copy()
    for size in (3, 7, 15):  # gaps: the mean of the filled pixels around them
        near = cv2.blur(filled, (size, size))
        mean = cv2.blur(image, (size, size)) / np.maximum(near, 1e-6)[..., None]
        gap = (filled == 0) & (near > 0.05) & (picture.sum(-1) == 0)
        picture[gap] = mean[gap]
    picture[picture.sum(-1) == 0] = (10, 7, 5)  # nothing there: the page's background
    cv2.imwrite(str(out / "birdseye.jpg"), cv2.cvtColor(np.clip(picture, 0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR),
                [cv2.IMWRITE_JPEG_QUALITY, 88])
    metres = None
    if (out / "labels.json").exists():
        metres = json.loads((out / "labels.json").read_text()).get("metres_per_unit")
    path = [[round(float((a - x0) * k), 1), round(float((y1 - b) * k), 1)] for a, b in zip(cx, cy)]
    (out / "birdseye.json").write_text(json.dumps({
        "image": "birdseye.jpg", "size": [W, H], "right": right.round(6).tolist(), "ahead": ahead.round(6).tolist(),
        "up": up.round(6).tolist(), "x0": round(float(x0), 6), "y1": round(float(y1), 6), "px_per_unit": round(float(k), 4),
        "metres_per_unit": metres, "floor": round(floor, 5), "eye": round(eye, 5), "cut": round(cut, 5), "path": path}))
    log(f"bird's-eye view: {W} x {H} px from {int(keep.sum())} of {len(rec)} Gaussians (ceiling cut at {cut:.3f}) in {out / 'birdseye.jpg'}")
    return W, H


def _quat_to_matrix(q):
    w, x, y, z = q / np.linalg.norm(q)
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                     [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                     [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])


def _slerp(q0, q1, t):
    if q0 @ q1 < 0:
        q1 = -q1
    d = min(1.0, float(q0 @ q1))
    if d > 0.9995:
        q = q0 + t * (q1 - q0)
    else:
        a = math.acos(d)
        q = (math.sin((1 - t) * a) * q0 + math.sin(t * a) * q1) / math.sin(a)
    return q / np.linalg.norm(q)


def flythrough(out, between=6, fps=24, log=print):
    """The trained splat seen along the recorded path, as OUT/flythrough.mp4: from each keyframe's camera to the next
    in `between` steps (positions moved evenly, rotations turned evenly). Returns the number of frames written."""
    import splat as splat_file
    import torch
    from gsplat import rasterization

    out = Path(out)
    rec = np.fromfile(out / "trained.splat", dtype=[("pos", "<f4", 3), ("scale", "<f4", 3), ("rgba", "u1", 4), ("rot", "u1", 4)])
    cams = np.load(out / "cameras.npz")
    E, K = cams["E"], cams["K"]
    dev = "cuda"
    means, scales = torch.tensor(rec["pos"], device=dev), torch.tensor(rec["scale"], device=dev)
    quats = torch.tensor((rec["rot"].astype(np.float32) - 128) / 128, device=dev)
    quats = quats / quats.norm(dim=1, keepdim=True).clamp(min=1e-8)
    opacities = torch.tensor(rec["rgba"][:, 3].astype(np.float32) / 255, device=dev)
    colors = torch.tensor(rec["rgba"][:, :3].astype(np.float32) / 255, device=dev)
    side = int(round(2 * K[0][0, 2]))
    Kt = torch.tensor(K[0], device=dev, dtype=torch.float32)[None]
    rots = [splat_file.quaternion(e[:, :3]) for e in E]
    centres = [-e[:, :3].T @ e[:, 3] for e in E]
    poses = []
    for i in range(len(E) - 1):
        for s in range(between):
            t = s / between
            R = _quat_to_matrix(_slerp(rots[i], rots[i + 1], t))
            c = (1 - t) * centres[i] + t * centres[i + 1]
            poses.append((R, -R @ c))
    poses.append((E[-1][:, :3], E[-1][:, 3]))
    path = out / "flythrough.mp4"
    writer = None
    for code in ("avc1", "mp4v"):  # H.264 plays everywhere; OpenCV builds without it fall back to MPEG-4
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*code), fps, (side, side))
        if writer.isOpened():
            break
    with torch.no_grad():
        for R, tvec in poses:
            view = torch.eye(4, device=dev)
            view[:3, :3] = torch.tensor(R, device=dev, dtype=torch.float32)
            view[:3, 3] = torch.tensor(tvec, device=dev, dtype=torch.float32)
            render, _, _ = rasterization(means, quats, scales, opacities, colors, view[None], Kt, side, side, packed=False)
            frame = (render[0].clamp(0, 1).cpu().numpy() * 255).astype(np.uint8)
            writer.write(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
    writer.release()
    log(f"fly-through: {len(poses)} frames ({len(poses) / fps:.0f} s) along {len(E)} keyframes in {path}")
    return len(poses)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", nargs="?")
    ap.add_argument("out", nargs="?")
    ap.add_argument("--flythrough-only", metavar="OUT", help="only draw OUT/flythrough.mp4 from OUT/trained.splat and cameras.npz")
    ap.add_argument("--keyframes", type=int, default=96, help="the most keyframes in the one VGGT pass (96 fit an L4's 24 GB)")
    ap.add_argument("--change", type=float, default=0.9, help="a new keyframe once the view is less alike than this")
    ap.add_argument("--steps", type=int, default=7000)
    ap.add_argument("--no-train", action="store_true")
    ap.add_argument("--labels", default=LABELS, help="things to find and name in the keyframes, comma separated (empty: none)")
    ap.add_argument("--labels-only", metavar="OUT", help="only label OUT again (OUT/labels.json): give the frames or video it was made from as VIDEO for full-size pictures")
    ap.add_argument("--birdseye", metavar="OUT", help="only draw OUT/birdseye.jpg: the trained splat from above (CPU)")
    ap.add_argument("--labels-place", metavar="OUT", help="only place OUT/labels.json's boxes again (sizes, grouping): CPU, no model")
    args = ap.parse_intermixed_args()  # two optional positionals next to options: plain parse_args reads them differently by Python version
    if args.flythrough_only:
        flythrough(args.flythrough_only)
        return
    if args.birdseye:
        bird_view(args.birdseye)
        return
    if args.labels_place:
        place_again(Path(args.labels_place), lambda text: print(text, flush=True))
        return
    if args.labels_only:
        relabel(args.labels_only, args.video, args.labels, lambda text: print(text, flush=True))
        return
    if not args.video or not args.out:
        ap.error("give VIDEO OUT (or --flythrough-only OUT)")
    out = Path(args.out)
    (out / "keyframes").mkdir(parents=True, exist_ok=True)
    summary = {"video": Path(args.video).name}

    def log(text):
        print(text, flush=True)

    t0 = time.time()
    picked, total = keyframes(args.video, args.keyframes, args.change)
    frames = [cv2.resize(square_up(f), (SIDE, SIDE), interpolation=cv2.INTER_AREA) for _, f in picked]
    for k, f in enumerate(frames):
        cv2.imwrite(str(out / "keyframes" / f"{k:03d}.jpg"), f, [cv2.IMWRITE_JPEG_QUALITY, 92])
    summary.update(frames=total, keyframes=len(frames), keyframe_numbers=[n for n, _ in picked], select_seconds=round(time.time() - t0, 1))
    log(f"{total} frames read, {len(frames)} keyframes in {summary['select_seconds']} s")
    if len(frames) < 3:
        raise SystemExit("Too few keyframes: record longer, and move.")

    import torch
    model = rc.load_model(device="cuda")
    torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    r = rc.reconstruct(model, frames, long_side=SIDE)
    summary.update(vggt_seconds=round(time.time() - t0, 1), vggt_peak_gb=round(torch.cuda.max_memory_allocated() / 1e9, 2),
                   fov_deg=round(math.degrees(2 * math.atan(r.depth.shape[1] / 2 / float(r.K[0][1, 1]))), 1))  # vertical, keyframe 0
    del model
    torch.cuda.empty_cache()
    np.savez(out / "cameras.npz", K=r.K, E=r.E, keyframes=np.array([n for n, _ in picked]))  # world to camera, as VGGT gives them
    valid = rc.valid_mask(r, 35.0)
    agree = agreeing(r, valid)
    keep = valid & (agree >= 2)
    if keep.mean() < 0.03:
        keep = valid & (agree >= 1)
    pts, cols, sizes = start_points(r, keep)
    # the page's playback surfaces: depth edges out, but no need for two other keyframes to confirm a pixel of its own photo
    summary["views"] = export_views(r, out, valid & (agree >= 1) if (valid & (agree >= 1)).mean() > 0.05 else valid)
    if args.labels.strip():
        labelled = label_objects(r.depth, r.K, r.E, (r.depth.shape[2], r.depth.shape[1]), [square_up(f) for _, f in picked], out, args.labels, log)
        summary["labels"] = None if labelled is None else len(labelled)
        torch.cuda.empty_cache()
    quat = np.tile([1.0, 0, 0, 0], (len(pts), 1))
    summary["untrained_gaussians"] = write_splat(out / "untrained.splat", pts, np.repeat(sizes[:, None] * 0.75, 3, 1), cols,
                                                 np.full(len(pts), 255.0), quat)
    summary.update(kept_share=round(float(keep.mean()), 3), median_depth=round(float(np.median(r.depth[keep])), 3) if keep.any() else None)
    log(f"VGGT {summary['vggt_seconds']} s, {summary['vggt_peak_gb']} GB; {100 * keep.mean():.0f}% of pixels confirmed; "
        f"untrained splat {summary['untrained_gaussians']} Gaussians")
    if not args.no_train:
        try:
            summary["trained_gaussians"], summary["train_seconds"], summary["psnr_median"] = train(r, pts, cols, sizes, args.steps, out, log)
            log(f"trained splat: {summary['trained_gaussians']} Gaussians in {summary['train_seconds']} s, PSNR {summary['psnr_median']} dB")
            try:
                summary["flythrough_frames"] = flythrough(out, log=log)
            except Exception as err:  # noqa: BLE001  the splat itself is done
                log(f"no fly-through: {type(err).__name__}: {err}")
            try:
                summary["birdseye"] = bird_view(out, log=log)
            except Exception as err:  # noqa: BLE001
                log(f"no bird's-eye view: {type(err).__name__}: {err}")
        except ImportError as err:
            summary["trained"] = f"not trained: {err} (gsplat is not installed here)"
            log(summary["trained"])
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    log("done")


if __name__ == "__main__":
    main()
