# Position fix on a person walking through a real room (TUM RGB-D)

Measured on 14 November 2026 on one Iridis L4 GPU: 938 position fixes. `locate.py` had so far been
measured only on PASLCD (`LOCATE_RESULTS.md`, `LOCATE_LOOKALIKE_RESULTS.md`): photos taken standing
still, no motion blur, no real metres. This test uses a handheld camera carried through three
rooms, with motion blur, true camera poses and true metres.

## Short answer

1. **When the fix says "found", the position and the direction are right.** 453 found fixes in three
   rooms: position error 0.03 m (median), 0.10 m (90th percentile), 0.19 m (worst); heading error
   1.0, 5.3 and 7.9 degrees. No wrong room was accepted (0 of 178).
2. **It says "lost" or "unsure" too often: 16% of the same-room views** (84 of 537), and 26% in the
   cluttered office. In 58 of those 84 the position was right anyway. It happens when the wearer
   looks where the baseline clip did not look, and when the head turns fast.
3. **The metres are wrong, always in the same direction: the code's metres are 24% to 54% too
   long.** Its 2 m guess for the typical viewing distance should have been 1.3 to 1.6 m in these
   rooms. With the tilt of the map on top, a place that is truly 2 m away is shown as "about 2.6 m"
   in the best room and "about 3.3 m" in the worst.
4. The bearing ("slightly left") is good in two rooms (1 to 2 degrees median error) and coarse in
   the cluttered office (9 degrees median, 25 at the 90th percentile).
5. One fix takes 2.5 s on the GPU (12 baseline frames + 1 frame of the wearer).

| 12 baseline frames + 1 query frame | fr1_office | fr2_desk | fr3_office | All rooms |
| --- | --- | --- | --- | --- |
| Same-room queries | 180 | 180 | 177 | 537 |
| Found | 133 (74%) | 175 (97%) | 145 (82%) | 453 (84%) |
| Unsure | 5 (3%) | 5 (3%) | 9 (5%) | 19 (4%) |
| Lost | 42 (23%) | 0 | 23 (13%) | 65 (12%) |
| Wrong-room queries accepted (found or unsure) | 0 of 60 | 0 of 60 | 0 of 58 | 0 of 178 |
| Position error of found fixes, m: median / 90th percentile / worst | 0.08 / 0.14 / 0.19 | 0.01 / 0.04 / 0.06 | 0.02 / 0.05 / 0.12 | 0.03 / 0.10 / 0.19 |
| Heading error of found fixes, degrees | 4.5 / 6.4 / 7.9 | 0.8 / 1.3 / 1.9 | 0.7 / 1.4 / 3.7 | 1.0 / 5.3 / 7.9 |
| Position error of every same-room query, also unsure and lost, m | 0.10 / 0.33 / 1.06 | 0.01 / 0.04 / 0.06 | 0.03 / 0.08 / 0.15 | 0.03 / 0.13 / 1.06 |
| Heading error of every same-room query, degrees | 4.8 / 7.1 / 40.4 | 0.8 / 1.3 / 1.9 | 0.7 / 1.5 / 3.7 | 1.0 / 5.9 / 40.4 |
| The code's metres / true metres | 1.54 | 1.24 | 1.31 | 1.31 |
| Error of the shown distance (found fixes), m | 1.44 / 2.11 / 2.49 | 0.46 / 0.66 / 0.85 | 0.67 / 1.10 / 1.24 | 0.63 / 1.66 / 2.49 |
| The same as a share of the true distance, median | 63% | 29% | 33% | 34% |
| Error of the shown bearing (found fixes), degrees | 9 / 25 / 35 | 2 / 7 / 23 | 1 / 3 / 9 | 2 / 13 / 35 |

Position and heading error are measured after fitting the model's baseline cameras to the truth,
so they do not contain the metres error. The shown distance and bearing are what `locate()` returns,
untouched, so they contain everything.

## What was tested

**The fix.** One call of `recon_change.reconstruct` on 12 baseline frames plus 1 frame of the wearer,
then `locate.locate`, assembled the way `live/worker.py` `where()` does it. Code under test: branch
`jun-22-hud-waypoints` at commit 11ff58e (`locate.py` and `recon_change.py` unchanged). Only
`locate()` was tested, not the relay, the gateway or the worker.

**Data.** TUM RGB-D (Sturm et al., IROS 2012, CC BY 4.0, plain downloads, `fetch_tum.py`). A person
carries a Kinect through a room: 640x480 colour at 30 frames a second with real motion blur, a
depth picture in true metres for every frame, and the camera's true pose from motion capture.
In the ground-truth frame z points up and the floor is at z = 0 within 5 cm (checked on the Kinect
points, `locate_walk_eval.py --check`).

**Rooms and recordings.**

| Room | Baseline recording | Query frames come from | How the camera moves in the queries |
| --- | --- | --- | --- |
| fr1_office: a cluttered office, the baseline walks once around all of it in 44 s | `freiburg1_room` | four other recordings of the same office (`freiburg1_desk`, `desk2`, `360`, `xyz`) and held-out frames of the baseline recording, one fifth each | fast: turning 32 deg/s (median), 64 deg/s (90th percentile), 0.34 m/s |
| fr2_desk: two desks in a large hall, the baseline circles them in 97 s | `freiburg2_desk` | another recording (`freiburg2_xyz`) and held-out frames, half each | slow: 4 deg/s, 14 deg/s, 0.10 m/s |
| fr3_office: two desks with a partition, the baseline circles them in 84 s | `freiburg3_long_office_household` | held-out frames only | medium: 12 deg/s, 22 deg/s, 0.27 m/s |

Two recordings of a room can be compared only if their ground truth uses one world frame. This was
checked, not assumed: Kinect depth of one recording, moved into the other recording's camera with
the ground-truth poses, lands on that camera's Kinect depth for 72% to 90% of the pixels, the same
as between two frames one second apart in a single recording (78% to 90%). So the freiburg1
recordings share a frame, and so do the two freiburg2 recordings.

**Baseline.** 24 frames per room, picked like `recon_change.sample_frames` picks from a video (24
stretches, four candidate frames in each, the sharpest wins), stored as JPEG quality 92 like the
worker does. A fix uses 12 of the 24, evenly spread, as `where()` does.

**Places.** Five per room, each one pixel in one of the 12 baseline frames, on something with
texture and a steady Kinect depth between 0.5 and 3 m (a basket, a box, a shelf, a thing on the
floor and a mug in fr1; a toy, a carton, a watering can on the floor, a can and a book in fr2; a
magazine, two keyboards, a bag and a pen in fr3). The true position of a place is the Kinect depth
at that pixel moved into the world with the true pose of that frame. They were picked by a rule and
then looked at, not clicked by hand.

**Truth for each number.**
- Position and heading error: the model's 12 baseline cameras are fitted to their true positions
  with one similarity transform per fix (scale, rotation, shift). The query camera is then compared
  with its true pose: distance in metres, and the angle between the two viewing directions.
- What the display shows: `distance_m` and `bearing_deg` exactly as `locate()` returns them, against
  the true horizontal distance and the true bearing (0 ahead, + to the right, around the true
  vertical) from the wearer's true pose to the place's true position.
- Metres ratio: the code's metres divided by true metres, from the scale of that same fit.
- Blur: `recon_change.sharpness` of the query frame (variance of the Laplacian), and, because that
  number is also low for a sharp picture of a plain surface, the camera's true turning speed from
  the motion capture.

## What had to change from the brief

- **fr3 has no second recording with public ground truth** (the `_validation` recording comes
  without poses). Its queries are held-out frames of the baseline recording.
- **"A few seconds away from every baseline frame" was not possible.** 24 baseline frames spread
  over a 44 to 97 s recording leave no such gap. Held-out frames are at least 2.5 s (fr1: 1.5 s)
  from each of the 12 baseline frames a fix uses and at least 0.5 s from the 12 other kept ones.
  What matters is reported directly: the true distance and viewing angle to the nearest baseline
  camera.
- **The query frames of one room are neighbours in a handful of recordings**, so they are not
  independent samples. Read the percentages as "about", not to the last digit.
- **Places were picked by a rule**, see above.
- TUM ships single frames, not a video, so the baseline rule of `sample_frames` was applied to the
  list of frames instead of calling it on a file.
- The 3-frame variant uses the frames 1.0 s and 0.5 s before the query frame plus the query frame.

## Where it breaks

All numbers are for 12 baseline frames + 1 query frame. The full tables are at the end.

**Looking where the baseline did not look.** This is the strongest effect. The angle between the
wearer's viewing direction and the closest viewing direction among the 12 baseline cameras:

| Angle to the closest baseline view | Queries | Found | Position error m, median / worst |
| --- | --- | --- | --- |
| under 15 degrees | 302 | 285 (94%) | 0.02 / 0.26 |
| 15 to 30 degrees | 200 | 163 (82%) | 0.05 / 0.75 |
| 30 to 50 degrees | 21 | 5 (24%) | 0.15 / 1.06 |
| 50 degrees and more | 14 | 0 | 0.54 / 1.01 |

All 35 queries beyond 30 degrees are in the office (fr1), where 12 frames cannot cover every
direction of a whole room. There the model's position really is wrong (up to 1.06 m), and "lost"
is the right answer. Eleven of the twelve worst position errors of the whole test are such views
(the twelfth is 28 degrees away), and all twelve were reported as lost with a match of 0.00.

**Turning the head fast (motion blur).**

| Turning speed when the frame was taken | Queries | Found | Found, only queries within 20 degrees of a baseline view |
| --- | --- | --- | --- |
| under 15 deg/s | 320 | 300 (94%) | 258 of 270 (96%) |
| 15 to 40 deg/s | 145 | 111 (77%) | 83 of 92 (90%) |
| 40 deg/s and more | 71 | 42 (59%) | 31 of 39 (79%) |

The right-hand column takes the first effect out: fast turns happen mostly in the office, in the
recordings that also look in new directions. With coverage held fixed, fast turning still costs
about one found fix in six. Blur lowers the match score, not the accuracy: among those covered
queries the worst position error is 0.26 m at any speed. Frames that are visibly smeared (sharpness
under 150) were found in 2 of 13 cases.

By sharpness of the query frame (`recon_change.sharpness`, thirds within each room): found 73% in the
blurriest third, 85% in the middle, 94% in the sharpest. Read this with care: the number is also
low for a sharp close-up of a plain thing. In fr3 most of the "blurry" misses are sharp close-ups (a
teddy bear filling the picture, a side table that one baseline frame saw), not blur.

**Distance from the baseline cameras does not matter in the range tested.** The query camera was
0.02 to 1.25 m from the nearest baseline camera. Found rate is 84%, 81%, 86% and 86% in the bands
under 0.2 m, 0.2 to 0.4, 0.4 to 0.8 and above 0.8 m, and the position error does not grow. Farther
than 1.25 m was not tested.

**Turned away for nothing.** Of the 84 same-room queries that were not found, 58 had the right
position (within 0.2 m and 10 degrees): 21 of 47 in fr1, 5 of 5 in fr2, 32 of 32 in fr3. The match
score asks that two baseline frames see a pixel at the same depth and looking alike. In the fr3
misses one baseline frame does so for about 80% of the query's pixels, two for only 12% to 30%.

**The office map is bent with 12 frames.** In fr1 the model's 12 baseline cameras fit their true
positions only to 0.17 m (fr2: 0.015 m, fr3: 0.04 m), and every one of them points about 5 degrees
off. That is where fr1's 4.5 degree heading error comes from. With 24 baseline frames the fit is
0.09 m and the heading error 1.6 degrees.

**The map's "up" is tilted.** `locate.py` takes "up" from the average of the baseline cameras. A
camera that mostly looks down at desks from one side tilts it: 19 degrees off the true vertical in
fr1, 11 in fr2, 8 in fr3. This is the main source of the bearing error in fr1.

## 12 or 24 baseline frames, 1 or 3 query frames

The same 89 query frames each way (30, 30 and 29 per room).

| Setting | Found | Unsure | Lost | Position error m, median / 90th | Heading error deg, median / 90th | Seconds per fix |
| --- | --- | --- | --- | --- | --- | --- |
| 12 baseline + 1 query | 71 (80%) | 6 | 12 | 0.04 / 0.13 | 0.9 / 5.7 | 2.5 |
| 24 baseline + 1 query | 80 (90%) | 0 | 9 | 0.03 / 0.10 | 0.7 / 1.9 | 5.2 |
| 12 baseline + 3 query | 72 (81%) | 4 | 13 | 0.03 / 0.12 | 0.9 / 5.7 | 2.9 |

24 frames find every query in fr2 (28 to 30 of 30) and fr3 (23 to 29 of 29). In fr1 they do not
help the found rate (20 to 21 of 30: the nine lost views are not covered by 24 frames either), but
they cut the heading error from 4.9 to 1.6 degrees. Wrong rooms accepted with 24 frames: 0 of 45
(highest match 0.15; with 12 frames 0.06). Three query frames instead of one change nothing.

Seconds per fix (VGGT plus `locate()`, model already loaded, pictures already read): 2.20 + 0.20 for
12 + 1, 2.60 + 0.25 for 12 + 3, 4.70 + 0.35 for 24 + 1. Peak GPU memory 9.3 to 9.9 GB.

## Fixes that were measured

Nothing below changes what runs today. `locate.py` got one new parameter, `need`, whose default
keeps the old behaviour.

### Metres

What the same 453 found fixes would have shown with another rule for metres:

| Rule | Its metres / true metres: median (10th to 90th percentile) | Fixes with metres within 15% of the truth | Error of the shown distance m: median / 90th / worst |
| --- | --- | --- | --- |
| as shipped (`typical_m=2.0`) | 1.31 (1.24 to 1.55) | 0 of 453 | 0.63 / 1.66 / 2.49 |
| `typical_m=1.5` | 0.98 (0.93 to 1.16) | 361 (80%) | 0.11 / 0.63 / 1.07 |
| one known distance: the true distance between two places is given | 0.99 (0.95 to 1.00) | 453 (100%) | 0.08 / 0.22 / 0.63 |
| floor found in the points, cameras assumed 1.6 m above it | 1.09 (1.03 to 1.11) | 415 (92%) | 0.21 / 0.47 / 2.99 |
| the same with the true camera height (1.43 to 1.49 m) | 1.00 (0.92 to 1.03) | 418 (92%) | 0.10 / 0.30 / 2.27 |
| perfect metres, for comparison | 1.00 | 453 (100%) | 0.09 / 0.22 / 0.62 |

- **One known distance is as good as perfect metres**, in every room. The carer would give one
  number once ("from the pill box to the door is 3.2 m"); each fix then scales itself so that this
  distance comes out right.
- **`typical_m=1.5` needs no input and no new code** (the parameter exists), and removes most of the
  error in fr2 and fr3. In the office it still shows 16% too much. It is fitted to these three
  rooms, which are all desks seen from 1 to 2.5 m. A large living room will need another number.
- **The floor rule is not reliable enough.** It works in fr2 and fr3, where the floor is open (every
  fix within 15% of the truth). In the cluttered office the floor is 6% of the points and the
  search picks another surface in 29% of the fixes (38 of 133 outside 15%, shown distances up to
  3 m off). It also needs the wearer's eye height.
- Taking the floor's normal as the map's "up" instead of the cameras' average tilt lowers the
  bearing error in the office from 9 to 3 degrees (median) and from 25 to 11 (90th percentile),
  with the same caveat: only where the floor is found.

### Fewer needless "lost": `locate(..., need=1)`

The shipped rule counts a pixel of the wearer's view when two baseline frames see it at the same
depth and looking alike. With `need=1` one baseline frame is enough (the yardstick, baseline frames
against each other, uses the same rule).

| | Shipped (`need=2`) | `need=1` |
| --- | --- | --- |
| TUM, same room: found | 454 of 537 (85%) | 501 of 537 (93%) |
| per room: fr1 / fr2 / fr3 | 133 / 175 / 145 | 147 / 177 / 177 (of 180 / 180 / 177) |
| TUM, wrong room accepted | 0 of 178 (highest score 0.06) | 0 of 178 (highest score 0.09) |
| TUM, found fixes off by more than 0.2 m or 10 degrees | 0 of 454 | 4 of 501 (0.23, 0.26, 0.37 and 0.75 m, all in fr1) |
| TUM, worst found fix | 0.19 m, 7.9 degrees | 0.75 m, 13.1 degrees |
| PASLCD, same room: found (8, 12 and 24 baseline photos) | 267 of 270 | 268 of 270 |
| PASLCD, wrong room: lost | 540 of 540 (highest score 0.12) | 540 of 540 (highest score 0.18) |

So `need=1` turns 47 of the 84 misses into fixes and keeps every wrong room out, on both
datasets, at the price of 4 poor fixes in 501. The TUM column for `need=1` is computed from the
parts of the score saved with each fix (the same arithmetic as `locate(need=1)`; the "shipped"
column recomputed this way gives 455 against 454 from `locate()` itself). The PASLCD rows are
`locate()` run again on the saved reconstructions; the `need=2` row reproduces the published
267 of 270 and 540 of 540. (454 rather than 453 in this table: one fix scored just under 0.5,
prints as 0.50 and was reported as unsure.)

## What this means for the demo

- **Trust the arrow when the state is "found".** Half the found fixes are within 3 cm and 1 degree,
  the worst of 453 is 19 cm and 8 degrees off, handheld and with motion blur. No wrong room got
  through.
- **Do not show the metres as they are.** They are 24% to 54% too long. Three ways out, best first:
  give one known distance per room (within 5% in every room here, needs a few lines in
  `locate.py` and one number from the carer); or pass `typical_m=1.5` (no new code, within 7% in
  two rooms and 16% in the third, fitted to these rooms); or show words ("close", "a few steps")
  instead of metres.
- **Expect "lost" on about one look in six**, more in a cluttered room, when the head turns fast,
  or when the wearer faces a direction the baseline clip skipped. Two thirds of those are false
  alarms. What helps, by the numbers above: record the baseline slowly and sweep every direction
  the wearer may face; ask for the fix when the head is still (94% found under 15 deg/s); do not
  alarm on a single "lost".
- **24 baseline frames are better if 5 s per fix is acceptable** (`RECON_LOCATE_BASELINE=24`): found
  80% to 90% on the same queries, heading error at the 90th percentile 5.7 to 1.9 degrees. Sending
  3 frames of the wearer instead of 1 buys nothing.
- **`need=1` is there to try** if "lost" hurts the demo more than an arrow that is half a metre off
  once in a hundred fixes. It is not switched on.
- **"Slightly left" can be one notch off in a cluttered room** (bearing error 9 degrees median, 25 at
  the 90th percentile in fr1; 1 to 2 and 3 to 7 in the other two rooms).

## What was not tested

- **The glasses.** A Kinect colour camera held in the hand at 1.2 to 1.9 m is not a glasses camera
  on a head: other lens, other field of view, other motion (a head can turn much faster than the
  64 deg/s that 90% of the fr1 queries stay under).
- **The live path.** Only `locate()` on frames read from disk. Not the relay's frame picking, not
  JPEG from a phone for the query frame, not `crop_like` (all frames here have one shape), not the
  HUD.
- **A wearer far from the baseline path.** The query camera was never more than about 1.2 m from
  the nearest baseline camera. Walking into a part of the room the baseline did not visit is
  untested.
- **A changed room.** The recordings of a room were made close together in time: same light, same
  furniture, almost the same clutter. PASLCD (`LOCATE_RESULTS.md`) covers other lighting; nothing
  covers moved furniture.
- **People in view.** One fr1 baseline frame shows a person; no query does. TUM has recordings with
  people walking through (`freiburg3_walking_*`); they were not used.
- **Homes.** Three rooms, all desks in an office or a lab hall, all seen from 1 to 2.5 m. No
  corridor, no kitchen, no large living room, no bare walls. The metres ratio in particular is
  measured on three scenes of one kind.
- **Many wrong rooms.** Wrong-room queries come from only two other rooms each. PASLCD (540 wrong-room
  photos, all rejected) remains the main evidence that a wrong room is turned away.
- **Lens distortion.** The freiburg1 and freiburg2 Kinects have some; it was ignored (TUM
  recommends that for the depth pictures). It moves the truth of a place by a few centimetres at
  most, and may add to the model's errors in those two rooms.
- **The floor rule inside `locate.py`.** It was measured offline on the saved points
  (`floor_plane` in `locate_walk_tables.py`) and is not wired in.

## How to run it again

On Iridis, in a folder `walk/` next to `.venv-recon` (`walk/code/` holds `fetch_tum.py`,
`locate_walk_eval.py`, `locate_walk_tables.py`, `locate_walk.slurm` and the `locate.py` and
`recon_change.py` under test):

```
../.venv-recon/bin/python -I code/fetch_tum.py --out data/tum                 # login node, 8 GB, about a minute
../.venv-recon/bin/python -I code/locate_walk_eval.py --data data/tum --check  # do the recordings share a frame?
../.venv-recon/bin/python -I code/locate_walk_eval.py --data data/tum --plan out/plan.json --make-plan \
    --shared freiburg1_desk freiburg1_desk2 freiburg1_360 freiburg1_xyz freiburg2_xyz
sbatch code/locate_walk.slurm --out out      # one L4 GPU, 26 minutes; submit again to continue
OMP_NUM_THREADS=2 ../.venv-recon/bin/python -I code/locate_walk_tables.py --plan out/plan.json --out out --detector-dir code
```

The PASLCD numbers for `need=1` come from the reconstructions saved earlier under `loclook/dump`
(`locate_dump.slurm`), scored again on CPU cores with `locate_eval.py --from-dump` (as
`locate_rescore.slurm` does), once with this branch's `locate.py` and once with a copy whose
default is `need=1`.

Nothing from the dataset is in git: no pictures, no depth, no `.npz`.

## Full tables

Written by `locate_walk_tables.py` from the 938 fixes. "12 + 1" means 12 baseline frames and 1 query frame. p90 is the 90th percentile, p10 the 10th.

Fixes: 938. Thresholds: found at match >= 0.5, lost below 0.3. GPU: NVIDIA L4. Code under test: `locate.py` and `recon_change.py` as on `jun-22-hud-waypoints` (11ff58e).

### 1. Is the wearer found? (12 baseline frames, 1 query frame)

| Room | Same-room queries | Found | Unsure | Lost | Match median | Match min | Wrong-room queries | Wrong room accepted (found or unsure) | Wrong-room match max |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fr1_office | 180 | 133/180 (74%) | 5/180 (3%) | 42/180 (23%) | 1.00 | 0.00 | 60 | 0/60 (0%) | 0.00 |
| fr2_desk | 180 | 175/180 (97%) | 5/180 (3%) | 0/180 (0%) | 0.99 | 0.45 | 60 | 0/60 (0%) | 0.06 |
| fr3_office | 177 | 145/177 (82%) | 9/177 (5%) | 23/177 (13%) | 1.00 | 0.14 | 58 | 0/58 (0%) | 0.00 |
| **All rooms** | 537 | 453/537 (84%) | 19/537 (4%) | 65/537 (12%) | 1.00 | 0.00 | 178 | 0/178 (0%) | 0.06 |

Match scores, and what other rules would have given on the same fixes (12 + 1). The shipped rule counts a pixel of the query when at least two baseline frames see it at the same depth and looking alike. Found is a score of at least 0.5, accepted at least 0.3.

| Rule | Same room: score median | p10 | Found | Accepted | Wrong room: score median | max | Accepted | Same-room queries scoring above every wrong-room query | Its found fixes: position error m p90 | max | heading error deg max | off by more than 0.2 m or 10 deg |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| shipped (`confidence`) | 1.00 | 0.21 | 454/537 (85%) | 472/537 (88%) | 0.00 | 0.06 | 0/178 (0%) | 498/537 (93%) | 0.10 | 0.19 | 7.9 | 0/454 (0%) |
| shipped, recomputed from its parts (check) | 1.00 | 0.21 | 455/537 (85%) | 472/537 (88%) | 0.00 | 0.06 | 0/178 (0%) | 499/537 (93%) | 0.10 | 0.19 | 7.9 | 0/455 (0%) |
| one baseline frame is enough (same depth and looking alike) | 1.00 | 0.61 | 501/537 (93%) | 507/537 (94%) | 0.00 | 0.09 | 0/178 (0%) | 509/537 (95%) | 0.11 | 0.75 | 13.1 | 4/501 (1%) |
| depth only, two baseline frames (`match_by_depth`) | 1.00 | 0.51 | 484/537 (90%) | 497/537 (93%) | 0.00 | 0.35 | 2/178 (1%) | 493/537 (92%) | 0.11 | 0.19 | 7.9 | 0/484 (0%) |
| depth only, one baseline frame | 1.00 | 0.61 | 500/537 (93%) | 507/537 (94%) | 0.00 | 0.42 | 1/178 (1%) | 506/537 (94%) | 0.11 | 0.68 | 10.5 | 4/500 (1%) |

Of the 84 same-room queries that were not found, 58 had the right position anyway (within 0.2 m and 10 degrees): turned away for nothing. Per room: fr1_office 21 of 47, fr2_desk 5 of 5, fr3_office 32 of 32.

Parts of the score, medians: share of the frame's trusted pixels that at least 1 / at least 2 baseline frames see at the same depth, and where at least 1 / at least 2 also look alike.

| Room | Frame | Depth, 1 frame | Depth, 2 frames | Alike, 1 frame | Alike, 2 frames |
| --- | --- | --- | --- | --- | --- |
| fr1_office | same-room query | 0.85 | 0.61 | 0.72 | 0.43 |
| fr1_office | baseline frame against the other baseline frames (the yardstick) | 0.51 | 0.16 | 0.37 | 0.07 |
| fr1_office | wrong-room query | 0.00 | 0.00 | 0.00 | 0.00 |
| fr2_desk | same-room query | 0.96 | 0.89 | 0.94 | 0.83 |
| fr2_desk | baseline frame against the other baseline frames (the yardstick) | 0.96 | 0.85 | 0.90 | 0.76 |
| fr2_desk | wrong-room query | 0.00 | 0.00 | 0.00 | 0.00 |
| fr3_office | same-room query | 0.91 | 0.76 | 0.84 | 0.59 |
| fr3_office | baseline frame against the other baseline frames (the yardstick) | 0.91 | 0.76 | 0.81 | 0.57 |
| fr3_office | wrong-room query | 0.00 | 0.00 | 0.00 | 0.00 |

By where the query frame comes from:

| Room | Query frames from | Queries | Found | Unsure | Lost | Position error m median | p90 | max | Heading error deg median | p90 | max |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fr1_office | the baseline recording, held out | 36 | 20/36 (56%) | 2 | 14 | 0.12 | 0.88 | 1.06 | 5.8 | 8.3 | 11.3 |
| fr1_office | another recording (freiburg1_desk) | 36 | 35/36 (97%) | 1 | 0 | 0.09 | 0.13 | 0.17 | 5.4 | 6.5 | 7.1 |
| fr1_office | another recording (freiburg1_desk2) | 36 | 34/36 (94%) | 1 | 1 | 0.10 | 0.14 | 0.18 | 5.0 | 7.0 | 7.9 |
| fr1_office | another recording (freiburg1_360) | 36 | 8/36 (22%) | 1 | 27 | 0.17 | 0.53 | 0.90 | 5.1 | 10.1 | 40.4 |
| fr1_office | another recording (freiburg1_xyz) | 36 | 36/36 (100%) | 0 | 0 | 0.04 | 0.06 | 0.19 | 2.0 | 3.8 | 4.8 |
| fr2_desk | the baseline recording, held out | 90 | 87/90 (97%) | 3 | 0 | 0.02 | 0.04 | 0.06 | 0.7 | 1.0 | 1.7 |
| fr2_desk | another recording (freiburg2_xyz) | 90 | 88/90 (98%) | 2 | 0 | 0.01 | 0.02 | 0.02 | 1.0 | 1.4 | 1.9 |
| fr3_office | the baseline recording, held out | 177 | 145/177 (82%) | 9 | 23 | 0.03 | 0.08 | 0.15 | 0.7 | 1.5 | 3.7 |

### 2. Is the position right? (same-room queries, 12 + 1, in true metres and degrees)

| Room | Fixes counted | Position error m median | p90 | max | Heading error deg median | p90 | max | Baseline fit m (RMS) median | Query to nearest baseline camera m median |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fr1_office, all | 180 | 0.10 | 0.33 | 1.06 | 4.8 | 7.1 | 40.4 | 0.174 | 0.28 |
| fr2_desk, all | 180 | 0.01 | 0.04 | 0.06 | 0.8 | 1.3 | 1.9 | 0.015 | 0.50 |
| fr3_office, all | 177 | 0.03 | 0.08 | 0.15 | 0.7 | 1.5 | 3.7 | 0.042 | 0.72 |
| **All rooms**, all | 537 | 0.03 | 0.13 | 1.06 | 1.0 | 5.9 | 40.4 | 0.042 | 0.45 |
| fr1_office, found only | 133 | 0.08 | 0.14 | 0.19 | 4.5 | 6.4 | 7.9 | 0.172 | 0.26 |
| fr2_desk, found only | 175 | 0.01 | 0.04 | 0.06 | 0.8 | 1.3 | 1.9 | 0.015 | 0.49 |
| fr3_office, found only | 145 | 0.02 | 0.05 | 0.12 | 0.7 | 1.4 | 3.7 | 0.042 | 0.74 |
| **All rooms**, found only | 453 | 0.03 | 0.10 | 0.19 | 1.0 | 5.3 | 7.9 | 0.041 | 0.47 |
| fr1_office, unsure or lost | 47 | 0.23 | 0.87 | 1.06 | 5.8 | 10.0 | 40.4 | 0.181 | 0.32 |
| fr2_desk, unsure or lost | 5 | 0.03 | 0.03 | 0.03 | 0.9 | 1.4 | 1.7 | 0.017 | 0.76 |
| fr3_office, unsure or lost | 32 | 0.08 | 0.12 | 0.15 | 0.6 | 2.6 | 3.7 | 0.043 | 0.52 |
| **All rooms**, unsure or lost | 84 | 0.12 | 0.74 | 1.06 | 3.3 | 8.7 | 40.4 | 0.136 | 0.41 |

### 3. What the display shows against the truth (found fixes, 12 + 1, the code's own metres)

Each fix gives a distance and a bearing to every place of the room. Bearing rows count only places at least 0.5 m away (true horizontal distance).

| Room | Fix-place pairs | True distance m median | Shown / true distance median | Distance error m median | p90 | max | Distance error % median | Bearing error deg median | p90 | max | Bearing off by more than 30 deg | Place position error m median |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fr1_office, found | 665 | 2.20 | 1.63 | 1.44 | 2.11 | 2.49 | 63 | 9 | 25 | 35 | 10/627 (2%) | 0.11 |
| fr2_desk, found | 875 | 1.54 | 1.29 | 0.46 | 0.66 | 0.85 | 29 | 2 | 7 | 23 | 0/875 (0%) | 0.07 |
| fr3_office, found | 725 | 2.17 | 1.33 | 0.67 | 1.10 | 1.24 | 33 | 1 | 3 | 9 | 0/725 (0%) | 0.02 |
| **All rooms**, found | 2265 | 1.94 | 1.34 | 0.63 | 1.66 | 2.49 | 34 | 2 | 13 | 35 | 10/2227 (0%) | 0.06 |
| fr1_office, every same-room fix, also unsure and lost | 900 | 2.26 | 1.63 | 1.46 | 2.11 | 2.96 | 63 | 9 | 27 | 176 | 44/862 (5%) | 0.11 |
| fr2_desk, every same-room fix, also unsure and lost | 900 | 1.56 | 1.29 | 0.46 | 0.66 | 0.88 | 29 | 2 | 7 | 23 | 0/900 (0%) | 0.07 |
| fr3_office, every same-room fix, also unsure and lost | 885 | 2.12 | 1.32 | 0.65 | 1.05 | 1.24 | 32 | 2 | 4 | 11 | 0/885 (0%) | 0.02 |
| **All rooms**, every same-room fix, also unsure and lost | 2685 | 1.99 | 1.34 | 0.65 | 1.71 | 2.96 | 34 | 3 | 16 | 176 | 44/2647 (2%) | 0.06 |

### 4. How wrong are the metres?

The code assumes the baseline's median viewing distance is 2 m. Ratio = the code's metres / true metres (1.00 would be right; 1.50 means a true 2 m is shown as 3 m).

| Room | Fixes | Ratio median | p10 | p90 | min | max | typical_m that would have been right | Kinect median depth of the baseline frames m | Tilt of the code's up axis from true vertical deg median |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fr1_office | 180 | 1.54 | 1.52 | 1.59 | 1.50 | 1.65 | 1.30 | 1.32 | 19.0 |
| fr2_desk | 180 | 1.24 | 1.24 | 1.25 | 1.23 | 1.26 | 1.61 | 1.70 | 10.8 |
| fr3_office | 177 | 1.31 | 1.31 | 1.32 | 1.31 | 1.32 | 1.52 | 1.65 | 8.2 |
| **All rooms** | 537 | 1.31 | 1.24 | 1.56 | 1.23 | 1.65 | 1.52 | - | 10.8 |

### 5. Where it breaks (same-room queries, 12 + 1, all rooms pooled)

By blur. Sharpness is `recon_change.sharpness` of the query frame (variance of the Laplacian); thirds within each room. Edges: fr1_office 508 and 882; fr2_desk 1084 and 1439; fr3_office 615 and 918.

| Sharpness of the query frame | Queries | Found | Unsure | Lost | Position error m median | p90 | max | Heading error deg median | p90 | max | Bearing error deg median (found) | Distance error m median, metres corrected (found) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| blurriest third | 179 | 131/179 (73%) | 5 | 43 | 0.05 | 0.15 | 1.01 | 0.9 | 6.4 | 40.4 | 3 | 0.06 |
| middle third | 179 | 153/179 (85%) | 6 | 20 | 0.02 | 0.12 | 1.06 | 1.0 | 6.0 | 11.3 | 2 | 0.09 |
| sharpest third | 179 | 169/179 (94%) | 8 | 2 | 0.03 | 0.09 | 0.75 | 1.1 | 4.8 | 13.1 | 3 | 0.11 |

By sharpness in absolute numbers, all rooms on one scale (a frame under 150 is visibly smeared; a plain close-up also scores low):

| Sharpness of the query frame | Queries | Found | Unsure | Lost | Position error m median | p90 | max | Heading error deg median | p90 | max | Bearing error deg median (found) | Distance error m median, metres corrected (found) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| under 150 | 13 | 2/13 (15%) | 0 | 11 | 0.16 | 0.37 | 0.90 | 3.7 | 6.6 | 40.4 | 17 | 0.11 |
| 150 to 400 | 57 | 36/57 (63%) | 2 | 19 | 0.11 | 0.23 | 0.83 | 3.6 | 7.3 | 9.8 | 4 | 0.06 |
| 400 to 800 | 154 | 112/154 (73%) | 9 | 33 | 0.05 | 0.14 | 1.06 | 0.9 | 6.3 | 11.3 | 2 | 0.08 |
| 800 and more | 313 | 303/313 (97%) | 8 | 2 | 0.02 | 0.07 | 0.75 | 0.9 | 4.4 | 13.1 | 2 | 0.10 |

By how fast the camera was turning when the frame was taken (from the motion capture):

| Turning speed deg/s | Queries | Found | Unsure | Lost | Position error m median | p90 | max | Heading error deg median | p90 | max | Bearing error deg median (found) | Distance error m median, metres corrected (found) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| under 15 | 320 | 300/320 (94%) | 6 | 14 | 0.02 | 0.05 | 0.37 | 0.8 | 1.6 | 10.5 | 2 | 0.08 |
| 15 to 40 | 145 | 111/145 (77%) | 9 | 25 | 0.06 | 0.18 | 1.06 | 2.1 | 6.4 | 13.1 | 4 | 0.10 |
| 40 to 80 | 66 | 38/66 (58%) | 3 | 25 | 0.12 | 0.34 | 1.01 | 5.2 | 7.6 | 40.4 | 5 | 0.13 |
| 80 and more | 5 | 4/5 (80%) | 0 | 1 | 0.10 | 0.13 | 0.15 | 5.2 | 5.6 | 5.6 | 6 | 0.16 |

By true distance from the query camera to the nearest baseline camera used in the fix:

| Distance to nearest baseline camera m | Queries | Found | Unsure | Lost | Position error m median | p90 | max | Heading error deg median | p90 | max | Bearing error deg median (found) | Distance error m median, metres corrected (found) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| under 0.2 | 49 | 41/49 (84%) | 2 | 6 | 0.07 | 0.16 | 0.75 | 3.9 | 6.9 | 13.1 | 10 | 0.12 |
| 0.2 to 0.4 | 171 | 139/171 (81%) | 3 | 29 | 0.06 | 0.16 | 0.90 | 2.2 | 6.3 | 40.4 | 4 | 0.09 |
| 0.4 to 0.8 | 251 | 216/251 (86%) | 7 | 28 | 0.02 | 0.11 | 1.06 | 0.9 | 2.9 | 11.3 | 2 | 0.08 |
| 0.8 and more | 66 | 57/66 (86%) | 7 | 2 | 0.02 | 0.05 | 0.06 | 0.7 | 1.3 | 1.5 | 1 | 0.10 |

By the angle between the query's viewing direction and the closest viewing direction among the baseline cameras:

| Angle to closest baseline view deg | Queries | Found | Unsure | Lost | Position error m median | p90 | max | Heading error deg median | p90 | max | Bearing error deg median (found) | Distance error m median, metres corrected (found) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| under 15 | 302 | 285/302 (94%) | 10 | 7 | 0.02 | 0.10 | 0.26 | 0.9 | 4.8 | 7.9 | 2 | 0.08 |
| 15 to 30 | 200 | 163/200 (82%) | 9 | 28 | 0.05 | 0.13 | 0.75 | 1.3 | 5.6 | 13.1 | 3 | 0.10 |
| 30 to 50 | 21 | 5/21 (24%) | 0 | 16 | 0.15 | 0.90 | 1.06 | 5.1 | 10.9 | 40.4 | 3 | 0.11 |
| 50 and more | 14 | 0/14 (0%) | 0 | 14 | 0.54 | 0.89 | 1.01 | 7.6 | 8.9 | 9.2 | - | - |

By how far down the camera looked (0 = level, 90 = straight down):

| Pitch deg | Queries | Found | Unsure | Lost | Position error m median | p90 | max | Heading error deg median | p90 | max | Bearing error deg median (found) | Distance error m median, metres corrected (found) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| under 15 | 30 | 10/30 (33%) | 3 | 17 | 0.15 | 0.35 | 0.75 | 5.4 | 7.0 | 13.1 | 3 | 0.12 |
| 15 to 30 | 169 | 144/169 (85%) | 11 | 14 | 0.02 | 0.11 | 1.06 | 0.8 | 5.6 | 11.3 | 1 | 0.09 |
| 30 to 45 | 290 | 257/290 (89%) | 5 | 28 | 0.03 | 0.11 | 1.01 | 0.9 | 4.8 | 9.2 | 2 | 0.08 |
| 45 and more | 48 | 42/48 (88%) | 0 | 6 | 0.06 | 0.15 | 0.90 | 4.4 | 6.4 | 40.4 | 15 | 0.14 |

Found rate per room, by blur (thirds within the room):

| Band | fr1_office: found | fr2_desk: found | fr3_office: found |
| --- | --- | --- | --- |
| blurriest third | 32/60 (53%) | 60/60 (100%) | 39/59 (66%) |
| middle third | 43/60 (72%) | 60/60 (100%) | 50/59 (85%) |
| sharpest third | 58/60 (97%) | 55/60 (92%) | 56/59 (95%) |

What blur does to the score (medians; `match_by_depth` is the score without the appearance check, `looks_alike` the share of depth-matched pixels that also look alike):

| Room | Blur band | Queries | match_by_depth | looks_alike | Match (shipped) | Position error m median | Not found although within 0.2 m and 10 deg |
| --- | --- | --- | --- | --- | --- | --- | --- |
| fr1_office | blurriest third | 60 | 1.00 | 0.49 | 0.91 | 0.13 | 14/60 (23%) |
| fr1_office | middle third | 60 | 1.00 | 0.66 | 1.00 | 0.10 | 7/60 (12%) |
| fr1_office | sharpest third | 60 | 1.00 | 0.80 | 1.00 | 0.06 | 0/60 (0%) |
| fr2_desk | blurriest third | 60 | 1.00 | 0.83 | 0.95 | 0.02 | 0/60 (0%) |
| fr2_desk | middle third | 60 | 1.00 | 0.95 | 1.00 | 0.01 | 0/60 (0%) |
| fr2_desk | sharpest third | 60 | 0.89 | 0.93 | 0.92 | 0.01 | 5/60 (8%) |
| fr3_office | blurriest third | 59 | 1.00 | 0.73 | 1.00 | 0.04 | 20/59 (34%) |
| fr3_office | middle third | 59 | 0.94 | 0.81 | 1.00 | 0.02 | 9/59 (15%) |
| fr3_office | sharpest third | 59 | 0.95 | 0.78 | 1.00 | 0.03 | 3/59 (5%) |

Found rate per room, by turning speed (deg/s):

| Band | fr1_office: found | fr2_desk: found | fr3_office: found |
| --- | --- | --- | --- |
| under 15 | 31/32 (97%) | 163/167 (98%) | 106/121 (88%) |
| 15 to 40 | 60/77 (78%) | 12/12 (100%) | 39/56 (70%) |
| 40 and more | 42/71 (59%) | - | - |

Found rate per room, by the angle to the closest baseline view (deg):

| Band | fr1_office: found | fr2_desk: found | fr3_office: found |
| --- | --- | --- | --- |
| under 15 | 52/56 (93%) | 151/155 (97%) | 82/91 (90%) |
| 15 to 30 | 76/89 (85%) | 24/25 (96%) | 63/86 (73%) |
| 30 and more | 5/35 (14%) | - | - |

Blur with coverage held fixed: only queries that look within 20 degrees of a baseline view.

| Band | Queries | Found | Unsure | Lost | Position error m median | max | Heading error deg median | max |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| turning under 15 deg/s | 270 | 258/270 (96%) | 6 | 6 | 0.02 | 0.14 | 0.8 | 6.8 |
| turning 15 to 40 deg/s | 92 | 83/92 (90%) | 5 | 4 | 0.05 | 0.23 | 1.7 | 6.6 |
| turning 40 deg/s and more | 39 | 31/39 (79%) | 2 | 6 | 0.10 | 0.26 | 4.8 | 7.9 |
| sharpness under 150 | 2 | 2/2 (100%) | 0 | 0 | 0.06 | 0.07 | 1.3 | 2.4 |
| sharpness 150 to 400 | 32 | 28/32 (88%) | 0 | 4 | 0.08 | 0.26 | 2.9 | 7.9 |
| sharpness 400 and more | 368 | 342/368 (93%) | 14 | 12 | 0.02 | 0.26 | 0.9 | 7.1 |

Range tested: the query camera was 0.02 to 1.25 m from the nearest baseline camera (median 0.45), turning at up to 95 deg/s and moving at up to 0.88 m/s.

By match score:

| Match | Queries | Found | Unsure | Lost | Position error m median | p90 | max | Heading error deg median | p90 | max | Bearing error deg median (found) | Distance error m median, metres corrected (found) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| under 0.3 (lost) | 65 | 0/65 (0%) | 0 | 65 | 0.13 | 0.80 | 1.06 | 4.0 | 9.1 | 40.4 | - | - |
| 0.3 to 0.5 (unsure) | 18 | 0/18 (0%) | 18 | 0 | 0.04 | 0.12 | 0.26 | 0.8 | 5.3 | 6.5 | - | - |
| 0.5 to 0.8 | 71 | 70/71 (99%) | 1 | 0 | 0.02 | 0.05 | 0.16 | 0.7 | 1.7 | 5.8 | 2 | 0.10 |
| 0.8 and more | 383 | 383/383 (100%) | 0 | 0 | 0.03 | 0.11 | 0.19 | 1.0 | 5.5 | 7.9 | 2 | 0.09 |

The twelve worst position errors (12 + 1):

| Room | Query frame | State | Match | Position error m | Heading error deg | Nearest baseline camera m | Angle to closest view deg | Sharpness | Turning deg/s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fr1_office | freiburg1_room/rgb/1305031933.968843.png | lost | 0.00 | 1.06 | 7.1 | 0.53 | 44 | 637 | 29 |
| fr1_office | freiburg1_room/rgb/1305031933.732901.png | lost | 0.00 | 1.01 | 11.3 | 0.55 | 49 | 667 | 56 |
| fr1_office | freiburg1_room/rgb/1305031932.333175.png | lost | 0.00 | 1.01 | 7.3 | 0.42 | 59 | 461 | 30 |
| fr1_office | freiburg1_room/rgb/1305031931.669064.png | lost | 0.00 | 0.90 | 8.4 | 0.35 | 56 | 790 | 23 |
| fr1_office | freiburg1_360/rgb/1305031814.285406.png | lost | 0.00 | 0.90 | 40.4 | 0.32 | 39 | 56 | 64 |
| fr1_office | freiburg1_room/rgb/1305031931.768911.png | lost | 0.00 | 0.85 | 8.9 | 0.35 | 55 | 573 | 24 |
| fr1_office | freiburg1_room/rgb/1305031932.132895.png | lost | 0.00 | 0.83 | 8.0 | 0.40 | 55 | 396 | 18 |
| fr1_office | freiburg1_360/rgb/1305031815.317708.png | lost | 0.00 | 0.75 | 13.1 | 0.15 | 28 | 910 | 31 |
| fr1_office | freiburg1_360/rgb/1305031798.349307.png | lost | 0.00 | 0.75 | 10.9 | 0.25 | 42 | 527 | 61 |
| fr1_office | freiburg1_room/rgb/1305031932.232821.png | lost | 0.00 | 0.72 | 9.2 | 0.41 | 58 | 296 | 29 |
| fr1_office | freiburg1_room/rgb/1305031933.600899.png | lost | 0.00 | 0.72 | 8.3 | 0.55 | 50 | 780 | 53 |
| fr1_office | freiburg1_room/rgb/1305031932.000829.png | lost | 0.00 | 0.70 | 7.9 | 0.38 | 55 | 765 | 22 |

### 6. 12 or 24 baseline frames, 1 or 3 query frames

Baseline frames. The same 89 query frames both ways.

| Setting | Found | Unsure | Lost | Match median | Position error m median | p90 | max | Heading error deg median | p90 | max | Ratio code metres / true metres median | Model s median | Locate s median | Whole fix s median |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 12 baseline frames | 71/89 (80%) | 6 | 12 | 0.95 | 0.04 | 0.13 | 1.06 | 0.9 | 5.7 | 13.1 | 1.31 | 2.20 | 0.20 | 2.48 |
| 24 baseline frames | 80/89 (90%) | 0 | 9 | 0.98 | 0.03 | 0.10 | 0.50 | 0.7 | 1.9 | 20.8 | 1.27 | 4.70 | 0.36 | 5.21 |

Query frames (3 = the frames 1.0 s and 0.5 s earlier plus the query frame; the newest is the wearer). The same 89 query frames both ways.

| Setting | Found | Unsure | Lost | Match median | Position error m median | p90 | max | Heading error deg median | p90 | max | Ratio code metres / true metres median | Model s median | Locate s median | Whole fix s median |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 query frame | 71/89 (80%) | 6 | 12 | 0.95 | 0.04 | 0.13 | 1.06 | 0.9 | 5.7 | 13.1 | 1.31 | 2.20 | 0.20 | 2.48 |
| 3 query frames | 72/89 (81%) | 4 | 13 | 0.92 | 0.03 | 0.12 | 0.96 | 0.9 | 5.7 | 9.1 | 1.31 | 2.60 | 0.25 | 2.91 |

Wrong-room queries accepted: 0/178 (0%) with 12 baseline frames, 0/45 (0%) with 24. Highest wrong-room match: 0.06 and 0.15.

### 7. Seconds per fix on the GPU

| Frames (baseline + query) | Fixes | Model s median | p90 | Locate s median | p90 | Model + locate s median | Peak GPU GB |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 12 + 1 | 715 | 2.20 | 2.20 | 0.20 | 0.21 | 2.47 | 9.3 |
| 12 + 3 | 89 | 2.60 | 2.60 | 0.25 | 0.27 | 2.91 | 9.4 |
| 24 + 1 | 134 | 4.70 | 4.70 | 0.35 | 0.37 | 5.20 | 9.9 |

Model s is VGGT inference as printed by `reconstruct`; locate s is `locate.locate`. Loading the model (once) and reading the pictures are not included.

### 8. Other ways to get metres (found fixes, 12 + 1)

Nothing here is in `locate.py`; each row is what the same fixes would have shown with another rule for metres. "Ratio" is that rule's metres / true metres.

| Room | Rule | Fixes | Ratio median | p10 | p90 | Fixes with metres within 15% of the truth | Distance error m median | p90 | max | Distance error % median | Bearing error deg median | p90 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fr1_office | as shipped: median viewing distance = 2 m | 133 | 1.54 | 1.52 | 1.59 | 0/133 (0%) | 1.44 | 2.11 | 2.49 | 63 | 9 | 25 |
| fr1_office | median viewing distance = 1.5 m (`typical_m=1.5`) | 133 | 1.16 | 1.14 | 1.19 | 41/133 (31%) | 0.50 | 0.82 | 1.07 | 23 | 9 | 25 |
| fr1_office | one known distance (the two places farthest apart) | 133 | 1.00 | 0.99 | 1.01 | 133/133 (100%) | 0.13 | 0.36 | 0.63 | 6 | 9 | 25 |
| fr1_office | floor found, cameras assumed 1.6 m above it (scale only) | 133 | 1.06 | 0.99 | 1.56 | 95/133 (71%) | 0.30 | 1.49 | 2.99 | 16 | 9 | 25 |
| fr1_office | floor found, the true camera height given (scale only) | 133 | 0.95 | 0.89 | 1.40 | 98/133 (74%) | 0.18 | 1.08 | 2.27 | 9 | 9 | 25 |
| fr1_office | floor found, 1.6 m, and the floor's normal as the up axis | 133 | 1.06 | 0.99 | 1.56 | 95/133 (71%) | 0.14 | 1.49 | 3.02 | 8 | 3 | 11 |
| fr1_office | floor found, true camera height, floor's normal as up | 133 | 0.95 | 0.89 | 1.40 | 98/133 (74%) | 0.21 | 1.09 | 2.30 | 10 | 3 | 11 |
| fr1_office | perfect metres (the true scale of each fix) | 133 | 1.00 | 1.00 | 1.00 | 133/133 (100%) | 0.14 | 0.37 | 0.62 | 7 | 9 | 25 |
| fr2_desk | as shipped: median viewing distance = 2 m | 175 | 1.25 | 1.24 | 1.25 | 0/175 (0%) | 0.46 | 0.66 | 0.85 | 29 | 2 | 7 |
| fr2_desk | median viewing distance = 1.5 m (`typical_m=1.5`) | 175 | 0.93 | 0.93 | 0.94 | 175/175 (100%) | 0.07 | 0.21 | 0.41 | 5 | 2 | 7 |
| fr2_desk | one known distance (the two places farthest apart) | 175 | 0.95 | 0.95 | 0.96 | 175/175 (100%) | 0.07 | 0.19 | 0.38 | 4 | 2 | 7 |
| fr2_desk | floor found, cameras assumed 1.6 m above it (scale only) | 175 | 1.10 | 1.10 | 1.10 | 175/175 (100%) | 0.21 | 0.36 | 0.49 | 13 | 2 | 7 |
| fr2_desk | floor found, the true camera height given (scale only) | 175 | 1.02 | 1.02 | 1.03 | 175/175 (100%) | 0.10 | 0.22 | 0.33 | 7 | 2 | 7 |
| fr2_desk | floor found, 1.6 m, and the floor's normal as the up axis | 175 | 1.10 | 1.10 | 1.10 | 175/175 (100%) | 0.17 | 0.29 | 0.37 | 10 | 2 | 4 |
| fr2_desk | floor found, true camera height, floor's normal as up | 175 | 1.02 | 1.02 | 1.03 | 175/175 (100%) | 0.05 | 0.12 | 0.18 | 3 | 2 | 4 |
| fr2_desk | perfect metres (the true scale of each fix) | 175 | 1.00 | 1.00 | 1.00 | 175/175 (100%) | 0.09 | 0.18 | 0.30 | 6 | 2 | 7 |
| fr3_office | as shipped: median viewing distance = 2 m | 145 | 1.31 | 1.31 | 1.32 | 0/145 (0%) | 0.67 | 1.10 | 1.24 | 33 | 1 | 3 |
| fr3_office | median viewing distance = 1.5 m (`typical_m=1.5`) | 145 | 0.98 | 0.98 | 0.99 | 145/145 (100%) | 0.07 | 0.15 | 0.22 | 4 | 1 | 3 |
| fr3_office | one known distance (the two places farthest apart) | 145 | 0.99 | 0.99 | 1.00 | 145/145 (100%) | 0.07 | 0.13 | 0.21 | 3 | 1 | 3 |
| fr3_office | floor found, cameras assumed 1.6 m above it (scale only) | 145 | 1.06 | 1.06 | 1.07 | 145/145 (100%) | 0.14 | 0.29 | 0.41 | 8 | 1 | 3 |
| fr3_office | floor found, the true camera height given (scale only) | 145 | 0.99 | 0.98 | 0.99 | 145/145 (100%) | 0.07 | 0.14 | 0.21 | 4 | 1 | 3 |
| fr3_office | floor found, 1.6 m, and the floor's normal as the up axis | 145 | 1.06 | 1.06 | 1.07 | 145/145 (100%) | 0.14 | 0.20 | 0.33 | 6 | 0 | 1 |
| fr3_office | floor found, true camera height, floor's normal as up | 145 | 0.99 | 0.98 | 0.99 | 145/145 (100%) | 0.03 | 0.07 | 0.23 | 1 | 0 | 1 |
| fr3_office | perfect metres (the true scale of each fix) | 145 | 1.00 | 1.00 | 1.00 | 145/145 (100%) | 0.08 | 0.13 | 0.18 | 4 | 1 | 3 |
| **All rooms** | as shipped: median viewing distance = 2 m | 453 | 1.31 | 1.24 | 1.55 | 0/453 (0%) | 0.63 | 1.66 | 2.49 | 34 | 2 | 13 |
| **All rooms** | median viewing distance = 1.5 m (`typical_m=1.5`) | 453 | 0.98 | 0.93 | 1.16 | 361/453 (80%) | 0.11 | 0.63 | 1.07 | 6 | 2 | 13 |
| **All rooms** | one known distance (the two places farthest apart) | 453 | 0.99 | 0.95 | 1.00 | 453/453 (100%) | 0.08 | 0.22 | 0.63 | 4 | 2 | 13 |
| **All rooms** | floor found, cameras assumed 1.6 m above it (scale only) | 453 | 1.09 | 1.03 | 1.11 | 415/453 (92%) | 0.21 | 0.47 | 2.99 | 11 | 2 | 13 |
| **All rooms** | floor found, the true camera height given (scale only) | 453 | 1.00 | 0.92 | 1.03 | 418/453 (92%) | 0.10 | 0.30 | 2.27 | 6 | 2 | 13 |
| **All rooms** | floor found, 1.6 m, and the floor's normal as the up axis | 453 | 1.09 | 1.03 | 1.11 | 415/453 (92%) | 0.14 | 0.33 | 3.02 | 7 | 1 | 5 |
| **All rooms** | floor found, true camera height, floor's normal as up | 453 | 1.00 | 0.92 | 1.03 | 418/453 (92%) | 0.06 | 0.30 | 2.30 | 3 | 1 | 5 |
| **All rooms** | perfect metres (the true scale of each fix) | 453 | 1.00 | 1.00 | 1.00 | 453/453 (100%) | 0.09 | 0.22 | 0.62 | 5 | 2 | 13 |

Floor found in 534/537 (99%) of the same-room fixes. Tilt of the up axis from the true vertical, degrees: the code's (mean of the baseline cameras' up) median 10.8, p90 20.0; the found floor's normal median 1.0, p90 10.6. True mean height of the baseline cameras above the floor: fr1_office 1.43 m, fr2_desk 1.49 m, fr3_office 1.48 m.
