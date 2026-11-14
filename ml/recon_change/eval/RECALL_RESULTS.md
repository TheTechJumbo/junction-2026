# Recall: a second kind of evidence, "looks different" (PASLCD)

- **Date:** 2026-11-14. Branch `jun-22-recall`, based on `jun-22-hud-waypoints` at `ea6b5a6`.
- **Data and scoring:** the PASLCD benchmark of `RESULTS.md` (10 real scenes, hand-drawn masks,
  24 baseline photos against 12 query photos, scored at 518 x 294). Nothing about the scoring
  changed. `run_bench.py` now also scores the new evidence on its own.
- **Hardware:** Iridis. Reconstructions were made once on an L4 (jobs 2218956 and 2218993) and
  saved. Every detection result below was then computed from those files on CPU cores
  (`amd_student`, 8 cores; jobs 2219061 and 2219066). No extra GPU time per check.

## Headline

The detector now reports a second list, `looks_different`: places where the new clip shows
something that looks different from what the baseline frames showed there. On PASLCD with
matching lighting it finds more, mostly medium and large things. It does **not** find small
things, and with different lighting it adds almost nothing.

| Instance_1 (same lighting), 10 scenes | Before (geometry only) | After (geometry + looks different) |
| --- | --- | --- |
| Precision, pixels pooled | 0.918 | 0.906 |
| Recall, pixels pooled | 0.239 | 0.303 |
| F1 / IoU | 0.379 / 0.234 | 0.454 / 0.293 |
| Regions hit, all | 253/682 (37%) | 318/682 (47%) |
| Indoor: precision / recall | 0.879 / 0.170 | 0.891 / 0.215 |
| Outdoor: precision / recall | 0.954 / 0.365 | 0.919 / 0.464 |
| Unchanged pairs with no flagged pixel | 10/10 | 10/10 |
| Change detection time per check (8 CPU cores) | 4.1 to 5.3 s | 6.0 to 8.0 s |

Regions hit by size (a region is one blob of a mask in one photo; hit = 10% of it predicted):

| Region size (pixels at 518 x 294) | Indoor before | Indoor after | Outdoor before | Outdoor after |
| --- | --- | --- | --- | --- |
| 50 to 199 (small) | 4/168 (2%) | 6/168 (4%) | 7/62 (11%) | 8/62 (13%) |
| 200 to 799 (medium) | 27/136 (20%) | 44/136 (32%) | 101/145 (70%) | 123/145 (85%) |
| 800 and larger | 58/94 (62%) | 67/94 (71%) | 56/77 (73%) | 70/77 (91%) |

"Before" is the detector of `ea6b5a6` re-run for this work (job 2218956). It includes the
look-alike check, which `RESULTS.md` (measured on `636cc94`) did not: pooled precision 0.918
and recall 0.239 against 0.917 and 0.242 there, the same 253 regions, the same clean unchanged
pairs. Cantina and Printing_area moved by about 0.01 recall, the other scenes not at all.
The geometry result is the same number for number in the "after" run: the new check does not
touch `appeared`, `gone` or the verdict.

## Per scene (Instance_1, changed pairs)

| Scene | Before: P / R / regions | After: P / R / regions | Looks-different groups | Their pixel precision |
| --- | --- | --- | --- | --- |
| Cantina (indoor) | 0.889 / 0.111 / 21 of 118 | 0.907 / 0.142 / 25 | 5 | 0.976 |
| Lounge (indoor) | 0.832 / 0.379 / 31 of 63 | 0.832 / 0.397 / 31 | 1 | 0.833 |
| Printing_area (indoor) | 0.897 / 0.224 / 22 of 90 | 0.913 / 0.274 / 25 | 4 | 0.991 |
| Lunch_room (indoor) | 1.000 / 0.039 / 4 of 58 | 0.861 / 0.065 / 7 | 1 | 0.715 |
| Meeting_room (indoor) | 0.827 / 0.098 / 11 of 69 | 0.860 / 0.187 / 29 | 4 | 0.899 |
| Garden (outdoor) | 0.891 / 0.324 / 30 of 58 | 0.889 / 0.381 / 32 | 1 | 0.894 |
| Pots (outdoor) | 0.999 / 0.339 / 36 of 56 | 0.930 / 0.454 / 48 | 7 | 0.774 |
| Zen (outdoor) | 1.000 / 0.313 / 48 of 66 | 0.925 / 0.487 / 57 | 6 | 0.816 |
| Playground (outdoor) | 0.799 / 0.244 / 11 of 36 | 0.799 / 0.244 / 11 | 0 | - |
| Porch (outdoor) | 0.939 / 0.443 / 39 of 68 | 0.923 / 0.504 / 53 | 4 | 0.826 |

The new evidence alone: pixel precision 0.868, recall 0.066, 115 of 682 regions (3 small, 60
medium, 52 large). Its masks are made of 7 x 7 pixel cells, so they are blocky; part of the
lost precision is the rim of the cells, not a wrong place. I did not look at the overlays, so
I cannot say what the wrongly flagged pixels are.

## Different lighting (Instance_2, indoor scenes only)

Same query photos against a baseline capture taken in other lighting and on another path
(reconstruction job 2218993, scoring job 2219066).

| | Geometry only | Geometry + looks different |
| --- | --- | --- |
| Precision / recall, pixels pooled | 0.861 / 0.200 | 0.859 / 0.208 |
| Regions hit | 80/410 | 82/410 |
| Small / medium / large regions | 4/178, 26/138, 50/94 | 4/178, 27/138, 51/94 |
| Unchanged pairs with no flagged pixel | 5/5 | 5/5 |

With a change of lighting the new check goes nearly silent: 7 groups in 5 scenes, precision
0.831 on the few pixels it flags. It does no harm, and it does not help. This is the expected
behaviour of the scale (see below), and it is the main limit of the approach: the gain in the
headline table comes from scenes photographed twice in the same light.

## How it works

For every 7 x 7 pixel cell of every new frame with a trusted depth:

1. Take its 3D point and project it into each baseline frame. A baseline frame counts when it
   has a trusted view of the same surface there, or of something behind it (then it looked
   through the place, and what it saw is what the place looked like). Something in front hides
   the place, and that frame is skipped. At least two baseline frames must count.
2. Compare what the cell looks like with what those frames show there, and keep the best match
   over the frames: smallest colour difference (smoothed Lab, lightness at half weight, the
   typical colour offset between the two frames removed, best of 5 small shifts) and highest
   similarity of the DINOv2 patch features VGGT already computes (`Recon.feat`, interpolated).
3. Do the same against the other frames of the new clip. Only the surplus counts: how much
   worse the baseline matches than the clip's own frames do. A cell on an edge, a reflection or
   fine texture differs from every other view, its own clip included, and cancels out.
4. Score = colour surplus + feature surplus, each divided by its robust spread. The spread is
   the wider of what the two clips show and what the control shows (the baseline clip against
   itself), with a floor. A noisy scene or a change of lighting widens it.
5. The limit comes from the control split: the lowest limit at which the baseline clip compared
   with itself reports no group at all, times 1.5, and at least 8.
6. Cells over the limit are grouped like the geometry hits (touching voxels, each seen in two
   frames, at least 8 voxels). Cells at a place geometry already reports are left out, and a
   group next to such a place is marked `next_to_geometry_change`.

In `report.json`: `looks_different` (a list like `appeared`) and `looks_different_check` (limit,
scales, counts). The verdict, `appeared`, `gone` and the events built on them are unchanged: a
pair with only a `looks_different` entry still says NO CHANGE FOUND. `analyse(...,
looks_different=False)` and `--no-looks-different` switch it off.

## What did not work: patch features alone

The first version was the plain idea: where the geometry agrees, compare DINOv2 patch features
between the new frame and the baseline frames. Measured per cell against the masks, on the 10
changed pairs, pooled (job 2218980; "caught" = share of ground-truth changed cells over a
threshold that also flags the given share of unchanged cells):

| Score | Caught at 0.1% false cells | at 0.5% | at 1% |
| --- | --- | --- | --- |
| Feature difference to the baseline | 0% | 2% | 6% |
| Feature surplus over the own clip | 3% | 13% | 22% |
| Colour difference to the baseline | 10% | 21% | 28% |
| Colour surplus over the own clip | 20% | 30% | 37% |
| Colour surplus + feature surplus | 21% | 34% | 40% |

Patch features of one unchanged surface already differ a lot between two views (in the three
scenes checked, job 2218972, 5% of the control's patches had a best similarity under 0.52 to
0.65, and those are neighbouring frames of one capture), and a changed patch often stays
similar (median 0.65 to 0.69 in those scenes). I did not test why. So the feature
difference alone cannot be thresholded. Two things made the difference: subtracting what the
clip's own frames disagree by, and adding colour. Colour is the stronger part in matching
light. I kept the feature part because it helped in single scenes (Pots: 72% against 51% at
0.1%) and should hold up better when light changes, but the Instance_2 result above shows that
it does not carry the check by itself.

## The limit is a trade, and it was set on this data

Same reconstructions, only the factor on the control's limit changed (job 2219033, group size
8 voxels):

| Factor | Precision | Recall | Regions hit (small / medium / large, indoor + outdoor) | Unchanged pairs flagged |
| --- | --- | --- | --- | --- |
| 1.0 | 0.867 | 0.342 | 35 / 189 / 140 | 5 of 10 (8 false groups, 1288 pixels) |
| 1.25 | 0.893 | 0.316 | 22 / 176 / 138 | 1 of 10 (1 false group, 90 pixels) |
| **1.5** | **0.906** | **0.303** | **14 / 167 / 137** | **0 of 10** |
| 2.0 | 0.915 | 0.283 | 12 / 142 / 132 | 0 of 10 |
| 3.0 | 0.921 | 0.256 | 11 / 140 / 123 | 0 of 10 |

1.5 is the lowest factor tried that keeps all 10 unchanged pairs clean. It was chosen on these
same 10 pairs, so "10 of 10 clean" is not an independent test. An earlier version with a fixed
percentile of the control as the limit (no "control must report nothing" rule) hit 55% of
regions and 25 of 168 small indoor regions, but put false groups into 7 of the 10 unchanged
pairs (job 2219001), so it was dropped.

## Time

`rc.analyse` per pair of 24 + 12 frames, on 8 cores of an `amd_student` node:

- 4.1 to 5.3 s with `looks_different=False`, 5.9 to 7.7 s with it, both timed in one job on
  all 20 pairs, before the limit search was added (job 2219001): 1.6 to 2.5 s more per pair.
- 6.0 to 8.0 s on all 20 pairs in the final run, which includes the limit search (job 2219061).

So the new check costs roughly 1.7 to 2.8 s per check: inside the 3 s budget, but not by much.
It runs the comparison twice (new clip, then control). It was not optimised or profiled. No GPU time is added: it uses the depth,
cameras, photos and patch features the reconstruction already returns.

## Tests

- `python test_synthetic.py`: all 19 checks pass. New: a flat 30 cm sticker on the floor in the
  second clip gives no geometry change, one `looks_different` group at the sticker and nothing
  else, the verdict stays NO CHANGE FOUND, and an unchanged room gives no `looks_different`.
- `python eval/test_masks.py`: passes.

## Not tested, or not sure

- **Two separate walks through an unchanged room.** PASLCD has no such pair. The unchanged
  pairs here are photos held back from the baseline capture, which is the easy case and close
  to what the control already measures. A second walk in the same light with nothing changed
  is the test this check most needs, and it has not had it.
- **Video from the glasses.** Auto-exposure, motion blur and white balance drift are not in
  these stills. The colour part removes one colour offset per frame pair, nothing more.
- **Different lighting** is covered only by the five indoor scenes above, where the check stays
  quiet. Shadows that move, lamps switched on, sunlight patches: not tested.
- **The overlays were not looked at.** All statements here come from the pixel counts.
- **The factor 1.5 and the group size 8** were picked on the benchmark itself.
- **The live path** (`live/`) was not touched and not run with this change. `analyse` takes
  about 2 s longer and the report has two new keys.
- **The "something behind it" rule** (step 1) raised recall and precision on the benchmark
  compared with "same surface only" (pooled 0.875 / 0.351 against 0.864 / 0.318 at an earlier
  setting), but it relies on the two clips lining up. With badly misaligned clips it could flag
  the rim of unchanged objects. The look-alike check does not cover this list.
- **Small things stay invisible:** 14 of 230 small regions after, 11 before.

## How to re-run

In `/scratch/$USER/junction-recon` on Iridis, with PASLCD already in `bench/data/PASLCD`:

```bash
mkdir -p recall/logs
# copy eval/run_bench.py, eval/recall_gpu.slurm, eval/recall_cpu.slurm and the recon_change.py
# to test into recall/ ; after scp from Windows: sed -i 's/\r$//' recall/*.py recall/*.slurm
sbatch recall/recall_gpu.slurm --out recall/out/base --save-recon
```

```bash
sbatch recall/recall_cpu.slurm recall/run_bench.py --from-recon recall/out/base --out recall/out/looks
```

The first job reconstructs all 20 pairs once (4 min 32 s on an L4, 71 MB per pair). The second
scores whatever `recall/recon_change.py` is at that moment (2 min 21 s) and writes
`recall/out/looks/results_changed_unchanged.md` with three sets of tables: all evidence,
geometry only, looks different only. For Instance_2 add `--instance 2 --scenes Cantina Lounge
Printing_area Lunch_room Meeting_room` to both.
