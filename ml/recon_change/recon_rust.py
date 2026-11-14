"""Optional Rust versions of the hottest loops in recon_change.py (JUN-28).

Build once with  pip install ./rust  (needs a Rust toolchain; see rust/README.md). Without the
build, or with RECON_FAST=0, recon_change.py runs its NumPy code exactly as before.

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


def enabled():
    return _rs is not None


def _ok(*arrays, mask=None):
    return all(a.dtype == np.float32 for a in arrays) and (mask is None or mask.dtype == np.bool_)


def _c(a):
    return np.ascontiguousarray(a)


def depth_gap(points, E, K, depth, near, valid):
    """recon_change.depth_gap in Rust, or None when Rust cannot take these inputs."""
    if _rs is None or not _ok(points, E, K, depth, near, mask=valid):
        return None
    return list(_rs.depth_gap(_c(points), _c(E), _c(K), _c(depth), _c(near), _c(valid)))


def votes(r, valid, near, pts, frames, tol_agree, tol_free):
    """recon_change.votes in Rust, or None when Rust cannot take these inputs."""
    if _rs is None or not _ok(pts, r.E, r.K, r.depth, near, mask=valid):
        return None
    # NumPy compares a float32 array with a Python float in float32, so round the tolerances the same way.
    return _rs.votes(_c(pts), _c(r.E), _c(r.K), _c(r.depth), _c(near), _c(valid),
                     np.asarray(frames, np.int64).reshape(-1), float(np.float32(tol_agree)), float(np.float32(tol_free)))
