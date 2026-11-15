#!/usr/bin/env python3
"""Watched spots: has this one marked place changed?

A carer draws a box in a baseline photo (around the pill box, the key hook, the hearing-aid
tray). Each check then answers one question per spot: is it as it was ("same"), is it different
("changed"), or did no new frame get a clear enough look ("not_seen")?

The baseline frames and the new frames have gone through the model in one pass
(recon_change.reconstruct), so they share one 3D frame. For every spot:

  1. Its 3D region is the main surface inside the box, from the baseline frame's depth.
  2. Every baseline frame with a clear look at that region is a reference view of the spot. A
     new frame is compared with the reference that looks from the most similar direction.
  3. For each new frame: where does the region land, how many pixels does it cover there, is
     something much nearer standing in front of it, and is the direction within MAX_ANGLE of
     the reference's? Frames without such a clear look are skipped.
  4. The new frame is laid over the reference picture of the spot through the 3D points, nudged
     a few pixels until the two line up best, and compared: colour (after removing the
     brightness and colour offset of the surroundings) and the model's patch features
     (Recon.feat).
  5. Only what the spot differs by beyond its surroundings (the ring around the box) counts: a
     change of lighting or a small pose error hits the ring as well and cancels. For the patch
     features the same comparison between baseline frames, where nothing changed, is
     subtracted too.

The score is a weighted sum of the two, scaled so that 1.0 is the limit: on the PASLCD benchmark
about 5% of unchanged spots score above 1 (eval/WATCH_RESULTS.md). Higher = more changed.

`photos` can hold larger pictures of the same frames (the originals). The geometry still comes
from the model's resolution, but the colour comparison is then made on the extra pixels.
"""
import cv2
import numpy as np

import recon_change as rc

PAD, PAD_MIN = 0.5, 6.0  # the ring: the box grown by this share of its longer side, at least this many pixels
SEARCH = 5.0  # pixels at the model's resolution: how far the new view may be nudged to line up
MAX_SIDE = 448  # a compared patch is never larger than this, so big spots are compared at a coarser level
SURFACE, SURFACE_BOXES = 0.10, 1.5  # the spot's surface: within this share of the distance, or this many box sizes, of the box's median depth
BLOCK = 0.08  # a new surface this much nearer (fraction of the distance) than the spot, or more, is in front of it
MIN_INSIDE, MIN_CLEAR = 0.6, 0.5  # share of the spot that must land in the frame / must not be blocked
MIN_AREA, MIN_SHRINK = 36.0, 0.2  # pixels the spot must cover in a new frame / at least this share of its baseline area
DEPTH_TOL = 0.04  # depth disagreement (fraction of the distance) that counts as "the surface is elsewhere"
# The score. Two kinds of evidence carry it (chosen on five PASLCD scenes and reported on the other
# five, eval/WATCH_RESULTS.md):
#   colour   the colour difference over the worst quarter of the spot, minus the ring's
#   feature  the patch-feature difference in the spot, minus the ring's, minus the same between baseline frames
# Fine structure and depth are measured as well (compare) but added nothing, so they carry no weight.
PAIRS = {"colour": ("c_top", "c_ring"), "feature": ("f_spot", "f_ring"), "structure": ("z_spot", "z_ring"), "depth": ("g_spot", "g_ring")}
FORMS = {"colour": "ring", "feature": "both", "structure": "both", "depth": "both"}  # see surplus()
WEIGHTS = {"colour": 0.1733, "feature": 10.21}  # per Lab unit, per unit of (1 - cosine similarity)
SCALE = 2.0922  # puts the limit at 1.0: about 5% of unchanged spots score higher
# Degrees between the reference view and a new view of the spot. Beyond this the look does not count:
# on the five scenes used for choosing, false alarms were 1 to 6% below 10 degrees and 17% or more above.
MAX_ANGLE = 10.0
THRESHOLD = 1.0
KEYS = ("c_spot", "c_ring", "c_top", "a_spot", "a_ring", "a_top", "z_spot", "z_ring", "f_spot", "f_ring", "g_spot", "g_ring", "g_far", "g_near")


class Scene:
    """What is worked out once per reconstruction."""

    def __init__(self, r, photos=None, max_scale=None):
        self.r = r
        self.H, self.W = r.depth.shape[1:]
        self.feat = None
        if r.feat is not None:
            f = r.feat.astype(np.float32)
            self.feat = f / (np.linalg.norm(f, axis=-1, keepdims=True) + 1e-6)
        self.photos = photos
        self.max_scale = max_scale
        self.centre = [-(E[:, :3].T @ E[:, 3]) for E in r.E]
        self._images, self._points = {}, {}

    def native(self, i):
        """How many times larger than the model's picture the stored photo of frame i is."""
        if self.photos is None or self.photos[i] is None:
            return 1.0
        s = self.photos[i].shape[1] / self.W
        return min(s, self.max_scale) if self.max_scale else s

    def image(self, i, scale):
        """RGB picture of frame i at `scale` times the model's resolution (1 = what the model saw)."""
        if scale <= 1.0 or self.photos is None or self.photos[i] is None:
            return self.r.rgb[i]
        key = (i, round(scale, 3))
        if key not in self._images:
            photo = self.photos[i]
            size = (int(round(self.W * scale)), int(round(self.H * scale)))
            self._images[key] = photo if size[0] >= photo.shape[1] else cv2.resize(photo, size, interpolation=cv2.INTER_AREA)
        return self._images[key]

    def points(self, i):
        if i not in self._points:
            self._points[i] = rc.unproject(self.r.depth[i], self.r.K[i], self.r.E[i])
        return self._points[i]


# ----------------------------------------------------------------------------- geometry
def spot_geometry(scene, spot):
    """The spot in its baseline frame: the box in pixels, the ring around it, its 3D points."""
    H, W = scene.H, scene.W
    b = int(spot["frame"])
    u0, v0, u1, v1 = [float(x) for x in spot["box"]]
    x0, x1 = sorted((np.clip(u0, 0, 1) * W, np.clip(u1, 0, 1) * W))  # pixel i covers [i, i + 1)
    y0, y1 = sorted((np.clip(v0, 0, 1) * H, np.clip(v1, 0, 1) * H))
    if x1 - x0 < 2:
        x0, x1 = max(0.0, (x0 + x1) / 2 - 1), min(float(W), (x0 + x1) / 2 + 1)
    if y1 - y0 < 2:
        y0, y1 = max(0.0, (y0 + y1) / 2 - 1), min(float(H), (y0 + y1) / 2 + 1)
    pad = max(PAD_MIN, PAD * max(x1 - x0, y1 - y0))
    px0, py0 = int(max(0, np.floor(x0 - pad))), int(max(0, np.floor(y0 - pad)))
    px1, py1 = int(min(W, np.ceil(x1 + pad))), int(min(H, np.ceil(y1 + pad)))
    xs = np.arange(int(np.ceil(x0 - 0.5)), int(np.floor(x1 - 0.5)) + 1)
    ys = np.arange(int(np.ceil(y0 - 0.5)), int(np.floor(y1 - 0.5)) + 1)
    xs, ys = xs[(xs >= 0) & (xs < W)], ys[(ys >= 0) & (ys < H)]
    gx, gy = np.meshgrid(xs, ys)
    depth = scene.r.depth[b][gy, gx]
    extent = max(x1 - x0, y1 - y0) / float(scene.r.K[b][0, 0])  # the box's size as a fraction of the distance
    # The spot is the main surface in the box. What shows past it (the floor behind a table edge,
    # the wall behind a hook) is somewhere else in 3D and lands somewhere else in another view.
    near, far = 0.0, np.inf
    if (depth > 0).any():
        middle = float(np.median(depth[depth > 0]))
        reach = max(SURFACE, SURFACE_BOXES * extent) * middle
        near, far = middle - reach, middle + reach
    on = (depth > near) & (depth < far) & (depth > 0)
    pts = scene.points(b)[gy, gx][on]
    if len(pts) > 600:
        pts = pts[np.linspace(0, len(pts) - 1, 600).round().astype(int)]
    return {"frame": b, "box": (x0, y0, x1, y1), "padded": (px0, py0, px1, py1), "points": pts, "depth": (near, far),
            "area": (x1 - x0) * (y1 - y0), "extent": extent, "centroid": pts.mean(0) if len(pts) else None}


def block_margin(geo):
    """How much nearer a new surface must be to count as standing in front of the spot rather than
    being a change of the spot itself: more than the spot's own size, and at least BLOCK."""
    return max(BLOCK, geo["extent"])


def view(scene, geo, j, max_angle=None):
    """How well frame j sees the spot. Returns None if the spot has no 3D points, else a dict:
    inside (share of the region that lands in the picture), box (where, 0..1), area (pixels it
    covers), blocked (share with something much nearer in front), angle (degrees between this
    viewing direction and the baseline frame's), quality, and why it is not a clear look (None
    when it is one). With max_angle, a look from a more different direction is not a clear one."""
    pts = geo["points"]
    if not len(pts):
        return None
    r, H, W = scene.r, scene.H, scene.W
    K, E = r.K[j], r.E[j]
    cam = pts @ E[:, :3].T + E[:, 3]
    z = cam[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        u = K[0, 0] * cam[:, 0] / z + K[0, 2] + 0.5  # + 0.5: pixel i covers [i, i + 1)
        v = K[1, 1] * cam[:, 1] / z + K[1, 2] + 0.5
    inside = (z > 1e-6) & (u >= 0) & (u < W) & (v >= 0) & (v < H)
    out = {"frame": int(j), "inside": float(inside.mean()), "box": None, "area": 0.0, "blocked": 0.0, "angle": None, "quality": 0.0}
    if out["inside"] < MIN_INSIDE:
        return {**out, "why": "out of view"}
    ui, vi = u[inside], v[inside]
    lo_u, hi_u = np.percentile(ui, [2, 98])
    lo_v, hi_v = np.percentile(vi, [2, 98])
    out["box"] = [round(float(lo_u / W), 4), round(float(lo_v / H), 4), round(float(hi_u / W), 4), round(float(hi_v / H), 4)]
    out["area"] = float((hi_u - lo_u + 1) * (hi_v - lo_v + 1))
    seen = r.depth[j][np.clip(vi.astype(np.int64), 0, H - 1), np.clip(ui.astype(np.int64), 0, W - 1)]
    gap = (seen - z[inside]) / z[inside]
    out["blocked"] = float(((gap < -block_margin(geo)) & (seen > 0)).mean())
    to_here = geo["centroid"] - scene.centre[j]
    to_base = geo["centroid"] - scene.centre[geo["frame"]]
    cos = float(to_here @ to_base / (np.linalg.norm(to_here) * np.linalg.norm(to_base) + 1e-9))
    out["angle"] = float(np.degrees(np.arccos(np.clip(cos, -1, 1))))  # between the two viewing directions
    out["quality"] = out["inside"] * (1 - out["blocked"]) * min(1.0, out["area"] / geo["area"]) ** 0.5 * max(cos, 0.0)
    if out["area"] < MIN_AREA or out["area"] < MIN_SHRINK * geo["area"]:
        return {**out, "why": "too small in view"}
    if 1 - out["blocked"] < MIN_CLEAR:
        return {**out, "why": "blocked"}
    if max_angle is not None and out["angle"] > max_angle:
        return {**out, "why": "seen from another side"}
    return {**out, "why": None}


def landing(scene, b, j, gx, gy):
    """Where sample positions gx, gy of baseline frame b (pixel i covers [i, i + 1)) land in frame
    j: u, v in the same convention, the depth z of each 3D point in camera j, and its depth d in
    the baseline frame (0 = none). The depth is the baseline pixel's, not interpolated, so a
    depth edge stays an edge."""
    r, H, W = scene.r, scene.H, scene.W
    d = r.depth[b][np.clip(np.floor(gy), 0, H - 1).astype(np.int64), np.clip(np.floor(gx), 0, W - 1).astype(np.int64)]
    K, E = r.K[b], r.E[b]
    cam = np.stack([(gx - 0.5 - K[0, 2]) / K[0, 0] * d, (gy - 0.5 - K[1, 2]) / K[1, 1] * d, d], -1)
    world = (cam - E[:, 3]) @ E[:, :3]
    K, E = r.K[j], r.E[j]
    cam = world @ E[:, :3].T + E[:, 3]
    z = cam[..., 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        u = K[0, 0] * cam[..., 0] / z + K[0, 2] + 0.5
        v = K[1, 1] * cam[..., 1] / z + K[1, 2] + 0.5
    return u, v, z, d


# ----------------------------------------------------------------------------- comparison
def sample(img, x, y):
    """Picture values at fractional positions (pixel i covers [i, i + 1)); 0 outside."""
    x, y = np.nan_to_num(x - 0.5).astype(np.float32), np.nan_to_num(y - 0.5).astype(np.float32)
    return cv2.remap(img, x, y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def to_lab(rgb):
    """Lightly smoothed Lab colour. Lightness counts half: shadows change it the most."""
    lab = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2LAB).astype(np.float32)
    return cv2.GaussianBlur(lab, (0, 0), 1.0) * np.float32([0.5, 1.0, 1.0])


def feature_at(fmap, x, y, H, W):
    """Unit patch features (h, w, C) at pixel positions x, y of the H x W picture, interpolated."""
    h, w = fmap.shape[:2]
    fx = np.clip(x * w / W - 0.5, 0, w - 1.001)
    fy = np.clip(y * h / H - 0.5, 0, h - 1.001)
    x0, y0 = np.floor(fx).astype(np.int64), np.floor(fy).astype(np.int64)
    ax, ay = (fx - x0)[..., None].astype(np.float32), (fy - y0)[..., None].astype(np.float32)
    f = ((1 - ax) * (1 - ay) * fmap[y0, x0] + ax * (1 - ay) * fmap[y0, x0 + 1]
         + (1 - ax) * ay * fmap[y0 + 1, x0] + ax * ay * fmap[y0 + 1, x0 + 1])
    return f / (np.linalg.norm(f, axis=-1, keepdims=True) + 1e-6)


def tolerant_difference(a, b, reach):
    """Per pixel: how far a's colour is from every colour b shows within `reach` pixels, and the
    other way round (the larger of the two). A residual misalignment of a pixel or two costs
    nothing; a colour or a detail that the other picture does not have nearby does."""
    kernel = np.ones((2 * reach + 1, 2 * reach + 1), np.uint8)
    out = None
    for p, q in ((a, b), (b, a)):
        lo, hi = cv2.erode(q, kernel), cv2.dilate(q, kernel)
        d = np.linalg.norm(np.maximum(np.maximum(lo - p, p - hi), 0), axis=-1)
        out = d if out is None else np.maximum(out, d)
    return out


def gain_matched(a, b, around):
    """b with a gain and an offset per channel that make its ring look like a's ring. The gain
    is the ratio of the two spreads (robust to a small misalignment), used only when the ring
    has contrast and the two rings go up and down together; otherwise it is 1."""
    out = np.empty_like(b)
    for c in range(b.shape[-1]):
        x, y = b[..., c][around], a[..., c][around]
        gain = 1.0
        if x.std() > 3.0 and y.std() > 3.0 and np.corrcoef(x, y)[0, 1] > 0.6:
            gain = float(np.clip(y.std() / x.std(), 0.6, 1.7))
        out[..., c] = gain * b[..., c] + np.median(y - gain * x)
    return out


def mean_of(x, mask):
    return float(x[mask].mean()) if mask.any() else float("nan")


def compare(scene, geo, j, scale=None, keep=False):
    """Lay frame j over the spot as its baseline frame shows it, and measure how much they differ
    inside the box (spot) and in the ring around it. Returns a dict:
      seen               share of the spot that frame j shows unblocked
      c_spot, c_ring     colour difference (Lab units) after removing the ring's offset
      c_top              the same over the worst quarter of the spot (a change that fills part of the box)
      a_spot, a_ring, a_top   the three again after matching a brightness gain as well (see gain_matched)
      z_spot, z_ring     1 - correlation of the brightness pattern (0 = same pattern)
      f_spot, f_ring     1 - cosine similarity of the patch features (None without features)
      g_spot, g_ring     median depth disagreement, as a fraction of the distance
      g_far, g_near      share of the spot where frame j sees farther / nearer than the baseline surface
      scale, shift       resolution used (1 = the model's) and the nudge in model pixels
    keep=True adds "patches": the two RGB patches as compared and the masks of spot and ring."""
    r, H, W = scene.r, scene.H, scene.W
    b = geo["frame"]
    x0, y0, x1, y1 = geo["box"]
    px0, py0, px1, py1 = geo["padded"]
    top = min(scene.native(b), scene.native(j))
    if scale is not None:
        top = min(top, scale)
    fit = min(top, MAX_SIDE / max(px1 - px0, py1 - py0))
    s = max([1.0] + [lv for lv in (2.0, 4.0, top) if lv <= fit])  # 1, 2, 4 or the photo's own size
    e = int(round(SEARCH * s))
    nx, ny = int(round((px1 - px0) * s)), int(round((py1 - py0) * s))
    gx, gy = np.meshgrid(px0 + (np.arange(-e, nx + e) + 0.5) / s, py0 + (np.arange(-e, ny + e) + 0.5) / s)
    u, v, z, d = landing(scene, b, j, gx, gy)
    has = d > 0
    inside = has & (z > 1e-6) & (u >= 0) & (u < W) & (v >= 0) & (v < H)
    img_b, img_j = scene.image(b, s), scene.image(j, s)
    core = (slice(e, e + ny), slice(e, e + nx))
    a_rgb = sample(img_b, gx[core] * img_b.shape[1] / W, gy[core] * img_b.shape[0] / H)
    b_rgb = sample(img_j, u * img_j.shape[1] / W, v * img_j.shape[0] / H)
    A, B_all = to_lab(a_rgb), to_lab(b_rgb)
    # Nudge: the shift of the whole patch (spot and ring together) at which the patterns match best.
    match = cv2.matchTemplate(B_all, A, cv2.TM_CCOEFF_NORMED)
    dy, dx = np.mgrid[-e:e + 1, -e:e + 1]
    match = np.nan_to_num(match, nan=-1.0, posinf=-1.0, neginf=-1.0) - 0.05 * np.hypot(dx, dy) / max(e, 1)  # ties go to the smaller nudge
    best = np.unravel_index(int(np.argmax(match)), match.shape)
    moved = (slice(best[0], best[0] + ny), slice(best[1], best[1] + nx))
    B, u, v, z, inside = B_all[moved], u[moved], v[moved], z[moved], inside[moved]
    with np.errstate(divide="ignore", invalid="ignore"):
        seen = r.depth[j][np.clip(np.floor(np.nan_to_num(v)), 0, H - 1).astype(np.int64),
                          np.clip(np.floor(np.nan_to_num(u)), 0, W - 1).astype(np.int64)]
        gap = np.where(inside & (seen > 0), (seen - z) / z, np.nan)
    blocked = np.nan_to_num(gap) < -block_margin(geo)
    ok = inside & has[core] & ~blocked
    in_box = (gx[core] >= x0) & (gx[core] < x1) & (gy[core] >= y0) & (gy[core] < y1)
    spot = in_box & (d[core] > geo["depth"][0]) & (d[core] < geo["depth"][1])  # the spot's own surface
    inner, ring = ok & spot, ok & ~in_box
    out = {"frame": int(j), "scale": round(float(s), 2), "shift": [round((best[1] - e) / s, 2), round((best[0] - e) / s, 2)],
           "seen": float(inner.sum() / max(spot.sum(), 1))}
    if keep:
        out["patches"] = {"a": a_rgb, "b": b_rgb[moved], "spot": spot, "ok": ok}
    if inner.sum() < 4:
        return {**out, **{k: None for k in KEYS}}
    # Colour: remove what the surroundings differ by (exposure, white balance, a lamp switched on).
    around = ring if ring.sum() >= 12 else ok
    reach = max(1, int(round(0.75 * s)))
    # The same again with a gain as well as an offset (a_...): a lamp or the sun scales the
    # brightness, which an offset alone only removes where the spot is as bright as its ring.
    for prefix, other in (("c", B + np.median((A - B)[around], axis=0)), ("a", gain_matched(A, B, around))):
        diff = tolerant_difference(A, other, reach)
        out[prefix + "_spot"], out[prefix + "_ring"] = mean_of(diff, inner), mean_of(diff, ring)
        worst = diff[inner]
        out[prefix + "_top"] = float(np.sort(worst)[-max(1, len(worst) // 4):].mean())
    B = B + np.median((A - B)[around], axis=0)
    for key, mask in (("z_spot", inner), ("z_ring", ring)):
        if mask.sum() < 4:
            out[key] = float("nan")
            continue
        a, c = A[..., 0][mask], B[..., 0][mask]
        a, c = a - a.mean(), c - c.mean()
        out[key] = float(1 - (a * c).mean() / (a.std() * c.std() + 4.0))  # + 4: a flat patch has no pattern to compare
    # Patch features, at the model's resolution.
    out["f_spot"] = out["f_ring"] = None
    if scene.feat is not None:
        step = max(1, int(np.ceil(max(nx, ny) / 32)))
        part = (slice(None, None, step), slice(None, None, step))
        fa = feature_at(scene.feat[b], gx[core][part], gy[core][part], H, W)
        fb = feature_at(scene.feat[j], np.nan_to_num(u[part]), np.nan_to_num(v[part]), H, W)
        apart = 1 - (fa * fb).sum(-1)
        out["f_spot"], out["f_ring"] = mean_of(apart, inner[part]), mean_of(apart, ring[part])
    # Depth: does frame j see the surface where the baseline has it?
    known = np.isfinite(gap)
    for key, mask in (("g_spot", spot & known), ("g_ring", ~in_box & known)):
        out[key] = float(np.median(np.abs(gap[mask]))) if mask.any() else float("nan")
    inner_known = spot & known
    out["g_far"] = float((gap[inner_known] > DEPTH_TOL).mean()) if inner_known.any() else float("nan")
    out["g_near"] = float((gap[inner_known] < -DEPTH_TOL).mean()) if inner_known.any() else float("nan")
    return out


def typical(rows):
    """Median of each measurement over several comparisons (None where none has it)."""
    out = {}
    for k in KEYS:
        vals = [row[k] for row in rows if row.get(k) is not None and np.isfinite(row[k])]
        out[k] = float(np.median(vals)) if vals else None
    return out


def surplus(new, own, a, b, form="both"):
    """One kind of evidence from a new-frame comparison and the baseline's own (typical).
    a and b name the measurement inside the spot and in the ring. Forms:
      plain  the difference inside the spot
      ring   minus the difference in the ring (what a lighting change or a pose error does to both)
      own    minus what baseline frames differ by on the spot
      both   (spot - ring) in the new frame minus (spot - ring) between baseline frames
    Missing measurements count as zero."""
    def val(d, k):
        return 0.0 if d is None or d.get(k) is None or not np.isfinite(d[k]) else d[k]
    if form == "plain":
        return val(new, a)
    if form == "ring":
        return val(new, a) - val(new, b)
    if form == "own":
        return val(new, a) - val(own, a)
    return (val(new, a) - val(new, b)) - (val(own, a) - val(own, b))


def evidence(new, own):
    """The kinds of evidence for one new frame, each in its form (FORMS)."""
    return {kind: surplus(new, own, a, b, FORMS[kind]) for kind, (a, b) in PAIRS.items()}


def score_of(new, own, weights=None, scale=None):
    ev = evidence(new, own)
    weights = WEIGHTS if weights is None else weights
    return sum(weights[k] * ev[k] for k in weights) / (SCALE if scale is None else scale)


def clear_views(scene, geo, frames, max_angle=None):
    """The frames with a clear look at the spot, best first, and the looks that were not clear."""
    looks = [view(scene, geo, j, max_angle) for j in frames if j != geo["frame"]]
    looks = [v for v in looks if v is not None]
    good = sorted((v for v in looks if v["why"] is None), key=lambda v: -v["quality"])
    return good, [v for v in looks if v["why"] is not None]


def baseline_disagreement(scene, geo, base, own=3, scale=None):
    """How much the other baseline frames differ from this one on the spot (nothing changed
    between them): the median over the `own` best views."""
    good, _ = clear_views(scene, geo, base)
    rows = [compare(scene, geo, v["frame"], scale) for v in good[:own]]
    return typical([row for row in rows if row["c_spot"] is not None])


def references(scene, geo, base):
    """The spot as every baseline frame with a clear look shows it: {frame: geometry}. The marked
    frame is one of them; in the others the box is where the spot's 3D points land. A new frame
    is compared with the reference that looks at the spot from the most similar direction, so a
    spot marked from the left can still be checked from the right."""
    refs = {geo["frame"]: geo}
    for b in base:
        if b == geo["frame"]:
            continue
        v = view(scene, geo, b)
        if v is None or v["why"] is not None:
            continue
        other = spot_geometry(scene, {"frame": b, "box": v["box"]})
        if len(other["points"]):
            refs[b] = other
    return refs


def looks_at(scene, refs, frames, max_angle=None):
    """For each of the frames: the reference with the most similar viewing direction, and how
    well the frame sees the spot from there. Returns the clear looks, best first, as
    (view, reference geometry), and the looks that were not clear."""
    max_angle = MAX_ANGLE if max_angle is None else max_angle
    good, poor = [], []
    for j in frames:
        def cos(g):
            a, c = g["centroid"] - scene.centre[j], g["centroid"] - scene.centre[g["frame"]]
            return float(a @ c / (np.linalg.norm(a) * np.linalg.norm(c) + 1e-9))
        ref = max(refs.values(), key=cos)
        v = view(scene, ref, j, max_angle)
        if v is not None:
            (good if v["why"] is None else poor).append((v, ref))
    return sorted(good, key=lambda x: -x[0]["quality"]), poor


def why_not_seen(poor):
    """The most telling reason: a frame that had the spot in view but blocked, tiny or from
    another side, else out of view."""
    reasons = [v["why"] for v, _ in sorted(poor, key=lambda x: -x[0]["inside"])]
    return next((w for w in reasons if w != "out of view"), "out of view")


# ----------------------------------------------------------------------------- the check
def check(r, base, new, spots, photos=None, threshold=THRESHOLD, views=3, own=3, max_scale=None):
    """r holds baseline frames (indices `base`) and new frames (indices `new`).
    spots: [{"name", "frame" (index into r of a baseline frame), "box": [u0, v0, u1, v1]}], 0..1.
    photos: optional larger RGB pictures of the same frames, indexed like r (None entries allowed).
    Returns one dict per spot:
      name, state ("same" | "changed" | "not_seen"),
      score   how much more the spot differs from the baseline than unchanged spots do: the
              limit is 1.0, higher = more changed, None when not seen. With several clear new
              frames it is the median of the best `views`.
      frame   the new frame with the best look, box_new where the spot lands in it (0..1),
      why     one short phrase,
      frame_base, box_base   the baseline frame that look was compared with (the one that sees
              the spot from the most similar direction) and the spot's box in it."""
    scene = Scene(r, photos, max_scale)
    out = []
    for spot in spots:
        geo = spot_geometry(scene, spot)
        answer = {"name": spot["name"], "state": "not_seen", "score": None, "frame": None, "box_new": None,
                  "why": "no depth at the spot", "frame_base": None, "box_base": None}
        out.append(answer)
        if not len(geo["points"]):
            continue
        good, poor = looks_at(scene, references(scene, geo, base), new)
        if not good:
            answer["why"] = why_not_seen(poor) if poor else "out of view"
            continue
        rows = [(v, ref, compare(scene, ref, v["frame"])) for v, ref in good[:views]]
        rows = [(v, ref, row) for v, ref, row in rows if row["c_spot"] is not None and row["seen"] >= MIN_CLEAR]
        if not rows:
            answer["why"] = "blocked"
            continue
        usual = {}  # the baseline's own disagreement, once per reference frame
        scores = []
        for v, ref, row in rows:
            if ref["frame"] not in usual:
                usual[ref["frame"]] = baseline_disagreement(scene, ref, base, own)
            scores.append(score_of(row, usual[ref["frame"]]))
        best_view, best_ref, best_row = rows[0]
        bx0, by0, bx1, by1 = best_ref["box"]
        score = float(np.median(scores))
        moved = max(best_row["g_far"] or 0.0, best_row["g_near"] or 0.0) > 0.5
        answer.update(state="changed" if score > threshold else "same", score=round(score, 3), frame=best_view["frame"],
                      box_new=best_view["box"], frame_base=best_ref["frame"],
                      box_base=[round(bx0 / scene.W, 4), round(by0 / scene.H, 4), round(bx1 / scene.W, 4), round(by1 / scene.H, 4)],
                      why=("its surface moved or is gone" if moved else "looks different") if score > threshold else "looks the same")
    return out
