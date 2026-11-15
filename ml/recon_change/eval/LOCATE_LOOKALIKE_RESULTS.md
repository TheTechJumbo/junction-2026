# Does an appearance check stop "where is the wearer?" accepting the wrong room?

Status: **finished.** Yes, on this benchmark. 945 passes scored (the 885 of `LOCATE_RESULTS.md`
plus 60 more other-lighting queries), none failed.

## Short answer

Same passes as `LOCATE_RESULTS.md` (PASLCD, 10 scenes), scored twice from the same saved
reconstructions: **before** = `locate.py` as it was (depth only, found 0.5, lost below 0.3),
**after** = this branch.

| | 24 baseline photos | 12 | 8 |
| --- | --- | --- | --- |
| Wrong room, found / unsure / lost (180 each): before | 0 / 0 / 180 | **1** / 0 / 179 | 0 / 2 / 178 |
| Wrong room: after | 0 / 0 / 180 | 0 / 0 / 180 | 0 / 0 / 180 |
| Highest wrong-room match: before -> after | 0.23 -> 0.12 | 0.97 -> 0.08 | 0.39 -> 0.09 |
| Same room, found / unsure / lost (90 each): before | 89 / 1 / 0 | 89 / 1 / 0 | 88 / 2 / 0 |
| Same room: after | 89 / 1 / 0 | 90 / 0 / 0 | 88 / 1 / **1** |
| Lowest same-room match: before -> after | 0.49 -> 0.49 | 0.48 -> 0.51 | 0.30 -> 0.27 |
| Same room, baseline under other lighting, found / unsure / lost (25 each): before and after | 25 / 0 / 0 | 25 / 0 / 0 | 24 / 1 / 0 |
| Lowest other-lighting match: before -> after | 0.53 -> 0.53 | 0.54 -> 0.54 | 0.46 -> 0.45 |
| Position error, % of the baseline cameras' spread, median (worst): before and after | 0.4 (1.7) | 0.4 (1.9) | 0.4 (1.5) |
| Heading error, degrees, median (worst): before and after | 0.4 (1.6) | 0.5 (1.6) | 0.7 (1.6) |
| `locate` time, one process on an Iridis CPU node: before -> after | 0.30 -> 0.46 s | 0.16 -> 0.25 s | 0.11 -> 0.21 s |
| Model time on the L4 (unchanged) | 3.2 s | 1.5 s | 1.0 s |

1. **Wrong room: no "found" and no "unsure" at any baseline size** (540 queries). The highest
   wrong-room match is 0.12. The photo that was found with 0.97 now scores 0.02.
2. **Same room: 267 of 270 found (was 266), 1 lost (was 0).** The lost one is a Zen photo on 8
   baseline photos that sat on the threshold: 0.30 -> 0.27. Its position was right (0.6%
   off). At 24 and 12 photos no same-room query is lost. Other lighting changes nothing: the
   features tolerate it here (75 of 75 states unchanged; with all 135 other-lighting queries
   saved, 134 found before and after).
3. **Position and heading are unchanged to the last digit** in all 945 passes: those lines of
   code are not touched. Extra time: 0.10 to 0.16 s per query. Measured on a CPU node, not on
   the L4 node the demo runs on.
4. **12 baseline photos now pass every check that 24 passes on this benchmark**: 0 of 180 wrong
   rooms above 0.08, 90 of 90 same-room found, same position error. That would cut the time per
   fix from about 3.7 s to about 1.8 s (model on the L4 plus `locate` as timed here). 0 of 180 still allows a true rate of up to about 1.7%
   (95% bound), and two look-alike rooms of one home are not in this data (see the end).

## What changed in `locate.py`

Before: a pixel of the wearer's photo counted as matched if at least two baseline photos saw
the same depth where it lands. Now those two photos must also show something that looks alike
there: the cosine similarity between the pixel's own image patch and the patch where it lands
must be at least 0.5 (`LOOK_LIMIT`). The patch features are the ones the model pass already
produces (`Recon.feat`). Poses are not patch-perfect, so the best of the 3 x 3 patches around
the landing place counts (`LOOK_REACH = 1`). The yardstick ("how well do baseline photos match
each other") is measured with the same rule, so the score still adapts to the room.

- `Recon.feat` missing (older saved reconstructions), or `look=False`: scored on depth alone,
  exactly as before.
- New keys in the result: `looks_alike` (of the newest photo's pixels that match by depth, the
  share that also look alike; `None` without features) and `match_by_depth` (the old score).
  The return shape is the same. Nothing under `live/` was changed, so the pages do not show the
  new keys yet.
- It stays cheap because it stops early: a pixel is not looked at again once two photos look
  alike, and the surrounding patches are only tried if the landing patch fails.

## The cases that were wrong before

| Wrong-room photo | Baseline | Match before | Match after | Looks alike |
| --- | --- | --- | --- | --- |
| Cantina 2891 | Lunch room, 12 photos | 0.97 (found) | 0.02 | 0.01 |
| Cantina 2863 | Lunch room, 8 | 0.39 (unsure) | 0.04 | 0.07 |
| Cantina 2891 | Lunch room, 8 | 0.37 (unsure) | 0.00 | 0.00 |
| Meeting room 1871 | Lounge, 24 | 0.23 | 0.12 | 0.51 |
| Cantina 2876 | Printing area, 24 | 0.22 | 0.02 | 0.11 |
| Cantina 2863 | Lunch room, 12 | 0.21 | 0.00 | 0.01 |
| Pots E2693 | Porch, 8 | 0.21 | 0.01 | 0.02 |

The highest wrong-room scores now: Meeting room 1871 on the Lounge at 24 photos (0.12) and at 12
(0.08), Printing area 2215 on the Cantina at 8 (0.09). For the first of these, half of the
pixels that match by depth also look alike. I did not look at the pictures. Wrong-room queries at or above
0.05: 2, 1 and 3 of 180 at 24, 12 and 8 photos.

How the two groups look (all 945 passes):

| Baseline photos | Queries | Number | Looks alike: lowest | median | highest |
| --- | --- | --- | --- | --- | --- |
| 24 | Same room | 90 | 0.80 | 0.99 | 1.00 |
| 24 | Same room, other lighting | 45 | 0.81 | 0.98 | 1.00 |
| 24 | Wrong room | 180 | 0.00 | 0.00 | 0.56 |
| 12 | Same room | 90 | 0.67 | 0.97 | 1.00 |
| 12 | Same room, other lighting | 45 | 0.65 | 0.96 | 0.99 |
| 12 | Wrong room | 180 | 0.00 | 0.00 | 0.65 |
| 8 | Same room | 90 | 0.61 | 0.94 | 1.00 |
| 8 | Same room, other lighting | 45 | 0.63 | 0.92 | 0.99 |
| 8 | Wrong room | 180 | 0.00 | 0.00 | 0.49 |

`looks_alike` is a share of the pixels that match by depth, so a wrong room with very few such
pixels can still show 0.65. The verdict is the match score, not this number.

Same-room scores moved a little in both directions: of 405 same-room queries, 15 fell by 0.05
or more and 9 rose by 0.05 or more (the yardstick falls too). Two states changed: Zen at 8
photos (unsure -> lost, above) and Cantina 2863 at 12 photos (unsure 0.48 -> found 0.51).

## How the limit was chosen

The brief asked for a limit taken from the baseline photos compared with each other. **That
was tried and it is worse than a fixed limit**, so the code uses a fixed one.

To keep the choice honest, the scenes were split by baseline scene before any score was seen:
tune = Cantina, Printing area, Meeting room, Garden, Zen; report = Lounge, Lunch room, Pots,
Playground, Porch. Six of the seven wrong-room cases that `LOCATE_RESULTS.md` lists as not lost are on the
report side. `lookalike_sweep.py` scored 230 rules on every pass and ranked them on the tune scenes
only: highest wrong-room score under 0.15, then the most same-room queries found, then the
fewest lost, then the largest gap, then the fewest knobs.

| Rule | Tune: highest wrong room | same room found (of 216) | lost | Report: highest wrong room | same room found (of 189) | lost |
| --- | --- | --- | --- | --- | --- | --- |
| Depth only (before) | 0.22 | 211 | 0 | 0.97 | 189 | 0 |
| **Fixed 0.5, best of 3 x 3 patches (chosen)** | 0.09 | 212 | 1 | 0.12 | 189 | 0 |
| Fixed 0.3 | 0.15 | 211 | 1 | 0.24 | 189 | 0 |
| Fixed 0.4 | 0.12 | 212 | 1 | 0.17 | 189 | 0 |
| Fixed 0.6 | 0.05 | 209 | 1 | 0.08 | 189 | 0 |
| Fixed 0.7 | 0.02 | 205 | 1 | 0.03 | 188 | 0 |
| Fixed 0.5, landing patch only | 0.03 | 206 | 1 | 0.06 | 189 | 0 |
| From the baseline: 0.8 x 10th percentile of all its pixel-photo pairs (my first guess) | 0.15 | 211 | 1 | **0.66** | 189 | 0 |
| From the baseline: best rule of that family (0.7 x 25th percentile) | 0.13 | 212 | 1 | 0.26 | 189 | 0 |
| From the baseline: 0.8 x 10th percentile of each pixel's second-best photo | 0.13 | 210 | 1 | **0.66** | 189 | 0 |
| From the baseline: best rule of that family (0.6 x median) | 0.10 | 212 | 1 | 0.12 | 189 | 0 |
| From the baseline, but never below 0.5 (0.6 x 25th percentile) | 0.09 | 212 | 1 | 0.12 | 189 | 0 |

- Why a limit from the baseline alone fails: baseline photos are spread over the room, so they
  look less alike to each other than a new photo and its nearest baseline photos do. In the
  Lunch room at 12 photos a tenth of the baseline's own matches score under about 0.25, so the
  limit ends up near 0.2, and on the report scenes a wrong-room photo still scores 0.66 ("found").
- With a floor of 0.5 the baseline-derived part changes almost nothing (it was above the floor
  in 23% of passes, by at most 0.05), and the result is the same to two decimals. The fixed
  limit won the tie because it has fewer knobs, and only a fixed limit can stop early: working
  out every similarity, as a baseline-derived limit needs, took 1.4 s per query at 24 photos
  against 0.5 s with early stopping (15 processes side by side in both runs).
- 0.5 is not a knife edge: 0.4 to 0.6 all reject every wrong room on both halves. A stricter
  limit lowers wrong-room scores further and costs same-room "found" answers (0.7: 7 fewer on
  the tune scenes).
- On the report scenes, which had no say in the choice, the rule gives: wrong room 0 found and
  0 unsure of 270 (before: 1 found, 2 unsure), highest 0.12 / 0.08 / 0.05 at 24 / 12 / 8 photos
  (before 0.23 / 0.97 / 0.39); same room 135 of 135 and other lighting 54 of 54 found, as before.

What weakens the split, plainly:

- I changed the ranking once, after seeing tune-scene numbers. The first ranking (largest gap
  only) picked a stricter rule that cost two or three same-room "found" answers per size, which
  is against the brief's order of priorities. Report-scene numbers played no part in that.
- While the reconstructions were still being saved I looked at the report-side hard cases for
  eight rules. All eight rejected them.
- The split is by baseline scene. A wrong-room query on a report-scene baseline can be a photo
  from a tune scene (the Cantina photos are).
- The top-level table above is all scenes, tune and report together.

## Method

- `locate_eval.py --dump` ran the model once per pass on an L4 and saved depth, confidence,
  cameras and features (23 GB, not in the repo). `--from-dump` then scores them on CPU cores.
  Passes: same room 9 per scene, wrong room 18 per scene, other lighting 9 per indoor scene
  (the earlier report had 5; the table above uses those 5, the rest are counted where stated),
  each at 24, 12 and 8 baseline photos.
- Check that this is the same benchmark: the old `locate.py` on the saved reconstructions gives
  the same match as the earlier GPU run in all 885 shared passes (largest difference 0.00) and
  the same positions. The only state changes come from the "unsure" threshold moving from 0.2
  to 0.3 after that report (4 wrong-room queries, unsure -> lost).
- "Before" and "after" were both scored from the saved reconstructions, by `locate_eval.py`.
  The sweep's own number for the chosen rule agrees with what `locate.py` answers in all 945
  passes (largest difference 0.01, from rounding).
- Time: `locate.locate` alone, 63 passes (every 15th), one process with 4 cores on an Iridis CPU
  node, old and new one after the other. With 15 processes side by side the numbers were 0.33 -> 0.52,
  0.18 -> 0.31 and 0.12 -> 0.23 s.
- Offline check: `python eval/test_locate_lookalike.py` (synthetic room, no GPU). A query whose
  depth lies exactly on the room's surfaces but whose patches show another place is "lost"
  (match 0.00, depth-only 1.00); the same query with the room's own look is "found"; without
  features the answer is the depth-only one; position, heading and map are identical.

## How to run

Headers of `locate_dump.slurm` (GPU, once) and `locate_rescore.slurm` (CPU):
`sbatch loclook/eval/locate_rescore.slurm eval <folder with locate.py> <output>` for the
benchmark tables, `... sweep <folder> <output>` for the rule comparison.

## Not tested, or unsure

- **On the L4 node, end to end.** Every score here comes from saved reconstructions on CPU
  nodes. The live worker was not run with this `locate.py`, and the extra time was not
  measured on the machine the demo uses.
- **Two rooms of one home that look alike** (two bedrooms, the same furniture). Patch features
  compare what things look like, so this check is weakest exactly there. The scenes here are
  different places.
- A real home, the glasses camera, motion blur, night lighting, a person in view. The
  other-lighting test is 5 indoor scenes, daylight changes only.
- A room whose baseline photos barely look alike to each other. The yardstick shrinks with the
  appearance rule (here to 52% of its depth-only value at worst, median 98%), and a small
  yardstick inflates every score. No guard was added for that.
- Several query frames at once: `match_per_frame` uses the new rule for each frame, but only
  single-photo queries were scored.
- Whether 0.5 holds for another model or another feature size: the features are reduced per
  run (`reduce_features`), and the limit was chosen for that.
- 0 wrong-room "found" in 180 per size is not proof of zero (up to about 1.7% at 95%).
- The Zen photo lost at 8 baseline photos was not looked at beyond its numbers (most of its
  depth-matched pixels look alike, 0.86; both its score and the yardstick moved).
- Places and bearings (`waypoints`): not scored, code unchanged.
