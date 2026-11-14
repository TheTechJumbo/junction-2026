# How well does "where is the wearer?" work on real photos?

Status: **main run finished (540 scored passes). A follow-up job is queued** (why one wrong-room
photo was accepted, a baseline under different lighting, more wrong-room photos). Its results
will be added here.

What is measured: `locate.py` (commit ea6b5a6, unchanged) on PASLCD: 10 real scenes (5 indoor,
5 outdoor), each with a reference capture and a later capture on a different camera path with
some objects moved. GPU: one NVIDIA L4 on Iridis.

## Short answer

| | 24 baseline photos | 12 | 8 |
| --- | --- | --- | --- |
| Same room: found / unsure / lost (90 queries each) | 89 / 1 / 0 | 89 / 1 / 0 | 88 / 2 / 0 |
| Wrong room: lost / unsure / **found** (90 queries each) | 89 / 1 / **0** | 89 / 0 / **1** | 89 / 1 / **0** |
| Position error, % of the baseline cameras' spread: median (worst) | 0.4 (1.7) | 0.4 (1.9) | 0.4 (1.5) |
| Heading error, degrees: median (worst) | 0.4 (1.6) | 0.5 (1.6) | 0.7 (1.6) |
| Time per query on the L4: model + locate | 3.2 s + 0.2 s | 1.5 s + 0.1 s | 1.0 s + 0.1 s |

- **Same room works.** 266 of 270 same-room queries are found, none is lost. The 4 "unsure"
  ones have a correct position too (error under 1%).
- **Wrong room is not safe yet.** 267 of 270 wrong-room queries are lost, but one photo of the
  Cantina was **found with match 0.97** on a Lunch room baseline of 12 photos. The same photo on
  the same room scored 0.02 with 24 baseline photos and 0.37 (unsure) with 8. No threshold
  separates 0.97 from a real match. Cause: under investigation in the follow-up job.
- **When the room is right, the position is right.** The dataset's poses are not metric, so the
  error is given against the size of the baseline camera path: under 2% in every one of 270
  queries, heading within 1.6 degrees. A blind guess (the middle of the baseline cameras) is 34%
  off.
- **Fewer baseline photos cost nothing in accuracy** and the time falls from 3.5 s to 1.1 s,
  but the one false "found" happened at 12 and the worst "unsure" cases at 8. With one event in
  90 this is not enough to rank the sizes; see the follow-up.

## Method

- Baseline: 24, 12 or 8 photos spread evenly over a scene's reference capture (the 24 are the
  same photos `run_bench.py` uses; 12 and 8 are an even pick from those).
- One query photo at a time goes through VGGT together with the baseline, as in production
  (`rc.reconstruct`, then `locate.locate` with the query as the only new frame).
- **Same room:** the query is a photo from the later capture of the same scene, 9 per scene.
- **Wrong room:** the query is a photo from the later capture of another scene. Every scene's
  baseline is tried against one photo from each of the 9 other scenes.
- The same queries are used for every baseline size.
- **Position:** PASLCD ships a COLMAP model per scene with a pose for every photo of both
  captures in one frame (checked: every baseline and query photo used here is in it). Our
  baseline cameras are fitted to COLMAP's with a similarity transform (scale, rotation, shift);
  the query camera's position and viewing direction are compared after that fit. COLMAP has no
  real-world scale, so the dataset is **not metric**: position error is a percentage of the
  largest distance between two baseline cameras. The pose maths was checked on made-up cameras
  with a known offset (0.3 units and 10 degrees recovered exactly).
- Repeatability: PASLCD's second instance reuses the same query pictures as re-encoded files.
  135 such pairs were run by accident; the match differed by at most 0.02 and the state never
  changed. These passes are not in the tables.

## How to run

See the header of `locate_eval.slurm`. `fetch_poses.py` downloads the poses (login node),
`locate_eval.py` runs the passes and writes `results.jsonl` and `results.md`; a second run with
the same `--out` continues. The tables below are `results.md` as written by the script.

## Thresholds (found = 0.5, unsure = 0.2)

Not changed yet. From the main run:

- Same room: the lowest match is 0.49 with 24 baseline photos, 0.48 with 12, 0.30 with 8.
- Wrong room: the highest match is 0.23 with 24 baseline photos; with 12 and 8 the one bad
  photo scored 0.97 and 0.37, everything else at most 0.17.
- A recommendation follows once the follow-up job has the other-lighting scores.

## Not tested, or unsure

- A real home, the glasses camera, motion blur, night lighting, a person in view.
- Query photos far from the baseline path. PASLCD's later capture stays close to it: the
  nearest baseline camera is a median 6% of the camera spread away (up to 40% with 8 photos).
- Metres. The dataset has no real scale. "Rough m" in the tables uses `locate.py`'s own
  assumption that things are about 2 m away.
- The truth is COLMAP, itself an estimate. Our baseline cameras fit it to 0.4% of the spread.
- Several query frames at once (`locate` accepts a short clip): only one photo per query here.
- Places and bearings to them (`waypoints`): not scored.
- Rooms that look alike in one home (two bedrooms). The scenes here are different places, and
  one wrong-room photo was still accepted.
- The CPU worker: every pass ran on the L4 GPU.

## Full tables (main run)

Thresholds in use: found at match >= 0.5, lost at match < 0.2. Passes so far: 675.

### 1. Same room (24 baseline photos, one query photo from the later capture)

| Scene | Queries | Found | Unsure | Lost | Match min | Match median | Match max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Cantina (indoor) | 9 | 8/9 (89%) | 1/9 (11%) | 0/9 (0%) | 0.49 | 0.99 | 1.00 |
| Lounge (indoor) | 9 | 9/9 (100%) | 0/9 (0%) | 0/9 (0%) | 0.92 | 1.00 | 1.00 |
| Printing_area (indoor) | 9 | 9/9 (100%) | 0/9 (0%) | 0/9 (0%) | 0.67 | 1.00 | 1.00 |
| Lunch_room (indoor) | 9 | 9/9 (100%) | 0/9 (0%) | 0/9 (0%) | 0.88 | 1.00 | 1.00 |
| Meeting_room (indoor) | 9 | 9/9 (100%) | 0/9 (0%) | 0/9 (0%) | 0.86 | 1.00 | 1.00 |
| Garden (outdoor) | 9 | 9/9 (100%) | 0/9 (0%) | 0/9 (0%) | 0.78 | 0.97 | 1.00 |
| Pots (outdoor) | 9 | 9/9 (100%) | 0/9 (0%) | 0/9 (0%) | 0.87 | 0.96 | 1.00 |
| Zen (outdoor) | 9 | 9/9 (100%) | 0/9 (0%) | 0/9 (0%) | 0.56 | 1.00 | 1.00 |
| Playground (outdoor) | 9 | 9/9 (100%) | 0/9 (0%) | 0/9 (0%) | 0.98 | 1.00 | 1.00 |
| Porch (outdoor) | 9 | 9/9 (100%) | 0/9 (0%) | 0/9 (0%) | 0.94 | 1.00 | 1.00 |
| **Indoor pooled** | 45 | 44/45 (98%) | 1/45 (2%) | 0/45 (0%) | 0.49 | 1.00 | 1.00 |
| **Outdoor pooled** | 45 | 45/45 (100%) | 0/45 (0%) | 0/45 (0%) | 0.56 | 1.00 | 1.00 |
| **All pooled** | 90 | 89/90 (99%) | 1/90 (1%) | 0/90 (0%) | 0.49 | 1.00 | 1.00 |

### 2. Wrong room (24 baseline photos, one query photo from another scene; must be lost)

| Baseline scene | Queries | Lost | Unsure (wrong) | Found (wrong) | Match median | Match max | Highest match came from |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Cantina (indoor) | 9 | 9/9 (100%) | 0/9 (0%) | 0/9 (0%) | 0.00 | 0.01 | Printing_area photo on Cantina baseline |
| Lounge (indoor) | 9 | 8/9 (89%) | 1/9 (11%) | 0/9 (0%) | 0.00 | 0.23 | Meeting_room photo on Lounge baseline |
| Printing_area (indoor) | 9 | 9/9 (100%) | 0/9 (0%) | 0/9 (0%) | 0.00 | 0.00 | Lunch_room photo on Printing_area baseline |
| Lunch_room (indoor) | 9 | 9/9 (100%) | 0/9 (0%) | 0/9 (0%) | 0.00 | 0.08 | Porch photo on Lunch_room baseline |
| Meeting_room (indoor) | 9 | 9/9 (100%) | 0/9 (0%) | 0/9 (0%) | 0.00 | 0.12 | Lunch_room photo on Meeting_room baseline |
| Garden (outdoor) | 9 | 9/9 (100%) | 0/9 (0%) | 0/9 (0%) | 0.00 | 0.07 | Zen photo on Garden baseline |
| Pots (outdoor) | 9 | 9/9 (100%) | 0/9 (0%) | 0/9 (0%) | 0.00 | 0.08 | Zen photo on Pots baseline |
| Zen (outdoor) | 9 | 9/9 (100%) | 0/9 (0%) | 0/9 (0%) | 0.00 | 0.00 | Playground photo on Zen baseline |
| Playground (outdoor) | 9 | 9/9 (100%) | 0/9 (0%) | 0/9 (0%) | 0.00 | 0.16 | Zen photo on Playground baseline |
| Porch (outdoor) | 9 | 9/9 (100%) | 0/9 (0%) | 0/9 (0%) | 0.00 | 0.00 | Cantina photo on Porch baseline |
| **Indoor pooled** | 45 | 44/45 (98%) | 1/45 (2%) | 0/45 (0%) | 0.00 | 0.23 | Meeting_room photo on Lounge baseline |
| **Outdoor pooled** | 45 | 45/45 (100%) | 0/45 (0%) | 0/45 (0%) | 0.00 | 0.16 | Zen photo on Playground baseline |
| **All pooled** | 90 | 89/90 (99%) | 1/90 (1%) | 0/90 (0%) | 0.00 | 0.23 | Meeting_room photo on Lounge baseline |

Wrong-room queries that were not lost (all baseline sizes):

- Cantina photo Inst_1_test_IMG_2891.jpg on Lunch_room baseline of 12: found, match 0.97
- Cantina photo Inst_1_test_IMG_2891.jpg on Lunch_room baseline of 8: unsure, match 0.37
- Meeting_room photo Inst_1_test_IMG_1871.jpg on Lounge baseline of 24: unsure, match 0.23

### 3. Is the position right? (same-room queries, 24 baseline photos, against COLMAP poses)

Position error is a percentage of the largest distance between two baseline cameras. "Rough m" uses locate.py's own assumed scale (things are about 2 m away), so it is not a measured distance.

| Scene | Queries with truth | Position error % median | p90 | max | Rough m median | Heading error deg median | p90 | max | Baseline fit % | Query to nearest baseline camera % median |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Cantina (indoor) | 9 | 0.4 | 0.7 | 0.9 | 0.03 | 0.4 | 0.5 | 0.5 | 0.5 | 4.8 |
| Lounge (indoor) | 9 | 0.1 | 0.2 | 0.3 | 0.01 | 0.5 | 0.5 | 0.6 | 0.2 | 5.3 |
| Printing_area (indoor) | 9 | 0.2 | 0.5 | 0.7 | 0.01 | 0.7 | 0.8 | 1.0 | 0.4 | 7.5 |
| Lunch_room (indoor) | 9 | 0.4 | 1.6 | 1.7 | 0.03 | 0.4 | 1.3 | 1.4 | 0.4 | 3.6 |
| Meeting_room (indoor) | 9 | 0.3 | 1.1 | 1.3 | 0.01 | 0.5 | 1.1 | 1.6 | 0.5 | 5.7 |
| Garden (outdoor) | 9 | 0.4 | 0.7 | 0.7 | 0.01 | 0.3 | 0.9 | 0.9 | 0.3 | 9.3 |
| Pots (outdoor) | 9 | 0.4 | 0.6 | 0.7 | 0.01 | 0.4 | 0.6 | 0.6 | 0.4 | 12.1 |
| Zen (outdoor) | 9 | 0.3 | 0.6 | 0.8 | 0.01 | 0.4 | 0.4 | 0.4 | 0.3 | 9.6 |
| Playground (outdoor) | 9 | 0.8 | 1.1 | 1.1 | 0.06 | 0.4 | 1.0 | 1.4 | 0.7 | 3.1 |
| Porch (outdoor) | 9 | 0.3 | 0.5 | 0.6 | 0.01 | 0.5 | 1.3 | 1.5 | 0.6 | 7.6 |
| **Indoor pooled** | 45 | 0.3 | 0.9 | 1.7 | 0.02 | 0.5 | 1.0 | 1.6 | 0.4 | 5.3 |
| **Outdoor pooled** | 45 | 0.4 | 0.8 | 1.1 | 0.01 | 0.4 | 0.9 | 1.5 | 0.4 | 8.5 |
| **All pooled** | 90 | 0.4 | 0.8 | 1.7 | 0.01 | 0.4 | 1.0 | 1.6 | 0.4 | 6.4 |
| **All pooled, found only** | 89 | 0.4 | 0.8 | 1.7 | 0.01 | 0.4 | 1.0 | 1.6 | 0.4 | 6.4 |

For scale: guessing the middle of the baseline cameras every time would give a median position error of 33.6%.

Position and heading error by match score (same-room queries, all baseline sizes):

| Match | Queries with truth | Position error % median | max | Heading error deg median | max |
| --- | --- | --- | --- | --- | --- |
| 0.0 to 0.2 | 0 | - | - | - | - |
| 0.2 to 0.5 | 4 | 0.4 | 0.6 | 0.5 | 0.7 |
| 0.5 to 0.8 | 10 | 0.6 | 1.1 | 0.6 | 0.8 |
| 0.8 to 1.0 | 256 | 0.4 | 1.9 | 0.5 | 1.6 |

### 4. Cost: fewer baseline photos

| Baseline photos | Same room: found / unsure / lost | Same-room match median (min) | Wrong room: lost / unsure / found | Wrong-room match max | Position error % median (p90) | Heading error deg median (p90) | Model s median | Locate s median | Peak GPU GB |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 24 | 89/90 (99%) / 1 / 0 | 1.00 (0.49) | 89/90 (99%) / 1 / 0 | 0.23 | 0.4 (0.8) | 0.4 (1.0) | 3.2 | 0.2 | 9.1 |
| 12 | 89/90 (99%) / 1 / 0 | 1.00 (0.48) | 89/90 (99%) / 0 / 1 | 0.97 | 0.4 (0.9) | 0.5 (1.1) | 1.5 | 0.1 | 8.7 |
| 8 | 88/90 (98%) / 2 / 0 | 1.00 (0.30) | 89/90 (99%) / 1 / 0 | 0.37 | 0.4 (1.0) | 0.7 (1.2) | 1.0 | 0.1 | 8.6 |

Model s is VGGT inference as printed by the detector; Locate s is locate.locate. Loading the model and reading the photos are not included.

### 5. Match scores and thresholds

Share of queries with a match at or above each value. Same room should be high, wrong room should be zero.

| Match at or above | Same room, 24 | Same room, 12 | Same room, 8 | Wrong room, 24 | Wrong room, 12 | Wrong room, 8 |
| --- | --- | --- | --- | --- | --- | --- |
| 0.05 | 90/90 (100%) | 90/90 (100%) | 90/90 (100%) | 8/90 (9%) | 9/90 (10%) | 10/90 (11%) |
| 0.10 | 90/90 (100%) | 90/90 (100%) | 90/90 (100%) | 4/90 (4%) | 2/90 (2%) | 6/90 (7%) |
| 0.15 | 90/90 (100%) | 90/90 (100%) | 90/90 (100%) | 2/90 (2%) | 2/90 (2%) | 4/90 (4%) |
| 0.20 | 90/90 (100%) | 90/90 (100%) | 90/90 (100%) | 1/90 (1%) | 1/90 (1%) | 1/90 (1%) |
| 0.25 | 90/90 (100%) | 90/90 (100%) | 90/90 (100%) | 0/90 (0%) | 1/90 (1%) | 1/90 (1%) |
| 0.30 | 90/90 (100%) | 90/90 (100%) | 90/90 (100%) | 0/90 (0%) | 1/90 (1%) | 1/90 (1%) |
| 0.40 | 90/90 (100%) | 90/90 (100%) | 89/90 (99%) | 0/90 (0%) | 1/90 (1%) | 0/90 (0%) |
| 0.50 | 89/90 (99%) | 89/90 (99%) | 88/90 (98%) | 0/90 (0%) | 1/90 (1%) | 0/90 (0%) |
| 0.60 | 87/90 (97%) | 88/90 (98%) | 87/90 (97%) | 0/90 (0%) | 1/90 (1%) | 0/90 (0%) |
| 0.70 | 86/90 (96%) | 86/90 (96%) | 86/90 (96%) | 0/90 (0%) | 1/90 (1%) | 0/90 (0%) |
| 0.80 | 85/90 (94%) | 86/90 (96%) | 85/90 (94%) | 0/90 (0%) | 1/90 (1%) | 0/90 (0%) |
| 0.90 | 79/90 (88%) | 79/90 (88%) | 75/90 (83%) | 0/90 (0%) | 1/90 (1%) | 0/90 (0%) |

| Baseline photos | Same room: min | p10 | median | Wrong room: median | p90 | max |
| --- | --- | --- | --- | --- | --- | --- |
| 24 | 0.49 | 0.89 | 1.00 | 0.00 | 0.04 | 0.23 |
| 12 | 0.48 | 0.87 | 1.00 | 0.00 | 0.04 | 0.97 |
| 8 | 0.30 | 0.86 | 1.00 | 0.00 | 0.05 | 0.37 |
