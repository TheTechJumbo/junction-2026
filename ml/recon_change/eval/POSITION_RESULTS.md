# Where the wearer is in the venue's splat: held-out test (Sun 15 Nov 2026)

Question: does the glasses' position land where the frame was taken in the trained Gaussian splat of zone `kitchen`
(the venue), and how far off is it? Measured on the venue recording, without a new recording; the live check at named
spots is still to do (see the end).

## How it was measured

- Recording `20261115_025911_0a9dfa62` (1347 frames, 30 fps, glasses video). Trained splat
  `train-kitchen-20261115_025911_0a9dfa62-1`: 96 keyframes with known cameras in the splat's axes (cameras.npz).
  Room map: 48 pictures from the same recording. `/care/space` lines the splat up with the room map: 47 pairs,
  rms 0.315 m, 4.477 map metres per splat unit.
- Photo stream: every 10th frame from the first to the last keyframe (3 a second, as the app sends): 135 photos.
  **Scored: the 49 that are at least 4 frames from every trained keyframe and every room picture.** Each photo is
  sent as the app does (JPEG, long side 896) and, for the model, as the relay does (one photo, long side 518).
- Truth: the camera between the two trained keyframes around the frame (`care_space.pose_at`), taken into map axes
  with the same `/care/space` matrix the caretaker's page draws the splat with. Errors are horizontal (east, north),
  in the map's metres.
- **easy**: everything as recorded. **hard**: every keyframe and room picture within ±45 frames (±1.5 s) of the photo
  left out, a stand-in for a walk that does not retrace the recording.
- Script: `eval/position_heldout.py` (cluster: one L4 for the model's methods, a CPU node for the rest).
  Raw per-frame answers: `poseval/out/heldout_{gpu,cpu,cpu_final}.json` on the cluster's scratch (no pictures).

Caveats: the photos come from the recording the splat and the room map were made from, so matching is easier than for
a new visit (the hard variant only partly makes up for it). The metres are the room map's own (an assumed 1.5 m median
viewing distance, `locate.TYPICAL_M`), not checked against a tape. The recording's own cameras jump about 5 m between
frames 870 and 880 (a break in VGGT's poses), so the truth there is wrong and that one photo gives every method's
largest error. Heading truth is interpolated over 0.37 s between keyframes, which adds error on fast head turns.

## Each way of placing one photo

| Method | variant | placed | median | p95 | heading median / p95 |
|---|---|---|---|---|---|
| Today: model's full fix (worker.where, VGGT, ~4 s on the L4) | easy | 43/49 | 0.16 m | 3.49 m | 4.1° / 18.6° |
| | hard | 44/49 | 0.57 m | 3.06 m | 4.6° / 31.0° |
| Today: instant fix (room-map picture match, with its chained references) | easy | 49/49 | 0.25 m | 3.10 m | 6.2° / 99° |
| | hard | 49/49 | 1.56 m | 5.50 m | 13.4° / 157° |
| VGGT with the 8 most alike trained keyframes, aligned on their cameras (~2 s on the L4) | easy | 49/49 | 0.12 m | 2.01 m | 2.6° / 33.7° |
| | hard | 49/49 | 0.22 m | 4.59 m | 3.1° / 85.8° |
| Instant match against the trained keyframes (their own camera) | easy | 49/49 | 0.09 m | 1.20 m | 2.9° / 60° |
| | hard | 49/49 | 1.64 m | 6.24 m | 10.0° / 142° |
| **New: SIFT + PnP against the trained keyframes (`splat_locate.py`, CPU)** | easy | 46/49 | **0.05 m** | **0.27 m** | 1.9° / 14.3° |
| | hard | 30/49 | **0.03 m** | **0.72 m** | 1.5° / 11.5° |

(PnP rows: final code, tracking mode, i.e. the last pose guides which keyframes are matched; without it: easy 47/49,
0.05 m / 0.28 m; hard 29/49, 0.02 m / 0.77 m. 0.21 s median per photo on a cluster CPU core set, 0.3–0.6 s on the
laptop.)

**Where today's error comes from.** The room map's own cameras put the frames 0.11 m (median) and 0.58 m (p95) from
the splat's: that is the fit. Today's full fixes are 0.27 m (median) and 3.5 m (p95) from the room map's own cameras,
about as far as from the truth. So almost all of today's error is the fix itself: each full fix builds its own axes and
scale from the 12 pictures it picked, so the same spot lands up to 3–4 m apart from one fix to the next. The fit adds
a few tens of centimetres on top.

## What the pages show: before and after

Over the same stream, as each page draws it. Before: the 3D viewer drew the newest fix (instant, or the model's every
4 s); the caretaker's page drew the steady pose of track.py (made from the model's fixes only), else the newest fix.
After: both draw the splat pose: every photo placed by PnP, then `PoseFilter` (constant velocity, nothing faster than a
walk, a disagreeing fix held back until a second agrees, the uncertainty growing with time between fixes).

| | variant | median | p95 | within 0.5 m | moves > 1 m between photos | inside the stated 95% ring |
|---|---|---|---|---|---|---|
| Before, 3D viewer | easy | 0.22 m | 3.10 m | 73% | 42 (largest 6.3 m) | no ring |
| Before, caretaker's page | easy | 0.42 m | 4.11 m | 55% | 4 (largest 3.5 m) | no ring |
| **After, both pages** | easy | **0.06 m** | **0.67 m** | **94%** | **1** (1.6 m) | 92% |
| Before, 3D viewer | hard | 1.32 m | 5.50 m | 43% | 52 (largest 7.7 m) | no ring |
| Before, caretaker's page | hard | 0.43 m | 6.97 m | 51% | 2 (largest 6.5 m) | no ring |
| **After, both pages** | hard | **0.16 m** | 3.01 m | 57% | 9 (largest 4.2 m) | 92% |

A second run of the same test with an earlier version of the code (4000 features per photo, before the speed-up) gave
easy 0.05 m / 0.49 m (96% inside the ring) and hard 0.20 m / 2.40 m (86%): with 49 photos the p95 is the two or three
worst, so it moves by tenths of a metre between runs. In the hard variant PnP places 30 of 49 photos; the rest of the
error is the pose held (and its ring growing) while the wearer walks on unseen, and the larger moves are the pose
catching up when the next photo is placed.

## What /where carries

`position`, `heading_deg`, `pitch_deg` in the room map's axes (the ones /care draws in), and on both the fix and
`stable`: `source` ("fused": the splat pose below; "full": the model's fix; "instant": the cosine match) and
`uncertainty_m` (the 95% radius in metres; null when unknown, i.e. for "full" and "instant"). The fused fix also
has `splat_pose` (keyframe matched, its photo, inliers, the raw fix, the trail); a model fix overridden by it keeps
`model_pose`. The marker and ring on the pages are the caretaker session's (patch handed over).

## Choices made by these numbers

- **PnP against the splat's own keyframes, on the laptop.** Most accurate by far and 10–20 times faster than any model
  pass. It needs the keyframes' depth (`depths.bin`, trusted only where two keyframes agree) plus features
  triangulated between neighbouring keyframes with their known cameras (8,279 added to 51,438): without them,
  57 instead of 71 of the 96 keyframes were placed when each was left out in turn (the trusted depth misses most
  corners SIFT finds: 8 of 234 matches between two neighbouring keyframes had a depth).
- **The answer is in the splat's axes**, then through the `/care/space` matrix: the page draws the splat with the same
  matrix, so the wearer is drawn where the splat says. The room-map-to-splat fit no longer adds error.
- **Not used:** VGGT against the keyframes (worse, needs the GPU and a worker change; on the photos PnP missed it was
  0.67 m median and 8.9 m p95, so it cannot fill those gaps either); the instant match against keyframes (1.6 m median
  in the hard variant); today's full fix as a position (kept for items, watched spots and the change gate, its own pose
  kept beside as `model_pose`).
- **Uncertainty:** the PnP covariance from the inliers' residuals, times 8 (`SIGMA_GAIN`, so that ~95% of held-out errors
  fall within 2.45 sigma), never under 0.12 m in the steady pose (a 95% ring of at least 0.29 m), growing between photos
  at the wearer's own speed (at least 0.15 m/s).
- **Camera:** the glasses stream at 504 x 896 (DAT SDK, MEDIUM), not the recording's 3:4. The relay tries five focal
  lengths on the first four placed photos and settles on the best (×1.0 unless another gets 10% more inliers).

## Still to check

- **Live, at named spots** (Seb at "our table", "snack table", "drinks fridge" with the glasses streaming): not done yet.
  It needs the main relay restarted with this code.
- The true scale (a tape measure between two named places, `POST /scale`): all metres here are the room map's.
- A new visit on another day (lighting, moved chairs): the hard variant is only a stand-in.
- Each model full fix still holds the app's single request for ~5 s every ~4 s; the ring grows during it.
