# How well does "where is the wearer?" work on real photos?

Status: **finished.** 885 scored passes on one NVIDIA L4 (Iridis), none failed.

What is measured: `locate.py` (commit ea6b5a6, unchanged) on PASLCD: 10 real scenes (5 indoor,
5 outdoor), each with a reference capture and a later capture on a different camera path with
some objects moved.

## Short answer

| | 24 baseline photos | 12 | 8 |
| --- | --- | --- | --- |
| Same room: found / unsure / lost (90 queries each) | 89 / 1 / 0 | 89 / 1 / 0 | 88 / 2 / 0 |
| Wrong room: lost / unsure / **found** (180 queries each) | 178 / 2 / **0** | 178 / 1 / **1** | 177 / 3 / **0** |
| Highest wrong-room match | 0.23 | 0.97 | 0.39 |
| Lowest same-room match | 0.49 | 0.48 | 0.30 |
| Position error, % of the baseline cameras' spread: median (worst) | 0.4 (1.7) | 0.4 (1.9) | 0.4 (1.5) |
| Heading error, degrees: median (worst) | 0.4 (1.6) | 0.5 (1.6) | 0.7 (1.6) |
| Time per query on the L4: model + locate | 3.2 s + 0.2 s | 1.5 s + 0.1 s | 1.0 s + 0.1 s |
| Same room, baseline under other lighting (25 queries each): found / unsure / lost | 25 / 0 / 0 | 25 / 0 / 0 | 24 / 1 / 0 |

1. **Same room works.** 266 of 270 queries are found and none is lost. The 4 "unsure" ones
   have a correct position too. A baseline taken under different lighting changes nothing
   (74 of 75 found, lowest match 0.46).
2. **Wrong room: one photo was wrongly found.** 533 of 540 wrong-room queries are lost, 6 are
   unsure and 1 is **found with match 0.97**: a photo of the Cantina's kitchen counter on a
   Lunch room baseline of 12 photos. No threshold can reject 0.97. With 24 baseline photos the
   same photo scored 0.02, and nothing scored above 0.23 in 180 tries. 0 of 180 is not proof of
   zero: the true rate could still be up to about 1.7% (95% bound).
3. **When the room is right, the position is right.** The dataset's poses are not metric, so
   the error is given against the size of the baseline camera path: under 2% in all 270
   queries, heading within 1.6 degrees. A blind guess (the middle of the baseline cameras) is
   34% off.
4. **Use 24 baseline photos.** Fewer photos do not hurt same-room results or position, and
   the time falls from 3.5 s to 1.7 s (12) or 1.1 s (8). But 24 is the only size with no wrong
   "found", and the three highest wrong-room scores at 12 and 8 (0.97, 0.39, 0.37) are all
   0.07 or less at 24.
5. **Thresholds: keep found = 0.5 and unsure = 0.2.** `locate.py` is not changed. Details below.

## Why the wrong-room photo was accepted

The three worst cases were run again with the parts of the score printed (same scores came
back, so this is repeatable):

| Cantina photo 2891 on Lunch room baseline | 24 photos | 12 | 8 |
| --- | --- | --- | --- |
| Match | 0.02 | 0.97 | 0.37 |
| Share of the query's trusted pixels that two baseline photos saw at the same depth | 4% | 73% | 22% |
| Share of the query's pixels that are trusted (baseline: about 70%) | 41% | 84% | 91% |
| Model confidence, median: query / baseline | 5.1 / 8.2 | 4.4 / 5.3 | 3.8 / 3.5 |
| How well baseline photos match each other (the score's yardstick) | 0.93 | 0.76 | 0.60 |

- The photo does not look like the room: a kitchen counter against an open room with tables.
  The model still placed it inside the Lunch room, and with 12 baseline photos its surfaces
  landed on the room's surfaces. The score only checks depth agreement, so it was accepted.
- What protects at 24 photos is the trusted-pixel filter: the model is much less confident
  about the foreign photo than about the baseline, so few of its pixels count. With 12 or 8
  photos the baseline's own confidence falls to the foreign photo's level and the filter
  stops working.
- The yardstick was normal (0.76), so this is not a division by a small number.
- 5 of the 7 wrong-room queries that were not lost are Cantina photos.

Suggested fixes, **not tested**: keep 24 baseline photos; also require that the matched
pixels look alike (the patch features `recon_change` already computes for its look-alike
check); ask for agreement over a few frames before showing "found".

## Thresholds (found = 0.5, unsure = 0.2)

- With 24 baseline photos the two groups are well apart: wrong room at most 0.23, same room at
  least 0.49 (0.53 under other lighting). Any "found" value between 0.25 and 0.48 gives the
  same split, so 0.5 is slightly strict: it costs 1 same-room query in 90 ("unsure" at 0.49).
- Lowering "found" to 0.4 would gain that one query, but wrong-room photos reach 0.37 and 0.39
  with 8 baseline photos. Not worth it.
- Raising "unsure" to 0.3 would turn the 2 wrong-room "unsure" answers at 24 photos (0.22,
  0.23) into "lost" and lose no same-room query at 24 or 12 photos. That is 2 events in 180:
  a reasonable choice, but not clear enough to change the code on.
- No threshold helps with the 0.97 case.

## Method

- Baseline: 24, 12 or 8 photos spread evenly over a scene's reference capture (the 24 are the
  same photos `run_bench.py` uses; 12 and 8 are an even pick from those).
- One query photo at a time goes through VGGT together with the baseline, as in production
  (`rc.reconstruct`, then `locate.locate` with the query as the only new frame).
- **Same room:** the query is a photo from the later capture of the same scene, 9 per scene.
- **Wrong room:** the query is a photo from the later capture of another scene. Every scene's
  baseline is tried against two different photos from each of the 9 other scenes.
- **Same room, other lighting** (the 5 indoor scenes): the baseline is the reference capture of
  the scene's second PASLCD instance, taken under different lighting than the query photos.
  5 queries per scene.
- The same queries are used for every baseline size.
- **Position:** PASLCD ships a COLMAP model per scene with a pose for every photo of both
  captures in one frame (checked: every baseline and query photo used here is in it). Our
  baseline cameras are fitted to COLMAP's with a similarity transform (scale, rotation, shift);
  the query camera's position and viewing direction are compared after that fit. COLMAP has no
  real-world scale, so the dataset is **not metric**: position error is a percentage of the
  largest distance between two baseline cameras. The pose maths was checked on made-up cameras
  with a known offset (0.3 units and 10 degrees recovered exactly).
- **Lounge under other lighting shows heading errors of 6 and 160 degrees in table 2b. These
  are not real.** One baseline photo (the first, IMG_3976) sits in a different place in COLMAP
  than in our reconstruction, and it skews the fit. With that photo left out of the fit, the
  other 23 baseline cameras agree to 0.3% and the query is within 0.7% and 1.2 degrees (checked
  for one query, at 24 and 12 photos). In COLMAP's own path that photo is 7.4 normal steps from
  the next one, the largest jump on the path, so the truth is the likelier side to be wrong.
  Not proven. Note that `locate.py` takes the map's origin and north from this first photo.
- Repeatability: PASLCD's second instance reuses the same query pictures as re-encoded files.
  135 such pairs were run by accident in the first job; the match differed by at most 0.02 and
  the state never changed. These passes are not in the tables, and neither is the single pass
  of a wrong-room round that the time limit cut short (it was "lost").

## How to run

See the header of `locate_eval.slurm`. `fetch_poses.py` downloads the poses (login node; add
`--instance 2` for the other-lighting test), `locate_eval.py` runs the passes and writes
`results.jsonl` and `results.md`; a second run with the same `--out` continues. The tables
below are `results.md` as written by the script. The score-parts table above and the Lounge
check came from two one-off scripts that are not in the repo.

## Not tested, or unsure

- A real home, the glasses camera, motion blur, night lighting, a person in view.
- Rooms that look alike in one home (two bedrooms). The scenes here are different places, and
  one wrong-room photo was still accepted.
- Whether the Cantina and the Lunch room are connected spaces. The photos show different
  places (a kitchenette is visible at the far end of the Lunch room, with different
  cupboards), but this was judged from 5 pictures only.
- Why the model placed the kitchen photo inside the Lunch room: only the score parts were
  examined, on 4 cases.
- The suggested fixes (appearance check, several frames).
- Query photos far from the baseline path. PASLCD's later capture stays close to it: the
  nearest baseline camera is a median 6% of the camera spread away at 24 photos (up to 40% at 8).
- Metres. The dataset has no real scale. "Rough m" in the tables uses `locate.py`'s own
  assumption that things are about 2 m away.
- The truth is COLMAP, itself an estimate (see the Lounge note). Elsewhere our baseline cameras
  fit it to 0.2 to 0.7% of the spread.
- Several query frames at once (`locate` accepts a short clip): only one photo per query here.
- Places and bearings to them (`waypoints`): not scored.
- The CPU worker, and timings end to end (upload, queue): every pass ran on the L4 GPU and
  only the model and `locate` were timed.
- Other-lighting test: 5 queries per scene only, indoor scenes only.

## Full tables

Thresholds in use: found at match >= 0.5, lost at match < 0.2. Passes so far: 885.

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
| Cantina (indoor) | 18 | 18/18 (100%) | 0/18 (0%) | 0/18 (0%) | 0.00 | 0.01 | Printing_area photo on Cantina baseline |
| Lounge (indoor) | 18 | 17/18 (94%) | 1/18 (6%) | 0/18 (0%) | 0.00 | 0.23 | Meeting_room photo on Lounge baseline |
| Printing_area (indoor) | 18 | 17/18 (94%) | 1/18 (6%) | 0/18 (0%) | 0.00 | 0.22 | Cantina photo on Printing_area baseline |
| Lunch_room (indoor) | 18 | 18/18 (100%) | 0/18 (0%) | 0/18 (0%) | 0.00 | 0.09 | Printing_area photo on Lunch_room baseline |
| Meeting_room (indoor) | 18 | 18/18 (100%) | 0/18 (0%) | 0/18 (0%) | 0.00 | 0.12 | Lunch_room photo on Meeting_room baseline |
| Garden (outdoor) | 18 | 18/18 (100%) | 0/18 (0%) | 0/18 (0%) | 0.00 | 0.08 | Zen photo on Garden baseline |
| Pots (outdoor) | 18 | 18/18 (100%) | 0/18 (0%) | 0/18 (0%) | 0.00 | 0.08 | Zen photo on Pots baseline |
| Zen (outdoor) | 18 | 18/18 (100%) | 0/18 (0%) | 0/18 (0%) | 0.00 | 0.00 | Playground photo on Zen baseline |
| Playground (outdoor) | 18 | 18/18 (100%) | 0/18 (0%) | 0/18 (0%) | 0.01 | 0.16 | Zen photo on Playground baseline |
| Porch (outdoor) | 18 | 18/18 (100%) | 0/18 (0%) | 0/18 (0%) | 0.00 | 0.00 | Cantina photo on Porch baseline |
| **Indoor pooled** | 90 | 88/90 (98%) | 2/90 (2%) | 0/90 (0%) | 0.00 | 0.23 | Meeting_room photo on Lounge baseline |
| **Outdoor pooled** | 90 | 90/90 (100%) | 0/90 (0%) | 0/90 (0%) | 0.00 | 0.16 | Zen photo on Playground baseline |
| **All pooled** | 180 | 178/180 (99%) | 2/180 (1%) | 0/180 (0%) | 0.00 | 0.23 | Meeting_room photo on Lounge baseline |

Wrong-room queries that were not lost (all baseline sizes):

- Cantina photo Inst_1_test_IMG_2891.jpg on Lunch_room baseline of 12: found, match 0.97
- Cantina photo Inst_1_test_IMG_2863.jpg on Lunch_room baseline of 8: unsure, match 0.39
- Cantina photo Inst_1_test_IMG_2891.jpg on Lunch_room baseline of 8: unsure, match 0.37
- Meeting_room photo Inst_1_test_IMG_1871.jpg on Lounge baseline of 24: unsure, match 0.23
- Cantina photo Inst_1_test_IMG_2876.jpg on Printing_area baseline of 24: unsure, match 0.22
- Cantina photo Inst_1_test_IMG_2863.jpg on Lunch_room baseline of 12: unsure, match 0.21
- Pots photo Inst_1_test_IMG_E2693.JPG on Porch baseline of 8: unsure, match 0.21

### 2b. Same room, baseline taken under different lighting (indoor scenes, PASLCD instance 2)

| Baseline photos | Scene | Queries | Found | Unsure | Lost | Match min | Match median | Position error % median | max | Heading error deg median | max |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 24 | Cantina (indoor) | 5 | 5/5 (100%) | 0/5 (0%) | 0/5 (0%) | 0.53 | 1.00 | 0.3 | 0.6 | 1.1 | 1.4 |
| 24 | Lounge (indoor) | 5 | 5/5 (100%) | 0/5 (0%) | 0/5 (0%) | 0.93 | 0.95 | 1.1 | 2.3 | 6.7 | 6.9 |
| 24 | Printing_area (indoor) | 5 | 5/5 (100%) | 0/5 (0%) | 0/5 (0%) | 0.85 | 0.91 | 0.4 | 0.5 | 0.4 | 0.6 |
| 24 | Lunch_room (indoor) | 5 | 5/5 (100%) | 0/5 (0%) | 0/5 (0%) | 1.00 | 1.00 | 0.5 | 1.4 | 0.3 | 1.3 |
| 24 | Meeting_room (indoor) | 5 | 5/5 (100%) | 0/5 (0%) | 0/5 (0%) | 0.81 | 1.00 | 0.3 | 1.1 | 0.4 | 0.9 |
| 24 | **Indoor pooled** | 25 | 25/25 (100%) | 0/25 (0%) | 0/25 (0%) | 0.53 | 1.00 | 0.5 | 2.3 | 0.9 | 6.9 |
| 12 | Cantina (indoor) | 5 | 5/5 (100%) | 0/5 (0%) | 0/5 (0%) | 0.54 | 1.00 | 0.3 | 0.4 | 1.1 | 1.5 |
| 12 | Lounge (indoor) | 5 | 5/5 (100%) | 0/5 (0%) | 0/5 (0%) | 0.84 | 0.96 | 6.3 | 11.2 | 161.1 | 163.1 |
| 12 | Printing_area (indoor) | 5 | 5/5 (100%) | 0/5 (0%) | 0/5 (0%) | 0.81 | 0.97 | 0.3 | 0.5 | 0.3 | 0.5 |
| 12 | Lunch_room (indoor) | 5 | 5/5 (100%) | 0/5 (0%) | 0/5 (0%) | 1.00 | 1.00 | 0.5 | 1.1 | 0.4 | 1.1 |
| 12 | Meeting_room (indoor) | 5 | 5/5 (100%) | 0/5 (0%) | 0/5 (0%) | 0.71 | 1.00 | 0.2 | 1.3 | 0.3 | 1.0 |
| 12 | **Indoor pooled** | 25 | 25/25 (100%) | 0/25 (0%) | 0/25 (0%) | 0.54 | 1.00 | 0.4 | 11.2 | 0.9 | 163.1 |
| 8 | Cantina (indoor) | 5 | 4/5 (80%) | 1/5 (20%) | 0/5 (0%) | 0.46 | 1.00 | 0.4 | 0.5 | 0.9 | 1.1 |
| 8 | Lounge (indoor) | 5 | 5/5 (100%) | 0/5 (0%) | 0/5 (0%) | 0.92 | 1.00 | 5.3 | 11.2 | 163.9 | 165.2 |
| 8 | Printing_area (indoor) | 5 | 5/5 (100%) | 0/5 (0%) | 0/5 (0%) | 0.80 | 1.00 | 0.4 | 0.7 | 1.2 | 1.4 |
| 8 | Lunch_room (indoor) | 5 | 5/5 (100%) | 0/5 (0%) | 0/5 (0%) | 1.00 | 1.00 | 0.5 | 1.4 | 0.3 | 1.3 |
| 8 | Meeting_room (indoor) | 5 | 5/5 (100%) | 0/5 (0%) | 0/5 (0%) | 0.79 | 1.00 | 0.2 | 1.3 | 0.2 | 1.1 |
| 8 | **Indoor pooled** | 25 | 24/25 (96%) | 1/25 (4%) | 0/25 (0%) | 0.46 | 1.00 | 0.5 | 11.2 | 0.9 | 165.2 |

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
| 24 | 89/90 (99%) / 1 / 0 | 1.00 (0.49) | 178/180 (99%) / 2 / 0 | 0.23 | 0.4 (0.8) | 0.4 (1.0) | 3.2 | 0.2 | 9.1 |
| 12 | 89/90 (99%) / 1 / 0 | 1.00 (0.48) | 178/180 (99%) / 1 / 1 | 0.97 | 0.4 (0.9) | 0.5 (1.1) | 1.5 | 0.1 | 8.7 |
| 8 | 88/90 (98%) / 2 / 0 | 1.00 (0.30) | 177/180 (98%) / 3 / 0 | 0.39 | 0.4 (1.0) | 0.7 (1.2) | 1.0 | 0.1 | 8.6 |

Model s is VGGT inference as printed by the detector; Locate s is locate.locate. Loading the model and reading the photos are not included.

### 5. Match scores and thresholds

Share of queries with a match at or above each value. Same room should be high, wrong room should be zero.

| Match at or above | Same room, 24 | Same room, 12 | Same room, 8 | Wrong room, 24 | Wrong room, 12 | Wrong room, 8 | Same room, other lighting, all sizes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.05 | 90/90 (100%) | 90/90 (100%) | 90/90 (100%) | 17/180 (9%) | 21/180 (12%) | 23/180 (13%) | 75/75 (100%) |
| 0.10 | 90/90 (100%) | 90/90 (100%) | 90/90 (100%) | 6/180 (3%) | 8/180 (4%) | 15/180 (8%) | 75/75 (100%) |
| 0.15 | 90/90 (100%) | 90/90 (100%) | 90/90 (100%) | 4/180 (2%) | 3/180 (2%) | 9/180 (5%) | 75/75 (100%) |
| 0.20 | 90/90 (100%) | 90/90 (100%) | 90/90 (100%) | 2/180 (1%) | 2/180 (1%) | 3/180 (2%) | 75/75 (100%) |
| 0.25 | 90/90 (100%) | 90/90 (100%) | 90/90 (100%) | 0/180 (0%) | 1/180 (1%) | 2/180 (1%) | 75/75 (100%) |
| 0.30 | 90/90 (100%) | 90/90 (100%) | 90/90 (100%) | 0/180 (0%) | 1/180 (1%) | 2/180 (1%) | 75/75 (100%) |
| 0.40 | 90/90 (100%) | 90/90 (100%) | 89/90 (99%) | 0/180 (0%) | 1/180 (1%) | 0/180 (0%) | 75/75 (100%) |
| 0.50 | 89/90 (99%) | 89/90 (99%) | 88/90 (98%) | 0/180 (0%) | 1/180 (1%) | 0/180 (0%) | 74/75 (99%) |
| 0.60 | 87/90 (97%) | 88/90 (98%) | 87/90 (97%) | 0/180 (0%) | 1/180 (1%) | 0/180 (0%) | 72/75 (96%) |
| 0.70 | 86/90 (96%) | 86/90 (96%) | 86/90 (96%) | 0/180 (0%) | 1/180 (1%) | 0/180 (0%) | 72/75 (96%) |
| 0.80 | 85/90 (94%) | 86/90 (96%) | 85/90 (94%) | 0/180 (0%) | 1/180 (1%) | 0/180 (0%) | 70/75 (93%) |
| 0.90 | 79/90 (88%) | 79/90 (88%) | 75/90 (83%) | 0/180 (0%) | 1/180 (1%) | 0/180 (0%) | 63/75 (84%) |

| Baseline photos | Same room: min | p10 | median | Wrong room: median | p90 | max |
| --- | --- | --- | --- | --- | --- | --- |
| 24 | 0.49 | 0.89 | 1.00 | 0.00 | 0.04 | 0.23 |
| 12 | 0.48 | 0.87 | 1.00 | 0.00 | 0.06 | 0.97 |
| 8 | 0.30 | 0.86 | 1.00 | 0.00 | 0.07 | 0.39 |
