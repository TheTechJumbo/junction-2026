# Best-quality configuration: what was tried and what it bought (JUN-22)

Goal: no false changes on hard video (the second clip looks from new viewpoints), cost no
object. Measured on Iridis (NVIDIA L4, 24 GB) on 14 Nov 2026. The five video cases are the
aerial clips from the VGGT examples, before = first 60% of the clip, after = last 40%, nothing
changed; "crop" is an upright 9:16 centre crop. The kitchen set is 24 + 12 interleaved photos.

## Result in one paragraph

More frames do not help: the misalignment between the clips is the same at 36, 72, 96 and 144
frames, and one extra false cluster appears. A rigid correction between the clips cannot fix
the remaining case either, because the error is not rigid. What works is a look-alike check:
drop a flagged surface when the other clip has a surface that looks the same close by. It uses
the DINOv2 patch features that VGGT computes anyway, so it needs no extra model, download or
GPU time. With it, every unchanged case tested reports nothing, at every frame count, and
virtual boxes are still found. The best configuration is therefore the cheapest one: 24 + 12
frames with the look-alike check (now the default).

## 1. Brute force: more frames

Stock detector (before this change). "Noise" is the robust sigma of the depth gap, inside the
before clip / between the clips. Clusters are in voxels, appeared / gone.

| Case | Frames | Noise inside / between | False clusters | Inference | Peak GPU |
| --- | --- | --- | --- | --- | --- |
| Colosseum_crop | 24+12 | 0.31% / 0.87% | 32, 14 / 34 | 5.0 s | 9.4 GB |
| Colosseum_crop | 48+24 | 0.31% / 0.86% | 32, 16, 11 / 29 | 13.0 s | 10.7 GB |
| Colosseum_crop | 64+32 | 0.30% / 0.86% | 33, 17, 12 / 31 | 18.7 s | 12.0 GB |
| Colosseum_crop | 96+48 | 0.30% / 0.86% | 37, 17, 12 / 30 | 34.7 s | 14.5 GB |
| great_wall_land | 24+12 | 0.50% / 0.77% | none | 5.3 s | 9.4 GB |
| great_wall_land | 48+24 | 0.52% / 0.86% | - / 21 | 12.5 s | 10.7 GB |
| great_wall_land | 64+32 | 0.52% / 0.85% | - / 39 | 18.8 s | 12.0 GB |
| great_wall_crop | 24+12 | 1.02% / 3.95% | none | 5.1 s | 9.4 GB |
| great_wall_crop | 48+24 | 0.99% / 3.97% | none | 12.4 s | 10.7 GB |
| great_wall_crop | 64+32 | 1.00% / 3.99% | none | 18.7 s | 12.0 GB |
| pyramid_land | 24+12 | 1.01% / 3.99% | none | 8.0 s | 10.5 GB |
| pyramid_land | 48+24 | 1.01% / 3.87% | none | 20.7 s | 12.4 GB |
| pyramid_land | 64+32 | 1.04% / 3.89% | none | 32.2 s | 14.2 GB |
| Colosseum_land | 24+12 | 0.23% / 0.28% | none | 5.0 s | 9.4 GB |
| Colosseum_land | 48+24 | 0.22% / 0.28% | none | 12.5 s | 10.7 GB |
| Colosseum_land | 64+32 | 0.23% / 0.28% | none | 18.8 s | 12.0 GB |

- The noise between the clips does not move with the frame count. It is a property of the two
  viewpoints, not of how densely they are sampled.
- More frames make it slightly worse: great_wall_land gains a false "gone" cluster at 72 and
  96 frames, because more frames means more chances to collect the two see-through votes.
- Memory grows slowly (about 0.05 GB per frame), so 144 frames fit on the L4 with room to
  spare. Only Colosseum_crop was run at 144: that job was stopped early to give the GPU slot
  back to the live worker.
- Bigger GPUs: the account can submit to `a100` and `scavenger_4a100` (`sbatch --test-only`
  accepts both), but the estimated start was five to six hours away, and nothing here needs
  more than 15 GB. `l40`, `scavenger_8h100` and the H200 partitions refuse student accounts.

## 2. Aligning the clips first (measured, not implemented)

SIFT matches between after frames and before frames give pairs of 3D points that should
coincide. One similarity transform between the clips (RANSAC) changes the median 3D error like
this:

| Case | Matches | Median error before | after alignment | Pixel error p95 |
| --- | --- | --- | --- | --- |
| Colosseum_land | 73,687 | 0.29% | 0.29% | 2.6 px |
| Colosseum_crop | 77,385 | 1.19% | 1.10% | 22 px |
| great_wall_crop | 6,667 | 2.34% | 1.31% | 60 px |
| pyramid_land | 10,779 | 2.16% | 1.18% | 42 px |

A rigid correction would roughly halve the error on great_wall_crop and pyramid_land, which
would let their thresholds tighten (today they only report changes larger than about 24% of
the viewing distance). It does nothing for Colosseum_crop: of 2,923 matches more than 5% off,
2,827 are still more than 5% off afterwards. There the foreground tree sits at a slightly
wrong depth relative to the building, so no single transform fixes both. This is why the
look-alike check was built first. The alignment is the next thing worth adding.

## 3. Look-alike check (implemented, default on)

A flagged surface is dropped when the other clip has, in at least two frames, a surface within
`reach` of it that looks the same.

- Looks the same: cosine similarity of DINOv2 patch features (taken from VGGT's own first
  stage with a forward hook, mean removed, reduced to 256 dimensions). The bar is the lower
  quartile of the similarity that unchanged surfaces have between the two clips, kept between
  0.60 and 0.85.
- `reach` = 15 x the depth noise the clips have between them beyond the noise inside one
  clip, at most 15% of the viewing distance. Below one and a half image patches the check is
  off. So it switches itself on only as far as the clips are misaligned: kitchen and
  Colosseum_land get reach 0 and behave exactly as before.

Unchanged scenes, false clusters (appeared / gone) without and with the check:

| Case | Frames | Without | With | Reach | Analysis time without / with |
| --- | --- | --- | --- | --- | --- |
| Colosseum_crop | 36 | 32, 14 / 34 | none | 12.1% | |
| Colosseum_crop | 72 | 32, 16, 11 / 29 | none | 12.0% | 21.4 / 23.2 s |
| Colosseum_crop | 96 | 33, 17, 12 / 31 | none | 12.2% | 38.1 / 40.9 s |
| Colosseum_crop | 144 | 37, 17, 12 / 30 | none | 12.1% | 84.9 / 91.3 s |
| great_wall_land | 36 | none | none | 8.8% | |
| great_wall_land | 72 | - / 21 | none | 10.3% | 11.4 / 12.2 s |
| great_wall_land | 96 | - / 39 | none | 10.1% | 20.2 / 21.2 s |
| great_wall_crop | 36, 72, 96 | none | none | 15% | 14.2 / 15.0 s at 72 |
| pyramid_land | 36, 72, 96 | none | none | 15% | 29.7 / 31.7 s at 72 |
| Colosseum_land | 36, 72, 96 | none | none | off | 21.2 / 21.2 s at 72 |
| kitchen | 36 | none | none | off | |

The check adds about 7% to the analysis time (CPU), nothing to GPU time or memory. The control
stayed clean everywhere.

Objects are still found. Virtual boxes written into depth and colour; cluster size in voxels:

| Scene | Check | Box | Appeared (box in after clip) | Gone (box in before clip) |
| --- | --- | --- | --- | --- |
| kitchen | off by itself | 10% | found (223) | found (234) |
| kitchen | off by itself | 5% | found (44) | found (40) |
| Colosseum_land | off by itself | 10% | found (155) | found (97) |
| Colosseum_land | off by itself | 5% | found (34) | found (28) |
| Colosseum_crop | on, reach 14% | 10% | found (130 to 133), false clusters gone | found (99 to 100), false clusters gone |
| Colosseum_crop | on, reach 13% | 5% | found (32 to 34), false clusters gone | found (30), false clusters gone |
| kitchen | forced on, reach 12% | 10% | found (221 to 227) | found (227 to 231) |
| kitchen | forced on, reach 12% | 5% | found (44 to 47) | found (38 to 41) |
| Colosseum_land | forced on, reach 12% | 10% | found (154 to 158) | found (97 to 98) |
| Colosseum_land | forced on, reach 12% | 5% | found (34 to 36) | found (27 to 30) |

For the rows with the check on, the patch features were recomputed on the GPU from the painted
frames, with three textures: flat blue, a texture from another scene, and a texture cut from
the same scene (camouflage). All 36 variants (three scenes, two sizes, two directions, three
textures) were found. The "forced on" rows switch the check on where it is normally off, to
see whether it would eat objects there: cluster sizes are within a few voxels of the run with
the check off.

The committed `recon_change.py` was also run once end to end on the L4 (kitchen photos, 32 s
for the whole job): `recon.npz` holds the features as (frames, 25, 37, 256) float16 and
`report.json` has `lookalike_check`.

## Criteria

| Criterion | Status |
| --- | --- |
| Five hard video cases and kitchen: zero clusters when nothing changed | Met at 36 frames; also at 72 and 96 frames (five video cases) and 144 frames (Colosseum_crop) |
| Boxes of 5% and 10% found in kitchen and Colosseum_land, both directions | Met |
| `python test_synthetic.py` passes | Met, with four new checks for the look-alike check |

## Not done, and limits

- No real object moved between two real clips has been tested. Every "object" here is a
  virtual box, which looks unlike its surroundings by construction.
- While the check is active, an object that moved less than `reach` is not reported, and a
  new object within `reach` of one that looks the same is not reported. With well-aligned
  clips the check is off and this does not apply.
- The reach rule (15 x excess noise) is set from one failing case (Colosseum_crop needs 10%;
  5% and 8% leave one cluster of 11 and 9 voxels). It held on the 72 and 96 frame runs, which
  were not used to set it, but that is the same footage.
- great_wall_crop and pyramid_land are clean only because their thresholds are wide (changes
  under about 24% of the viewing distance are not reported). Aligning the clips first (section
  2) should tighten that; it is not implemented.
- 144 frames were run for Colosseum_crop only.
