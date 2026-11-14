# Benchmark: `recon_change.py` on real before/after changes (PASLCD)

- **Date:** 2026-11-14
- **Detector:** `ml/recon_change/recon_change.py` at commit `636cc94f5f441fe9c6e40899c61a3d4efa776054`,
  default settings, imported unmodified (`import recon_change as rc`). SHA-256 of the file as run:
  `76b4a39de5190ffbc247542c743cf3256f0a3a736f08b7bb5865902655f035af`.
- **Hardware:** one NVIDIA L4 on Iridis (`ecsstudents_l4`). Slurm jobs 2217849 (changed pairs,
  2 min 21 s), 2217760 (unchanged pairs, 2 min 13 s) and 2217821 (different lighting, indoor,
  2 min 10 s). Job 2217759 was an earlier run of the changed pairs: its pixel counts are
  identical to 2217849 in every scene. The unchanged job ran with the harness one commit
  earlier; the only change since then is the table by region size.
- The deployed copy in `/scratch/ase1g23/junction-recon/recon_change.py` was changed by other
  work during this session. These numbers are for the commit above, not for that copy.

## Headline

On 10 real scenes with hand-drawn change masks, the detector is **precise but misses most of
what changed**:

- Pixel precision 0.92, recall 0.24, F1 0.38, IoU 0.24 (all 10 scenes, pixels pooled).
- Indoor scenes are harder: IoU 0.17 indoors against 0.36 outdoors.
- It touches 253 of 682 ground-truth regions (37%): 89 of 398 indoors, 164 of 284 outdoors.
- Size decides: it hits 67% of large regions (800 pixels or more), 46% of medium ones and 5%
  of small ones. Indoors, medium regions are hit only 20% of the time.
- Every changed pair got the verdict PASS, and every unchanged pair got NO CHANGE FOUND with
  zero flagged pixels. The unchanged test is a weak one: see "What this does not show".

## Dataset

**PASLCD** (Pose-Agnostic Scene-Level Change Detection), from "Multi-View Pose-Agnostic Change
Localization with Zero Labels" (Galappaththige et al., CVPR 2025).

- Source: the paper's project page <https://chumsy0725.github.io/MV-3DCD/> and its code
  repository <https://github.com/Chumsy0725/MV3DCD> both link to
  <https://huggingface.co/datasets/ChamudithaJay/PASLCD>. No account, form or terms to accept.
- Licence: MIT, as stated on the dataset card. Research use is allowed.
- Content: 10 real scenes photographed with an iPhone, 5 indoor (Cantina, Lounge,
  Printing_area, Lunch_room, Meeting_room) and 5 outdoor (Garden, Pots, Zen, Playground, Porch).
  Each scene has a reference capture before the change and 25 photos after the change, taken on
  an independent path, each with a hand-drawn change mask. Several things change per scene.
  The paper gives the mix as objects added 24%, removed 27%, moved 18%, spills and stickers
  19%, surface colour 12%. The last two groups (31%) are flat, so this detector cannot see them.
- Used here: `Instance_1` of every scene (reference taken in the same lighting as the query).
  Per scene: 24 reference photos as the baseline, 12 post-change photos with masks as the
  query, and 12 more reference photos as an unchanged query. All are spread evenly over the
  capture. 12 MP photos, resized by the detector to 518 x 294.
- Downloaded: 2.29 GB of photos and masks for `Instance_1` (plus 0.91 GB for the indoor scenes
  of `Instance_2`, see the end). No code from the dataset was run. Nothing is in git.

## Results

### Changed pairs (24 baseline photos against 12 query photos with masks)

| Scene | Precision | Recall | F1 | IoU | GT regions hit | Clusters (appeared + gone) | Verdict | Noise between clips | Inference s | Detect s | Peak GPU GB |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Cantina (indoor) | 0.885 | 0.113 | 0.200 | 0.111 | 21/118 | 1 + 3 | PASS | 0.019 | 6.1 | 4.8 | 9.4 |
| Lounge (indoor) | 0.832 | 0.379 | 0.520 | 0.352 | 31/63 | 1 + 3 | PASS | 0.007 | 5.0 | 4.8 | 9.4 |
| Printing_area (indoor) | 0.897 | 0.237 | 0.374 | 0.230 | 22/90 | 0 + 4 | PASS | 0.018 | 5.0 | 4.8 | 9.4 |
| Lunch_room (indoor) | 1.000 | 0.039 | 0.075 | 0.039 | 4/58 | 1 + 0 | PASS | 0.020 | 5.0 | 4.6 | 9.4 |
| Meeting_room (indoor) | 0.827 | 0.098 | 0.175 | 0.096 | 11/69 | 1 + 1 | PASS | 0.014 | 5.0 | 4.5 | 9.4 |
| Garden (outdoor) | 0.891 | 0.324 | 0.475 | 0.312 | 30/58 | 1 + 2 | PASS | 0.009 | 5.0 | 5.4 | 9.4 |
| Pots (outdoor) | 0.999 | 0.339 | 0.507 | 0.339 | 36/56 | 3 + 0 | PASS | 0.010 | 5.0 | 5.4 | 9.4 |
| Zen (outdoor) | 1.000 | 0.313 | 0.476 | 0.313 | 48/66 | 4 + 0 | PASS | 0.011 | 5.0 | 5.5 | 9.4 |
| Playground (outdoor) | 0.799 | 0.244 | 0.374 | 0.230 | 11/36 | 0 + 1 | PASS | 0.024 | 5.0 | 4.9 | 9.4 |
| Porch (outdoor) | 0.939 | 0.443 | 0.602 | 0.430 | 39/68 | 2 + 2 | PASS | 0.010 | 5.0 | 6.0 | 9.4 |
| **Indoor, pixels pooled (5 scenes)** | 0.879 | 0.176 | 0.293 | 0.172 | 89/398 | 15 | | | | | |
| **Indoor, mean over scenes** | | | 0.269 | 0.166 | | | | | | | |
| **Outdoor, pixels pooled (5 scenes)** | 0.954 | 0.365 | 0.528 | 0.359 | 164/284 | 15 | | | | | |
| **Outdoor, mean over scenes** | | | 0.487 | 0.325 | | | | | | | |
| **All, pixels pooled (10 scenes)** | 0.917 | 0.242 | 0.383 | 0.237 | 253/682 | 30 | | | | | |
| **All, mean over scenes** | | | 0.378 | 0.245 | | | | | | | |

How to read the columns:

- **Precision, recall, F1, IoU:** over the pixels of the 12 query photos, at 518 x 294.
- **GT regions hit:** a region is one connected blob of a ground-truth mask in one photo, at
  least 50 pixels. It is hit when 10% or more of it is predicted. The dataset does not label
  objects across photos, so the same object counts once in every photo that shows it.
- **Clusters:** what the detector reports for the scene: things that appeared plus things gone.
- **Noise between clips:** `depth_noise_between_clips` from the report.
- **Inference s:** VGGT on 36 photos, as printed by the detector. **Detect s:** `rc.analyse`.

107 of the 120 query photos have at least one correctly flagged pixel.

### Ground-truth regions hit, by region size

| Region size (pixels at 518 x 294) | Indoor | Outdoor | All |
| --- | --- | --- | --- |
| 50 to 199 | 4/168 (2%) | 7/62 (11%) | 11/230 (5%) |
| 200 to 799 | 27/136 (20%) | 101/145 (70%) | 128/281 (46%) |
| 800 and larger | 58/94 (62%) | 56/77 (73%) | 114/171 (67%) |

Rough guide, not measured: with a 70 degree wide lens, a fist-sized object (10 cm) at 2 m
covers about 350 pixels. A region is a blob of the mask, not an object: one blob can hold
several objects, and a flat sheet of paper counts the same as a box.

### Unchanged pairs (the same 24 baseline photos against 12 other photos of the same capture)

| Scene | Clusters (appeared + gone) | Flagged pixels, % of query photos | Verdict | Noise between clips |
| --- | --- | --- | --- | --- |
| Cantina (indoor) | 0 + 0 | 0.00 | NO CHANGE FOUND | 0.020 |
| Lounge (indoor) | 0 + 0 | 0.00 | NO CHANGE FOUND | 0.007 |
| Printing_area (indoor) | 0 + 0 | 0.00 | NO CHANGE FOUND | 0.015 |
| Lunch_room (indoor) | 0 + 0 | 0.00 | NO CHANGE FOUND | 0.021 |
| Meeting_room (indoor) | 0 + 0 | 0.00 | NO CHANGE FOUND | 0.012 |
| Garden (outdoor) | 0 + 0 | 0.00 | NO CHANGE FOUND | 0.008 |
| Pots (outdoor) | 0 + 0 | 0.00 | NO CHANGE FOUND | 0.009 |
| Zen (outdoor) | 0 + 0 | 0.00 | NO CHANGE FOUND | 0.009 |
| Playground (outdoor) | 0 + 0 | 0.00 | NO CHANGE FOUND | 0.026 |
| Porch (outdoor) | 0 + 0 | 0.00 | NO CHANGE FOUND | 0.011 |
| **Scenes with no false cluster** | **10/10** | | | |

## What the overlays showed

Looked at 12 of the 30 overlays and 3 detector contact sheets, at least one overlay for each
of the 10 scenes (`bench/out/paslcd/<scene>_inst1_changed/` on Iridis), plus 2 overlays of the
different-lighting run.

- **Where the false alarms come from.** Nearly all wrongly flagged pixels belong to "gone"
  objects (13,347 of 13,367). In the overlays they sit on the rim of an object that was found:
  the gone points are projected from the baseline photos and land a few pixels off, and the
  scoring dilates them by 2 pixels. "Appeared" pixels are almost never wrong (20 of 58,401
  fall outside a mask). In the 12 overlays, every flagged blob touched a ground-truth region.
- **Where the recall goes.** The detector marks the solid inside of an object, not its outline,
  so a found object is only partly covered (Zen, Pots, Porch: bottles, pots and boxes found,
  each with an unflagged rim). Flat and small things are missed in every scene.
- **Lunch_room (IoU 0.04, worst).** A large open-plan room photographed from across it. The
  changed things are small and far away: a pole, items on tables, a tube on the floor. One
  cluster is reported (on a white board in the foreground), nothing else, and no false alarm.
  The see-through threshold here is 12.8% of the viewing distance, the highest of the indoor
  scenes: about 64 cm at 5 m. That, and a voxel size that also grows with distance, are the
  likely reasons, but this was not tested.
- **Meeting_room (IoU 0.10).** Found: an object that appeared in the corner and a red one
  that is gone from beside the wall. Missed: the things on the table (a keyboard removed,
  flat sheets, pens, small blocks). Those are flat or a few centimetres high.
- **Cantina (IoU 0.11).** Found: an opened drawer, and the edges of an oven door that changed
  position. Missed: items on the worktop and by the sink, and most of the oven region, which
  the mask covers as one large area.
- **Printing_area (IoU 0.23).** A box removed from the floor is found, about half covered.
  Cupboard doors that changed position are flagged only along one edge, while the mask covers
  the whole door.

## What this does not show

- **False alarms between two separate walks through an unchanged room.** PASLCD has no such
  pair. The unchanged pairs here come from one capture, which is the easy case and the same
  thing the detector's own control already measures. The better evidence on false alarms is
  the precision on the changed pairs, where the two captures follow independent paths.
- **Video.** These are sharp 12 MP stills. Motion blur, rolling shutter and the glasses' camera
  are not covered.
- **Recall on objects it is meant to find.** The masks mix solid objects with flat things
  (paper, stickers, spills) that a geometry-only detector cannot see, and PASLCD does not say
  which is which. The size table is the nearest thing available. Nobody has sorted the misses
  into "flat" and "solid but missed".
- **Run-to-run spread.** The changed pairs were run twice with identical pixel counts. The
  other pairs were run once.
- **The paper's own numbers** are not comparable: its method trains a 3D model per scene and
  is scored on all 25 masks at full resolution. For orientation only, the paper reports mIoU
  0.46 and F1 0.61 on PASLCD (read from the arXiv page, not re-checked against the table).

## Scoring choices

- Appeared: pixels of points in a kept cluster (`member >= 0`) in that query photo, with a 5 x 5
  closing, as in the detector's own overlay.
- Gone: points of kept gone clusters, projected into each query photo with `recon.E` and
  `recon.K`, dropped where that photo's depth is more than 10% nearer than the point (hidden),
  then dilated 5 x 5.
- Masks are resized to 518 x 294 with nearest neighbour. Non-zero means changed.
- `python test_masks.py` (no GPU) checks the mask building on the synthetic room of
  `test_synthetic.py`: both masks land on the box's old and new place, IoU 0.61.

## How to re-run

On the Iridis login node, in `/scratch/$USER/junction-recon` (the folder with `.venv-recon`):

```bash
mkdir -p bench
# copy eval/run_bench.py, eval/fetch_paslcd.py, eval/bench.slurm and the recon_change.py to
# test into bench/ ; after scp from Windows: sed -i 's/\r$//' bench/*.py bench/bench.slurm
.venv-recon/bin/python -I bench/fetch_paslcd.py --out bench/data/PASLCD
```

```bash
sbatch bench/bench.slurm --out bench/out/paslcd --pairs changed --detector-commit <commit>
```

```bash
sbatch bench/bench.slurm --out bench/out/paslcd --pairs unchanged --detector-commit <commit>
```

The download is 2.3 GB and takes about a minute. Each job takes under 3 minutes on an L4. The
tables are written to `bench/out/paslcd/results_changed.md` and `results_unchanged.md` (and
`.json` with per-photo counts), next to one folder per pair with three overlays, the detector's
contact sheet and its `report.json`. In an overlay, the left half shows the query photo with
the prediction (green appeared, red gone) and the ground truth outlined in yellow; the right
half shows hits in green, false alarms in red and misses in blue.

The harness loads `recon_change.py` from its own folder if there is one, otherwise from the
parent folder, so the deployed copy is never touched. Use `--detector-dir` to point elsewhere.

## Different lighting (`Instance_2`, indoor scenes only)

`Instance_2` pairs the same query photos with another reference capture, taken in different
lighting and on another path. Its masks differ slightly from `Instance_1` in some scenes.
Job 2217821, same detector commit.

| Scene | Precision | Recall | F1 | IoU | GT regions hit | Clusters (appeared + gone) | Verdict | Noise between clips | IoU with same lighting |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Cantina | 0.885 | 0.226 | 0.360 | 0.220 | 22/118 | 1 + 2 | PASS | 0.016 | 0.111 |
| Lounge | 0.779 | 0.409 | 0.537 | 0.367 | 31/65 | 1 + 3 | PASS | 0.008 | 0.352 |
| Printing_area | 0.870 | 0.230 | 0.364 | 0.222 | 21/100 | 1 + 3 | PASS | 0.018 | 0.230 |
| Lunch_room | 1.000 | 0.010 | 0.020 | 0.010 | 0/58 | 2 + 0 | PASS | 0.021 | 0.039 |
| Meeting_room | 1.000 | 0.026 | 0.051 | 0.026 | 6/69 | 1 + 0 | PASS | 0.014 | 0.096 |
| **Indoor, pixels pooled** | 0.861 | 0.200 | 0.325 | 0.194 | 80/410 | 14 | | | 0.172 |
| **Indoor, mean over scenes** | | | 0.266 | 0.169 | | | | | 0.166 |

- Regions hit by size: 4/178 small (2%), 26/138 medium (19%), 50/94 large (53%).
- The 5 unchanged pairs: no cluster, no flagged pixel, NO CHANGE FOUND.
- Overall it is about the same as with matching lighting, but single scenes move both ways
  (Cantina up, Meeting_room and Lunch_room down). The reference capture differs in path as
  well as in lighting, so this does not isolate the effect of lighting.
- In Lunch_room the two reported clusters lie inside ground-truth regions but cover too
  little of any region to count as a hit. The verdict is still PASS.

```bash
.venv-recon/bin/python -I bench/fetch_paslcd.py --out bench/data/PASLCD --instances 2 --scenes Cantina Lounge Printing_area Lunch_room Meeting_room
```

```bash
sbatch bench/bench.slurm --out bench/out/paslcd_inst2 --instance 2 --scenes Cantina Lounge Printing_area Lunch_room Meeting_room --detector-commit <commit>
```
