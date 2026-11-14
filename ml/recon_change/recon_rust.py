"""Optional Rust versions of the hottest loops in recon_change.py (JUN-28).

Build once with  pip install ./rust  (needs a Rust toolchain; see rust/README.md). Without the
build, or with RECON_FAST=0, recon_change.py runs its NumPy code exactly as before.
RECON_FAST_OFF=unproject,nearest (any of the function names below) switches single ones off.

Rust is only used when every input is float32 (bool for the trust mask), which is what VGGT
gives us. Anything else goes to NumPy, because NumPy would compute in a different precision.
"""
import os

import numpy as np

try:
    if os.environ.get("RECON_FAST", "1") == "0":
        raise ImportError("switched off with RECON_FAST=0")
    import recon_fast as _rs
except ImportError:
    _rs = None
OFF = {name.strip() for name in os.environ.get("RECON_FAST_OFF", "").split(",") if name.strip()}


def enabled():
    return _rs is not None


def _ok(*arrays, mask=None):
    return all(a.dtype == np.float32 for a in arrays) and (mask is None or mask.dtype == np.bool_)


def _c(a):
    return np.ascontiguousarray(a)


def depth_gap(points, E, K, depth, near, valid):
    """recon_change.depth_gap in Rust, or None when Rust cannot take these inputs."""
    if _rs is None or "depth_gap" in OFF or not _ok(points, E, K, depth, near, mask=valid):
        return None
    return list(_rs.depth_gap(_c(points), _c(E), _c(K), _c(depth), _c(near), _c(valid)))


def votes(r, valid, near, pts, frames, tol_agree, tol_free):
    """recon_change.votes in Rust, or None when Rust cannot take these inputs."""
    if _rs is None or "votes" in OFF or not _ok(pts, r.E, r.K, r.depth, near, mask=valid):
        return None
    # NumPy compares a float32 array with a Python float in float32, so round the tolerances the same way.
    return _rs.votes(_c(pts), _c(r.E), _c(r.K), _c(r.depth), _c(near), _c(valid),
                     np.asarray(frames, np.int64).reshape(-1), float(np.float32(tol_agree)), float(np.float32(tol_free)))


def unproject(depth, K, E):
    """recon_change.unproject in Rust, or None when Rust cannot take these inputs."""
    if _rs is None or "unproject" in OFF or not _ok(depth, K, E):
        return None
    return _rs.unproject(_c(depth), _c(K), _c(E))


def patch_median(depth, valid, frames, h, w):
    """Median trusted depth per image patch for recon_change.patch_table, or None."""
    if _rs is None or "patch_median" in OFF or not _ok(depth, mask=valid):
        return None
    return _rs.patch_median(_c(depth), _c(valid), np.asarray(frames, np.int64).reshape(-1), int(h), int(w))


def nearest(a, b):
    """(index, distance) of the nearest row of b for each row of a, or None."""
    if _rs is None or "nearest" in OFF or not _ok(a, b) or not len(b):
        return None
    return _rs.nearest(_c(a), _c(b))


def lookalike_count(centre, z, reach, feat_a, pos, feat_b, frame_b, min_sim):
    """Per flagged patch, the number of other-clip frames with a look-alike patch in reach, or None."""
    if _rs is None or "lookalike_count" in OFF or not _ok(feat_a, pos, feat_b):
        return None
    return _rs.lookalike_count(_c(np.asarray(centre, np.float64)), _c(np.asarray(z, np.float64)), float(reach), _c(feat_a),
                               _c(pos), _c(feat_b), _c(np.asarray(frame_b, np.int64)), float(np.float32(min_sim)))
