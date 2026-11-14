# How well does "where is the wearer?" work on real photos?

Status: **job running, interim numbers below.** This file is updated as results come in.

What is measured: `locate.py` (commit ea6b5a6, unchanged) on PASLCD, 10 real scenes (5 indoor,
5 outdoor), each with a reference capture and a later capture on a different camera path with
some objects moved.

## Method

- Baseline: 24, 12 or 8 photos spread evenly over a scene's reference capture (the 24 are the
  same photos `run_bench.py` uses; 12 and 8 are an even pick from those).
- One query photo at a time goes through VGGT together with the baseline, as in production
  (`rc.reconstruct`, then `locate.locate` with the query as the only new frame).
- **Same room:** the query is a photo from the later capture of the same scene.
- **Wrong room:** the query is a photo from the later capture of another scene. Over 9 rounds
  every scene's baseline is tried against one photo from each of the 9 other scenes.
- **Same room, other lighting** (indoor scenes only): the baseline is the reference capture of the scene's second
  PASLCD instance, taken under different lighting than the query photos.
- **Position:** PASLCD ships a COLMAP model per scene with a pose for every photo of both
  captures in one frame (checked: all baseline and query photos are in it). Our baseline cameras
  are fitted to COLMAP's with a similarity transform; the query camera's position and viewing
  direction are compared after that fit. COLMAP has no real-world scale, so the dataset is
  **not metric**: position error is a percentage of the largest distance between two baseline
  cameras.
- GPU: one NVIDIA L4 on Iridis.

## How to run

See the header of `locate_eval.slurm`. `fetch_poses.py` downloads the poses (login node),
`locate_eval.py` runs the passes and writes `results.jsonl` and `results.md`.

## Results

**Interim, first 89 of about 675 passes (the job is still running; full tables follow).**

| Baseline photos | Same room: found / unsure / lost | Same-room match median (min) | Wrong room: lost / unsure / found | Wrong-room match max | Position error % of camera spread, median (p90) | Heading error deg median (p90) | Model s median | Locate s median | Peak GPU GB |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 24 | 14/15 (93%) / 1 / 0 | 0.98 (0.49) | 15/15 (100%) / 0 / 0 | 0.08 | 0.4 (0.7) | 0.4 (0.7) | 3.2 | 0.2 | 9.1 |
| 12 | 9/10 (90%) / 1 / 0 | 1.00 (0.48) | 10/10 (100%) / 0 / 0 | 0.08 | 0.3 (0.6) | 0.5 (0.7) | 1.5 | 0.1 | 8.7 |
| 8 | 10/10 (100%) / 0 / 0 | 1.00 (0.53) | 10/10 (100%) / 0 / 0 | 0.06 | 0.3 (0.6) | 0.6 (1.0) | 1.0 | 0.1 | 8.6 |

So far: no wrong-room photo scored above 0.08, no same-room photo scored below 0.48, and the
position is within about 1% of the camera spread. Too few passes yet to recommend thresholds.
