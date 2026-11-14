# How well does "where is the wearer?" work on real photos?

Status: **job running, no numbers yet.** This file is updated as results come in.

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
- **Same room on another day** (indoor scenes only): the query is from the scene's second
  PASLCD instance. No pose truth for these.
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

Pending.
