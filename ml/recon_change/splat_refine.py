#!/usr/bin/env python3
"""Iterative splat refinement: low detail where things move, high detail where nothing does.

splat.py builds the whole scene again from every clip, at one detail level. This keeps a map
instead and refines it a little with each new clip. The room is cut into cubes (cells) of a fixed
size. Each cell remembers what it looks like (one feature vector), how detailed its Gaussians are
(a level from LOW to HIGH), how many clips in a row it stayed the same, and whether it holds
something that moves.

For each new clip, every cell the clip sees is compared with what the cell looked like before,
by cosine similarity of the feature vectors:

    changed    (similarity below SIM_CHANGED): rebuilt at LOW detail and marked movable.
               Movable cells stay at MOVABLE_CAP while they keep changing (a chair, a cup).
    unchanged  one level more detail, unless the cell is already at its cap; then nothing is done.
               A static cell climbs to HIGH; a movable cell that stays put for SETTLE_STEPS clips
               counts as static again.
    new        a cell seen for the first time starts at LOW. If the map had looked straight through
               that spot before (it was empty), something arrived there, so it is movable too.
    emptied    a cell the map had, which this clip looks straight through: what was there has
               gone. Its Gaussians are dropped and it is marked movable (unless it only ever
               held a few stray points; those are just cleared).

Cells the clip does not see are left alone. So the work per clip is bounded by what changed or is
still refining: a room that stays the same stops costing anything once it reaches HIGH.

The feature vector of a cell is the mean of the image-patch features (Recon.feat, the DINOv2
features VGGT computes anyway, the same ones the look-alike check uses) over the cell's pixels.
Reconstructions saved without features fall back to a coarse colour histogram.

A detail level is a pixel stride: level L keeps one pixel in LEVEL_STRIDE[L] x LEVEL_STRIDE[L],
each a flat disc built as in splat.py, sized to its share of the surface.

The clip must be in the map's coordinates: the cell grid is fixed in space. The live worker
reconstructs the baseline frames and the new frames in one pass, so with the same baseline first
the coordinates repeat from clip to clip (see the PR for what is still open there).

    m = Map()
    report = step(m, recon)          # once per clip
    write_splat(m, "map.splat")
    m.save("map.npz"); m = Map.load("map.npz")
"""
from dataclasses import dataclass, field

import numpy as np

import recon_change as rc
from splat import quaternion

CELL = 0.25  # side of a cell, in scene units (VGGT scenes are a few units deep)
LEVEL_STRIDE = (16, 8, 4, 2)  # level -> keep one pixel in stride x stride; level 0 is LOW
LOW, HIGH = 0, len(LEVEL_STRIDE) - 1
MOVABLE_CAP = LOW  # movable cells never go above this
SETTLE_STEPS = 3  # unchanged clips in a row before a movable cell counts as static again
# A cell is "changed" when the cosine similarity of its feature to the stored one is below this.
# The look-alike check measures the same thing between views of unchanged surfaces and lands
# between 0.6 and 0.85 on real clips; 0.8 errs towards calling things changed.
SIM_CHANGED = 0.8
KEEP_OLD = 0.7  # an unchanged cell's stored feature: this much old, the rest new (follows slow light changes)
MIN_PIXELS = 20  # trusted pixels a cell needs in a clip to count as seen
# Pixels a cell needs before its arriving or leaving counts as something moving. Cells at the edge
# of a surface catch a few stray noisy points now and then; those come and go without meaning much.
SOLID_PIXELS = 100
MAX_FRAMES = 6  # frames of a clip used, evenly spread, as in splat.py
FREE_STRIDE = 8  # every 8th pixel each way casts a ray for "is this spot empty?"
FREE_MARGIN = 2.0  # a ray counts as passing through empty space up to this many cells before its surface
FREE_FRAMES = 2  # frames that must look through a cell before it counts as empty
RECORD = np.dtype([("pos", "<f4", 3), ("scale", "<f4", 3), ("rgba", "u1", 4), ("rot", "u1", 4)])  # the .splat layout


@dataclass
class Map:
    """The refined scene: per cell what it looks like and how detailed it is, and its Gaussians."""

    cell: float = CELL
    keys: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))  # cell ids, sorted
    feat: np.ndarray = None  # (cells, C) unit feature vectors; zero = emptied
    level: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int8))
    still: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int16))  # unchanged clips in a row
    movable: np.ndarray = field(default_factory=lambda: np.zeros(0, bool))
    changes: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int16))  # how often it changed
    pixels: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int32))  # trusted pixels when last seen
    free: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))  # cells seen to be empty, sorted
    splats: np.ndarray = field(default_factory=lambda: np.zeros(0, RECORD))
    owner: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))  # cell id of each Gaussian
    steps: int = 0

    def save(self, path):
        np.savez_compressed(path, cell=self.cell, keys=self.keys, feat=np.zeros((0, 0), np.float32) if self.feat is None else self.feat,
                            level=self.level, still=self.still, movable=self.movable, changes=self.changes, pixels=self.pixels, free=self.free,
                            splats=self.splats.view(np.uint8).reshape(-1, RECORD.itemsize), owner=self.owner, steps=self.steps)

    @classmethod
    def load(cls, path):
        z = np.load(path)
        return cls(float(z["cell"]), z["keys"], z["feat"] if z["feat"].size else None, z["level"], z["still"], z["movable"],
                   z["changes"], z["pixels"], z["free"], z["splats"].reshape(-1).view(RECORD), z["owner"], int(z["steps"]))


# ----------------------------------------------------------------------------- cells
OFFSET, SPAN = 1 << 20, 1 << 21  # cell indices from -2^20 to 2^20 each way, packed in one int64


def cell_ids(points, cell):
    """World points (N, 3) -> the id of the cell each lies in."""
    i = np.floor(points / cell).astype(np.int64) + OFFSET
    return (i[:, 0] * SPAN + i[:, 1]) * SPAN + i[:, 2]


def centres(ids, cell):
    """Cell ids -> world positions of their centres."""
    i = np.stack([ids // (SPAN * SPAN), ids // SPAN % SPAN, ids % SPAN], 1) - OFFSET
    return (i + 0.5) * cell


def unit(x):
    return x / (np.linalg.norm(x, axis=-1, keepdims=True) + 1e-6)


def pick_frames(r, frames):
    frames = list(range(len(r.depth))) if frames is None else list(frames)
    return [frames[i] for i in np.linspace(0, len(frames) - 1, min(MAX_FRAMES, len(frames))).round().astype(int)]


def observe(r, frames, valid, cell):
    """What this clip shows of each cell: ids (sorted), unit feature vectors, pixel counts, and
    per frame the id of every pixel's cell (-1 = untrusted)."""
    S, H, W = r.depth.shape
    if r.feat is not None:
        h, w = r.feat.shape[1:3]
        u, v = np.meshgrid(np.arange(W), np.arange(H))
        patch = np.minimum(v // (H // h), h - 1) * w + np.minimum(u // (W // w), w - 1)
    pix_ids, all_ids, descr = [], [], []
    for n, q in enumerate(frames):
        ids = np.full(H * W, -1, np.int64)
        ok = valid[q].reshape(-1)
        ids[ok] = cell_ids(rc.unproject(r.depth[q], r.K[q], r.E[q]).reshape(-1, 3)[ok], cell)
        pix_ids.append(ids.reshape(H, W))
        all_ids.append(ids[ok])
        if r.feat is not None:
            descr.append(n * h * w + patch.reshape(-1)[ok])  # which patch of which frame
        else:
            c = r.rgb[q].reshape(-1, 3)[ok] // 64
            descr.append(c[:, 0] * 16 + c[:, 1] * 4 + c[:, 2])  # 4 x 4 x 4 colour histogram bin
    all_ids, descr = np.concatenate(all_ids), np.concatenate(descr)
    keys, inv, count = np.unique(all_ids, return_inverse=True, return_counts=True)
    if r.feat is not None:  # sum patch features weighted by how many of the cell's pixels each patch has
        table = unit(np.concatenate([r.feat[q].reshape(h * w, -1).astype(np.float32) for q in frames]))
        pair, n_pair = np.unique(inv.ravel() * len(table) + descr, return_counts=True)
        feat = np.zeros((len(keys), table.shape[1]), np.float32)
        np.add.at(feat, pair // len(table), table[pair % len(table)] * n_pair[:, None])
    else:
        feat = np.bincount(inv.ravel() * 64 + descr, minlength=len(keys) * 64).reshape(len(keys), 64).astype(np.float32)
    seen = count >= MIN_PIXELS
    return keys[seen], unit(feat[seen]), count[seen], pix_ids


def empty_cells(r, frames, valid, cell):
    """Cells this clip looks straight through: sample every FREE_STRIDE-th trusted pixel's ray from
    the camera up to FREE_MARGIN cells before the surface it hits."""
    S, H, W = r.depth.shape
    u, v = np.meshgrid(np.arange(0, W, FREE_STRIDE), np.arange(0, H, FREE_STRIDE))
    hits = []
    for q in frames:
        d = r.depth[q][v, u]
        ok = valid[q][v, u] & (d > FREE_MARGIN * cell)
        K, R, t = r.K[q], r.E[q][:, :3], r.E[q][:, 3]
        rays = np.stack([(u[ok] - K[0, 2]) / K[0, 0], (v[ok] - K[1, 2]) / K[1, 1], np.ones(ok.sum())], 1) @ R  # world, per unit z-depth
        steps = np.arange(cell / 2, d[ok].max() if ok.any() else 0, cell / 2)
        along = steps[None] < (d[ok] - FREE_MARGIN * cell / np.linalg.norm(rays, axis=1))[:, None]  # margin as a distance
        pts = -R.T @ t + rays[:, None] * steps[None, :, None]
        hits.append(np.unique(cell_ids(pts[along], cell)))  # count each cell once per frame
    if not hits:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    return np.unique(np.concatenate(hits), return_counts=True)  # ids, and in how many frames


# ----------------------------------------------------------------------------- Gaussians
def build(r, frames, valid, pix_ids, wanted, level):
    """Gaussians for the cells in `wanted` (ids), each at its level (same order). Flat discs facing
    the camera, as splat.py makes them."""
    if not len(wanted):
        return np.zeros(0, RECORD), np.zeros(0, np.int64)
    order = np.argsort(wanted)
    wanted, stride_of = wanted[order], np.asarray(LEVEL_STRIDE)[level[order]]
    S, H, W = r.depth.shape
    u, v = np.meshgrid(np.arange(W), np.arange(H))
    out, owner = [], []
    for q, ids in zip(frames, pix_ids):
        at = np.clip(np.searchsorted(wanted, ids), 0, len(wanted) - 1)
        mine = (ids >= 0) & (wanted[at] == ids)
        stride = np.where(mine, stride_of[at], 1)
        # each frame takes a different offset of the stride grid, so frames fill each other's gaps
        pick = mine & ((u + q) % stride == 0) & ((v + 2 * q) % stride == 0)
        if not pick.any():
            continue
        depth, s = r.depth[q][pick], stride[pick]
        size = depth / r.K[q][0, 0] * s * 0.75 / np.sqrt(len(frames))  # its share of the surface, over the frames that see it
        rec = np.zeros(pick.sum(), RECORD)
        rec["pos"] = rc.unproject(r.depth[q], r.K[q], r.E[q])[pick]
        rec["scale"] = np.stack([size, size, size * 0.3], 1)
        rec["rgba"] = np.concatenate([r.rgb[q][pick], np.full((pick.sum(), 1), 235)], 1)
        rec["rot"] = np.clip(quaternion(r.E[q][:, :3].T) * 128 + 128, 0, 255).astype(np.uint8)
        out.append(rec)
        owner.append(ids[pick])
    if not out:
        return np.zeros(0, RECORD), np.zeros(0, np.int64)
    return np.concatenate(out), np.concatenate(owner)


# ----------------------------------------------------------------------------- one clip
def step(m, r, frames=None, valid=None, sim_changed=SIM_CHANGED, conf_pct=25.0):
    """Refine the map with one clip (a Recon in the map's coordinates). frames: which of its frames
    are the new clip (default all). Returns what was done, per cell and per detail level."""
    frames = pick_frames(r, frames)
    valid = rc.valid_mask(r, conf_pct) if valid is None else valid
    keys, feat, count, pix_ids = observe(r, frames, valid, m.cell)
    through, n_frames = empty_cells(r, frames, valid, m.cell)
    through = through[n_frames >= FREE_FRAMES]
    if m.feat is None:
        m.feat = np.zeros((0, feat.shape[1]), np.float32)
    if m.feat.shape[1] != feat.shape[1]:
        raise ValueError(f"this clip has {feat.shape[1]}-dimensional features, the map {m.feat.shape[1]}")

    # cells of the map this clip sees, and cells it sees for the first time
    at = np.searchsorted(m.keys, keys)
    known = (at < len(m.keys)) & (m.keys[np.minimum(at, len(m.keys) - 1)] == keys) if len(m.keys) else np.zeros(len(keys), bool)
    idx = at[known]
    sim = np.einsum("ij,ij->i", m.feat[idx], feat[known])
    changed = sim < sim_changed
    ch, same = idx[changed], idx[~changed]

    m.level[ch], m.still[ch], m.movable[ch] = LOW, 0, True
    m.changes[ch] += 1
    m.feat[ch] = feat[known][changed]
    m.pixels[idx] = count[known]
    m.still[same] += 1
    m.movable[same] &= m.still[same] < SETTLE_STEPS
    m.feat[same] = unit(KEEP_OLD * m.feat[same] + (1 - KEEP_OLD) * feat[known][~changed])
    cap = np.where(m.movable[same], MOVABLE_CAP, HIGH)
    refine = same[m.level[same] < cap]
    skipped = len(same) - len(refine)
    m.level[refine] += 1

    # cells the map had and this clip looks straight through: what was there is gone
    emptied = np.flatnonzero(np.isin(m.keys, through) & ~np.isin(m.keys, keys) & (m.feat.any(1)))
    solid = emptied[m.pixels[emptied] >= SOLID_PIXELS]  # stray points vanishing is not something moving
    m.level[emptied], m.still[emptied], m.feat[emptied], m.pixels[emptied] = LOW, 0, 0, 0
    m.movable[solid] = True
    m.changes[solid] += 1

    # new cells; ones in space the map had seen to be empty hold something that arrived
    new_keys, new_feat, new_count = keys[~known], feat[~known], count[~known]
    arrived = np.isin(new_keys, m.free) & (new_count >= SOLID_PIXELS)
    rebuild_keys = np.concatenate([m.keys[ch], m.keys[refine], new_keys])
    rebuild_level = np.concatenate([m.level[ch], m.level[refine], np.full(len(new_keys), LOW, np.int8)])

    # swap the Gaussians of rebuilt and emptied cells for new ones
    gone = np.isin(m.owner, np.concatenate([rebuild_keys, m.keys[emptied]]))
    splats, owner = build(r, frames, valid, pix_ids, rebuild_keys, rebuild_level)
    m.splats, m.owner = np.concatenate([m.splats[~gone], splats]), np.concatenate([m.owner[~gone], owner])

    # add the new cells, keeping the arrays sorted by id
    n = len(new_keys)
    m.keys = np.concatenate([m.keys, new_keys])
    m.feat = np.concatenate([m.feat, new_feat])
    m.level = np.concatenate([m.level, np.full(n, LOW, np.int8)])
    m.still = np.concatenate([m.still, np.zeros(n, np.int16)])
    m.movable = np.concatenate([m.movable, arrived])
    m.changes = np.concatenate([m.changes, arrived.astype(np.int16)])
    m.pixels = np.concatenate([m.pixels, new_count.astype(np.int32)])
    order = np.argsort(m.keys)
    m.keys, m.feat, m.level, m.still, m.movable, m.changes, m.pixels = (
        a[order] for a in (m.keys, m.feat, m.level, m.still, m.movable, m.changes, m.pixels))

    # empty space: what any clip looked through, minus anything with a surface in it now
    m.free = np.setdiff1d(np.union1d(m.free, through), keys)
    m.steps += 1
    return {
        "step": m.steps,
        "seen": len(keys), "changed": int(changed.sum()), "refined": len(refine), "skipped": skipped,
        "new": n, "arrived": int(arrived.sum()), "emptied": len(solid), "cleared": len(emptied) - len(solid), "unseen": len(m.keys) - len(keys) - len(emptied),
        "gaussians_built": len(splats), "gaussians": len(m.splats), "movable_cells": int(m.movable.sum()),
        "similarity_median": float(np.median(sim)) if len(sim) else None,
        "by_level": by_level(m),
    }


def by_level(m):
    """Per detail level: how many cells, and how many Gaussians they hold."""
    lvl = m.level[np.clip(np.searchsorted(m.keys, m.owner), 0, max(len(m.keys) - 1, 0))] if len(m.owner) else np.zeros(0, int)
    return {L: {"cells": int((m.level == L).sum()), "gaussians": int((lvl == L).sum())} for L in range(len(LEVEL_STRIDE))}


def write_splat(m, path, show_levels=False):
    """Write the map as a `.splat` file (the format splat.py writes). Returns how many Gaussians.
    show_levels tints movable cells orange and the rest from blue (LOW) to white (HIGH)."""
    rec = m.splats.copy()
    if show_levels and len(rec):
        at = np.clip(np.searchsorted(m.keys, m.owner), 0, len(m.keys) - 1)
        shade = (m.level[at] / max(HIGH, 1))[:, None]
        tint = np.where(m.movable[at][:, None], [255, 150, 40], (1 - shade) * np.array([60, 110, 255]) + shade * 255)
        rec["rgba"][:, :3] = (0.5 * rec["rgba"][:, :3] + 0.5 * tint).astype(np.uint8)
    with open(path, "wb") as f:
        f.write(rec.tobytes())
    return len(rec)
