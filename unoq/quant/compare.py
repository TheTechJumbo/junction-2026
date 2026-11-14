"""Compare local outputs to supplied trusted FP32 outputs on identical public frames.
This does not run a model or transmit data. Metrics are diagnostic, not acceptance proof.
"""
import argparse
import json
import numpy as np
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--reference',required=True)
p.add_argument('--candidate',required=True)
a=p.parse_args()
with np.load(a.reference, allow_pickle=False) as ref, np.load(a.candidate, allow_pickle=False) as cand:
    d, q = ref['depth'], cand['depth']
    if d.shape != q.shape or ref['pose_enc'].shape != cand['pose_enc'].shape:
        raise ValueError('output shapes differ; use identical input/preprocessing')
    mask = np.isfinite(d) & np.isfinite(q) & (d>0) & (q>0)
    if not mask.any():raise ValueError('no valid depth pixels')
    scale = np.median(d[mask])/np.median(q[mask])
    depth_error = np.abs(q[mask]*scale-d[mask])/d[mask]
    # VGGT pose encoding uses translation, quaternion (xyzw), then field of view.
    r, c = ref['pose_enc'][...,3:7], cand['pose_enc'][...,3:7]
    r = r / np.linalg.norm(r,axis=-1,keepdims=True)
    c = c / np.linalg.norm(c,axis=-1,keepdims=True)
    angles = 2*np.arccos(np.clip(np.abs((r*c).sum(-1)),0,1))*180/np.pi
    print(json.dumps(dict(median_depth_relative_error=float(np.median(depth_error)),
                         mean_depth_relative_error=float(np.mean(depth_error)),
                         median_rotation_degrees=float(np.median(angles)), depth_alignment_scale=float(scale))))
