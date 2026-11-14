#!/usr/bin/env python3
"""Check that the Rust code in recon_rust.py (JUN-28) gives the NumPy answers.

Runs the synthetic scenes of test_synthetic.py both ways and compares:
  1. depth_gap and votes, value by value, on every frame pair the detector uses
  2. unproject, patch_table, same_place_similarity and has_lookalike, with 256-number features
  3. the full report (verdict, clusters, thresholds) and the flagged pixels

NumPy's matrix product may round the last bit of a coordinate differently from the Rust loop,
so a point lying exactly on a pixel boundary or a tolerance can, very rarely, land on the other
side. Steps 1 and 2 report how many values land on another pixel or count differently (float32
rounding of the gap itself is ignored); step 3 must match exactly.

    python test_rust.py
"""
import sys

import numpy as np

import recon_change as rc
import recon_rust
import test_synthetic as ts


class numpy_only:
    """Inside this block recon_change runs its NumPy code."""

    def __enter__(self):
        self.saved, recon_rust._rs = recon_rust._rs, None

    def __exit__(self, *exc):
        recon_rust._rs = self.saved


def both(fn, *args, **kwargs):
    with numpy_only():
        slow = fn(*args, **kwargs)
    return slow, fn(*args, **kwargs)


def wide_features(r, dim=256, seed=5):
    """The synthetic scenes' 3-number patch features spread over `dim` numbers with noise, stored
    as float16: the size and type of the real features (recon_change.reduce_features)."""
    rng = np.random.default_rng(seed)
    f = r.feat @ rng.standard_normal((r.feat.shape[-1], dim)).astype(np.float32)
    f += 0.3 * rng.standard_normal(f.shape).astype(np.float32)
    return rc.Recon(r.depth, r.conf, r.K, r.E, r.rgb, f.astype(np.float16))


def differ(a, b):
    """Values that differ by more than float32 rounding: another pixel, another trust answer, another count."""
    a, b = np.asarray(a), np.asarray(b)
    if a.dtype.kind == "f":
        both_nan = np.isnan(a) & np.isnan(b)
        close = np.abs(a - b) <= 1e-5 * np.maximum(1.0, np.abs(a))
        return int((~(both_nan | close | (a == b))).sum())
    return int((a != b).sum())


def main():
    if not recon_rust.enabled():
        sys.exit("recon_fast is not built: pip install ./rust (see rust/README.md)")
    failures = []

    def check(ok, what):
        print(("  ok   " if ok else "  FAIL ") + what)
        if not ok:
            failures.append(what)

    scenes = {
        "box moved": (ts.scene(ts.BOX_B), {}),
        "nothing moved": (ts.scene(ts.BOX_A), {}),
        "box rebuilt 20 cm off, look-alike check on": (ts.scene(ts.BOX_A + np.array([0.2, 0.0, 0.0]), features=True), {"reach": 0.15}),
        "box moved, look-alike check on": (ts.scene(ts.BOX_B, features=True), {"reach": 0.15}),
        "small box moved, 3% noise": (ts.scene(ts.BOX_B, box_size=0.06, noise=0.03, seed=7), {}),
        "box moved, 256-number features": (wide_features(ts.scene(ts.BOX_B, features=True)), {"reach": 0.15}),
        "box rebuilt 20 cm off, 256-number features": (wide_features(ts.scene(ts.BOX_A + np.array([0.2, 0.0, 0.0]), features=True)), {"reach": 0.15}),
        "box moved, 256-number features, look-alike reach from the clips": (wide_features(ts.scene(ts.BOX_B, features=True)), {}),
    }

    print("1. depth_gap and votes, value by value")
    r = scenes["box moved"][0]
    valid = rc.valid_mask(r, 0)
    near = rc.nearest_in_window(r.depth, rc.WINDOW)
    total = diffs = vote_total = vote_diffs = 0
    rng = np.random.default_rng(0)
    for q in range(len(r.depth)):
        pts = rc.unproject(r.depth[q], r.K[q], r.E[q])[valid[q]]
        # add points behind the cameras, far outside the image and right at the origin
        pts = np.concatenate([pts, rng.normal(0, 20, (2000, 3)).astype(np.float32), np.zeros((1, 3), np.float32)])
        for j in range(0, len(r.depth), 5):
            slow, fast = both(rc.depth_gap, pts, r.E[j], r.K[j], r.depth[j], near[j], valid[j])
            total += 2 * len(pts)
            diffs += differ(slow[0], fast[0]) + differ(slow[1], fast[1])
        frames = [j for j in range(len(r.depth)) if j != q]
        slow, fast = both(rc.votes, r, valid, near, pts, frames, 0.031, 0.062)
        vote_total += 3 * len(pts)
        vote_diffs += sum(differ(s, f) for s, f in zip(slow, fast))
    print(f"  depth_gap: {diffs} of {total:,} values differ by more than float32 rounding ({diffs / total:.1e})")
    print(f"  votes:     {vote_diffs} of {vote_total:,} counts differ ({vote_diffs / vote_total:.1e})")
    check(diffs / total < 1e-4, "depth_gap: fewer than 1 in 10,000 values land on another pixel")
    check(vote_diffs / vote_total < 1e-4, "votes: fewer than 1 in 10,000 counts differ")

    print("2. unproject, patch_table, same_place_similarity and has_lookalike")
    r = scenes["box moved, 256-number features"][0]
    unit = r.feat.astype(np.float32)
    r = rc.Recon(r.depth, r.conf, r.K, r.E, r.rgb, unit / (np.linalg.norm(unit, axis=-1, keepdims=True) + 1e-6))
    valid = rc.valid_mask(r, 0)
    slow, fast = both(rc.unproject, r.depth[3], r.K[3], r.E[3])
    check(slow.shape == fast.shape and differ(slow, fast) == 0, "unproject: same points to float32 rounding")
    after, ref = list(range(24, 36)), list(range(0, 24, 2))
    ta_slow, ta_fast = both(rc.patch_table, r, valid, after)
    tb_slow, tb_fast = both(rc.patch_table, r, valid, ref)
    same = all(differ(x, y) == 0 and x.shape == y.shape for x, y in zip(ta_slow + tb_slow, ta_fast + tb_fast))
    check(same, f"patch_table: same {len(ta_fast[0]) + len(tb_fast[0]):,} patches, positions and features")
    slow, fast = both(rc.same_place_similarity, r, ta_slow, tb_slow)
    check(slow == fast, f"same_place_similarity: same value ({fast})")
    hits = total = 0
    for q in after:
        pix = np.flatnonzero(valid[q])[::7]
        pts = rc.unproject(r.depth[q], r.K[q], r.E[q]).reshape(-1, 3)[pix]
        for reach, min_sim in ((0.05, 0.6), (0.15, 0.85)):
            slow, fast = both(rc.has_lookalike, r, q, pix, pts, tb_slow, reach, min_sim, 2)
            hits += differ(slow, fast)
            total += len(pix)
    print(f"  has_lookalike: {hits} of {total:,} answers differ")
    check(hits / total < 1e-4, "has_lookalike: fewer than 1 in 10,000 answers differ")

    print("3. full reports and flagged pixels")
    for name, (recon, kwargs) in scenes.items():
        (rep_slow, det_slow), (rep_fast, det_fast) = both(rc.analyse, recon, 24, conf_pct=0, typical_m=2.5, **kwargs)
        same_pixels = all(np.array_equal(det_slow[k][f], det_fast[k][f]) for k in ("appeared", "gone") for f in ("pixels", "frames"))
        check(rep_slow == rep_fast and same_pixels, f"{name}: same report and same flagged pixels ({rep_fast['verdict'].split(':')[0]})")

    print("\nAll checks passed." if not failures else f"\n{len(failures)} check(s) failed.")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
