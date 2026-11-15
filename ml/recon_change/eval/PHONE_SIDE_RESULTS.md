# Position fix with a small model on the phone and a map made once on the server

Status on 14 November 2026: **plan only, no numbers yet.** This file is filled in as the runs finish.

## The question

Today a position fix runs VGGT (1.2 billion parameters) over 12 baseline photos plus the wearer's
newest frame: about 4 s on an L4 GPU, 33 to 39 s on 32 CPU cores, 6 to 8.5 GB of memory. That
cannot run on a phone. Seb asked whether "only a ViT" could run on the phone while the 3D map is
made separately.

The split measured here:

1. **Server, once per room.** VGGT over the baseline photos alone gives depth and a camera per
   photo. From that a map file is written: per baseline photo one global descriptor (to find the
   closest photos) and local features with the 3D point behind each.
2. **Phone, per frame.** A small model extracts features from the new frame, the closest baseline
   photos are found, features are matched, every match gives a 2D point in the new frame and a 3D
   point of the map, and the camera pose is solved from those (PnP with RANSAC). Found, unsure or
   lost is decided from the number and the spread of the matches that agree with the pose.

## What is measured

- The same queries as the walking test (`LOCATE_WALK_RESULTS.md`): 537 same-room and 178
  wrong-room views of three TUM RGB-D rooms, 12 baseline photos (and 24), true poses from motion
  capture. Same error definition: the map's baseline cameras are fitted to their true positions
  with one similarity transform, then the query camera is compared.
- Variants: (a) DINOv2 ViT-S/14 and ViT-B/14 patch features ("just a ViT"); (b) XFeat, a 0.66 M
  parameter local-feature network (Apache-2.0); (c) SIFT from OpenCV as a no-network reference.
- Every limit (found / unsure / lost) is chosen on two rooms and reported on the third.
- Speed and peak memory of the phone side on 2, 4 and 8 pinned CPU cores of a server node, split
  into feature extraction, retrieval and matching, and pose. Server cores are not a phone.
- What rate of fixes each variant reaches on 4 cores, and what that does to how often the arrow
  is right between fixes (`TRACK_RESULTS.md`: a fix goes stale in seconds).

## Files

- `ml/recon_change/phone_side.py`: map building and the per-frame step.
- `eval/phone_side_recon.py`, `.slurm`: VGGT once over each room's baseline photos.
- `eval/phone_side_fetch.sh`: fetches DINOv2 ViT-S and XFeat on a login node.
- `eval/phone_side_eval.py`, `phone_side_tables.py`, `phone_side_bench.py`: accuracy, tables, speed.
- `eval/test_phone_side.py`: offline test on synthetic data.
