# Watched spots: can one marked place be checked for a small change? (PASLCD)

- **Date:** 2026-11-14. Branch `jun-22-watch-spots`, based on `jun-22-hud-waypoints`.
- **Code:** `ml/recon_change/watch.py` (`check(r, base, new, spots)`), offline test
  `ml/recon_change/test_watch.py`.
- **Data:** the PASLCD benchmark of `RESULTS.md`: 10 real scenes, hand-drawn change masks, 24
  baseline photos against 12 query photos, everything at the 518 x 294 the live system stores
  unless a table says otherwise.
- **Hardware:** Iridis, CPU cores only (`amd_student`). No GPU job was run: every number comes
  from the reconstructions the recall work saved (`recall/out/base`, `recall/out/base_inst2`).
  Final measurement: array jobs 2220659 (same lighting) and 2220660 (other lighting); close-up
  features: 2220652; contact sheets: 2220740 to 2220742.

## Headline

Seb asked: "what about a small change, and can we specify the exact region that changes?"
Yes. A carer marks a box in a baseline photo. For that one place the check answers `same`,
`changed` or `not_seen`. On small changes it catches about 10 times as many as the whole-room
detector.

Five scenes the limits were **not** chosen on (Printing_area, Meeting_room, Zen, Playground,
Porch), each spot checked against the one query photo its change was labelled in:

| Size of the change (pixels at 518 x 294) | Changes | Caught, "5%" limit | Caught, "1%" limit | Not seen | Whole-room detector (geometry) | Whole-room + looks different (PR 11) |
| --- | --- | --- | --- | --- | --- | --- |
| small, 50 to 199 | 101 | 75 (74%) | 62 (61%) | 13 (13%) | 7 (7%) | 9 (9%) |
| medium, 200 to 799 | 128 | 97 (76%) | 90 (70%) | 19 (15%) | 59 (46%) | 84 (66%) |
| large, 800 and more | 100 | 62 (62%) | 60 (60%) | 36 (36%) | 65 (65%) | 82 (82%) |
| all | 329 | 234 (71%) | 212 (64%) | 68 (21%) | 131 (40%) | 175 (53%) |

- **False alarms on these five scenes:** 16 of 185 unchanged spots (9%) at the "5%" limit and 2
  of 185 (1%) at the "1%" limit. The "5%" limit was set to give 5% on the other five scenes and
  drifted to 9% here; the "1%" limit held. The whole-room detector raised no false alarm on the
  same spots.
- **Of the spots it had a clear look at**, it caught 85% of small, 89% of medium and 97% of large
  changes at the "5%" limit (70%, 83%, 94% at the "1%" limit). Area under the ROC curve: 0.97
  small, 0.93 medium, 1.00 large.
- **`not_seen` is the price for large things:** a third of the large changes got no clear look
  from the single photo (seen from too different a direction, or the new thing stood in front of
  the watched surface). Given all 12 new photos, as a live check is, 3% of spots stay unseen.
- **The scenes differ a lot.** On the five scenes used for choosing, small changes were caught
  60% of the time, not 74%. Over all ten: 66% small, 72% medium, 68% large at the "5%" limit;
  52%, 64%, 63% at the "1%" limit.

## What was measured

- **Changed spots (positives).** Every connected blob of a change mask, 50 pixels or more, in
  every query photo (682, the same regions and size bands as `RESULTS.md`). Its 3D points (query
  depth inside the blob) are projected into the baseline frames; the box around them in the
  baseline frame that sees the place from the most similar direction is the watched spot. 16
  regions have no baseline frame that sees them whole; they count as not seen.
- **Unchanged spots (negatives).** (a) For each changed spot, a box of the same size at a random
  place of a random baseline frame, which lands in the same query photo and stays clear of every
  labelled change in all 12 query photos. Random boxes fall mostly on bare walls, so the first
  candidate with at least 0.7 times the texture of the changed spot is taken (median colour
  spread in the box: 18 for both kinds). 478 such boxes; few for large changes (26), because a
  large box rarely fits clear of the changes. (b) All those boxes, changed and unchanged, checked
  in the unchanged pairs (the same baseline against 12 other photos of the same capture): 1144.
- **One photo per spot.** The size bands are the size of the change in one query photo, so each
  spot is checked against that one photo. The unchanged pairs use one randomly chosen photo with
  a look at the spot. A separate table gives the live case, all 12 new photos.
- **Limits chosen on five scenes, reported on the other five.** Which evidence to use, its
  weights, the angle limit and the two score limits were chosen on Cantina, Lounge, Lunch_room,
  Garden and Pots ("choose" scenes; `watch_explore.py` reads nothing else). The limits are the
  scores that 5% and 1% of the choose scenes' 721 seen unchanged spots exceed. The tables below
  are for the other five ("report" scenes) unless they say otherwise. I did see report-scene
  tables during development (the first run, and when checking the gain variant below); no choice
  was made from them.
- **Whole-room detector on the same regions.** `analyse` run on the same saved reconstructions;
  a region counts as found when 10% of it is flagged, as in `RESULTS.md`. Over all ten scenes
  this reproduces `RESULTS.md` (11/230, 128/281, 114/171) and the recall results (14/230,
  167/281, 137/171) exactly.

## Results

### As a live check would run: all 12 new photos, median score of the 3 best looks

Same limits. The size band is still the change's size in the photo it was labelled in.

| Scenes | Size | Spots | Caught, "5%" limit | Caught, "1%" limit | Not seen |
| --- | --- | --- | --- | --- | --- |
| report | small | 101 | 67 (66%) | 49 (49%) | 0 |
| report | medium | 128 | 102 (80%) | 95 (74%) | 2 (2%) |
| report | large | 100 | 88 (88%) | 82 (82%) | 8 (8%) |
| report | all | 329 | 257 (78%) | 226 (69%) | 10 (3%) |
| all ten | small | 230 | 141 (61%) | 95 (41%) | 0 |
| all ten | medium | 281 | 233 (83%) | 204 (73%) | 3 (1%) |
| all ten | large | 171 | 151 (88%) | 136 (80%) | 13 (8%) |

False alarms in this mode, report scenes: 1 of 224 matched unchanged boxes at the "5%" limit and
0 at the "1%" limit; in the unchanged pairs 14 of 542 (3%) and 5 of 542 (1%). All ten scenes: 13
of 469 (3%) and 0; unchanged pairs 22 of 1134 (2%) and 6 (1%). Taking the median of three looks
removes most false alarms and costs some small changes.

### Why a spot was not seen (one photo per spot, all ten scenes)

| | Spots | Seen from another side (over 10 degrees) | Blocked | No baseline frame sees it whole | Out of view | Too small |
| --- | --- | --- | --- | --- | --- | --- |
| Changed | 682 | 74 | 38 | 16 | 5 | 2 |
| Unchanged | 478 | 32 | 32 | 0 | 0 | 20 |

"Blocked" on a changed spot is usually the change itself: something was put in front of the
watched surface, nearer than the spot's own size, and the check cannot tell that from a person
standing there.

### False alarms by kind of unchanged spot (one photo per spot)

| Scenes | Unchanged spot | Seen | "5%" limit | "1%" limit |
| --- | --- | --- | --- | --- |
| report | matched box, changed pair (independent walk) | 185 | 16 (9%) | 2 (1%) |
| report | box of a changed region, unchanged pair | 259 | 25 (10%) | 9 (3%) |
| report | matched box, unchanged pair | 203 | 16 (8%) | 1 (0%) |
| choose | all three kinds | 721 | 35 (5%) | 6 (1%) |

More textured half of the report scenes' matched boxes: 13% and 1%; plainer half: 4% and 1%.
The false alarms looked at on the sheets were a ceiling light strip and a glass door: shiny or
glowing surfaces, where the model's depth is poor and the box lands in the wrong place.

### How small? Pixels across the spot in the new photo (report scenes, spots seen)

| Pixels across at 518 x 294 | Changed spots seen | Caught, "5%" limit | Caught, "1%" limit | Compared on the original photos (4036 pixels), "5%" limit |
| --- | --- | --- | --- | --- |
| 8 to 10 | 5 | 5 | 4 | 5 |
| 11 to 13 | 30 | 24 (80%) | 20 (67%) | 28 (93%) |
| 14 to 19 | 58 | 51 (88%) | 45 (78%) | 55 (95%) |
| 20 to 27 | 69 | 62 (90%) | 54 (78%) | 63 (91%) |
| 28 to 39 | 53 | 49 (92%) | 48 (91%) | 49 (92%) |
| 40 and more | 46 | 43 (93%) | 41 (89%) | 43 (93%) |

"Pixels across" is the square root of the area of the box the spot lands in.

**What becomes reliable:** from about 14 pixels across, 9 in 10 changes that are seen are caught
at the "5%" limit and 8 in 10 at the "1%" limit; from 28 pixels across, 9 in 10 at either limit.
Between 11 and 13 pixels it is 8 in 10 and 2 in 3. Below 11 pixels there are 5 spots, too few to
say; below 7 x 7 pixels PASLCD has no labelled change at this resolution, so it is untested, and
`watch.py` answers "too small in view" below 36 pixels of area.

In centimetres: size = pixels x distance / focal length. PASLCD is shot with a very wide lens
(the model puts the focal length at about 206 pixels for the 518-pixel frame, roughly 103
degrees across), so one pixel is about 0.5 cm per metre of distance:

| | at 1 m | at 2 m | at 3 m |
| --- | --- | --- | --- |
| 14 pixels across, PASLCD's lens | 7 cm | 14 cm | 20 cm |
| 28 pixels across, PASLCD's lens | 14 cm | 27 cm | 41 cm |
| 14 pixels across, a 70 degree lens (focal length about 370 pixels) | 4 cm | 8 cm | 11 cm |

So with this lens a pill box (about 10 cm) is reliably checked from about 1.5 m, and a mug from
about 2 m. The glasses' lens was not measured here; put its focal length into the formula.
Size is not the whole story: a clear plastic sheet on a white table was missed at 49 x 25
pixels, because it barely changes what the camera sees.

### Does it help to store bigger baseline photos? (report scenes, one photo per spot)

The geometry always comes from the 518-pixel frames. Only the colour comparison was made on
larger pictures (the PASLCD originals are 4036 x 2269, shrunk to each size); the limits were
chosen again for each size on the choose scenes.

| Photos stored with long side | Small: caught at "5%" / "1%" | Small: AUC | Medium: caught at "5%" / "1%" | Large |
| --- | --- | --- | --- | --- |
| 518 (now) | 75 (74%) / 62 (61%) | 0.970 | 97 (76%) / 90 (70%) | 62 / 60 |
| 1036 | 83 (82%) / 62 (61%) | 0.986 | 97 (76%) / 93 (73%) | 62 / 60 |
| 2072 | 83 (82%) / 66 (65%) | 0.990 | 97 (76%) / 94 (73%) | 62 / 60 |
| 4036 (original) | 84 (83%) / 66 (65%) | 0.992 | 97 (76%) / 94 (73%) | 62 / 60 |

**Twice the size is worth it for small spots, more is not.** Storing 1036-pixel photos catches 8
more of 101 small changes at the "5%" limit (none more at the "1%" limit); 2072 and 4036 add 4
more at the "1%" limit and nothing else. Spots of 20 pixels across and more gain nothing. This
needs the new frames at the larger size too, not only the baseline.

### A bigger gain than more pixels: features from a close-up (side experiment)

The model's patch features come from the whole frame: one feature per 14 x 14 pixels, so a
small spot is less than one patch. In `watch_crop_features.py` the spot and its ring are cut
out (baseline and the new frame laid over it), blown up to 224 x 224 and put through the same
DINOv2 backbone on their own. Report scenes, changed pairs only, spots seen, colour evidence
unchanged, weights and limits from the choose scenes:

| Feature from | Small: AUC | Small: caught, "5%" limit | Medium: caught | Large: caught | False alarms |
| --- | --- | --- | --- | --- | --- |
| the whole 518-pixel frame (what `watch.py` uses) | 0.969 | 71 of 88 (81%) | 97 of 109 (89%) | 62 of 64 | 8 of 185 (4%) |
| a close-up cut from the 518-pixel frames | 0.985 | 82 of 88 (93%) | 100 of 109 (92%) | 62 of 64 | 7 of 185 (4%) |
| a close-up cut from the original photos | 0.989 | 84 of 88 (95%) | 102 of 109 (94%) | 64 of 64 | 15 of 185 (8%) |

The close-up is what helps (11 more small changes of 88), and it needs no bigger photos. It is
**not in `watch.py`**: it costs two backbone passes per comparison (measured: about 0.2 s per
224 x 224 crop on 16 CPU cores; not timed on a GPU), and it was measured only here, not on the
unchanged pairs, the 12-photo mode or other lighting. At a "1%" limit the table would rest on
two unchanged spots, so I do not quote it.

### Different lighting (five indoor scenes, `Instance_2`)

The same query photos against a baseline taken in other lighting, on another walk. Limits from
the same-lighting choose scenes. Three of these five scenes are choose scenes.

| Baseline | Unchanged spots seen | False alarms, "5%" limit | "1%" limit | Changes caught, "5%" / "1%" limit | Changed spots not seen |
| --- | --- | --- | --- | --- | --- |
| same lighting | 238 | 18 (8%) | 3 (1%) | 260 / 210 of 398 (65% / 53%) | 22% |
| other lighting | 224 | 30 (13%) | 6 (3%) | 255 / 196 of 410 (62% / 48%) | 25% |

Other lighting, by size at the "5%" limit: 122 of 178 small (69%), 74 of 138 medium (54%), 59 of
94 large (63%); the whole-room detector on the same regions: 4, 26 and 50.

False alarms rise by about half at the "5%" limit and double at the "1%" limit, and the changes
are still found. A change of lighting together with a new walk is what a real second visit
looks like, so 13% and 3% are the honest false alarm rates, not 5% and 1%. The unchanged pairs of `Instance_2` (6% and 1%)
compare one capture with itself and say nothing about lighting.

The same five scenes in the live mode (all 12 new photos, median of the 3 best looks):

| Baseline | Changed spots | Caught, "5%" / "1%" limit | False alarms, "5%" limit | "1%" limit | Not seen |
| --- | --- | --- | --- | --- | --- |
| same lighting | 398 | 292 / 224 (73% / 56%) | 10 of 279 (4%) | 0 of 279 | 4% |
| other lighting | 410 | 295 / 221 (72% / 54%) | 27 of 283 (10%) | 5 of 283 (2%) | 4% |

## What evidence worked and what did not

Each kind alone, on the choose scenes, as the area under the ROC curve (small / medium / large):

| Evidence | AUC |
| --- | --- |
| Colour, worst quarter of the spot, minus the ring's difference | 0.83 / 0.94 / 0.98 |
| Colour, mean over the spot, minus the ring's | 0.75 / 0.88 / 0.91 |
| Patch features, minus the ring's, minus the same between baseline frames | 0.67 / 0.86 / 0.98 |
| Patch features, plain difference | 0.66 / 0.84 / 0.92 |
| Fine structure (correlation of the brightness pattern), same form | 0.65 / 0.79 / 0.91 |
| Depth disagreement, same form | 0.64 / 0.77 / 0.92 |

Weighted sums, fitted on four choose scenes and scored on the fifth, at 5% false alarms (share of
seen changes caught, small / medium / large):

| Score | Caught |
| --- | --- |
| Colour alone | 67% / 85% / 93% |
| Patch features alone | 27% / 66% / 86% |
| **Colour + patch features (used)** | **68% / 90% / 96%** |
| + fine structure | 67% / 90% / 96% (weight 0.00) |
| + depth | 68% / 90% / 96% (weight 0.00) |
| Colour also minus the baseline's own disagreement | 60% / 85% / 95% |

**Worked:**

- **Colour inside the box against the ring around it.** The new frame is laid over the baseline
  picture through the 3D points and nudged up to 5 pixels to line up; the ring's colour offset
  is removed; a difference only counts where the other picture has no such colour within a
  pixel. The worst quarter of the spot is used, so a change that fills part of the box counts.
- **Subtracting the ring is what handles lighting.** In the same lighting it changes nothing
  (the same share caught), but in other lighting the score without it gave 47 false alarms of
  224 (21%), with it 30 (13%).
- **Comparing with the baseline frame that looks from the most similar direction**, not with the
  frame the box was drawn in. Every baseline frame with a clear look at the spot is a reference;
  the box is carried over through the 3D points. An earlier run without this (three choose
  scenes, 5% false alarms) caught 40% of small, 68% of medium and 82% of large changes; the
  first run with it (all five choose scenes) 57%, 92% and 95%. The two runs differ in scenes
  too, so this is an indication, not a controlled comparison.
- **An angle limit.** False alarms on the choose scenes were 1% when the new view was within 3
  degrees of the reference view, 3% up to 6, 6% up to 10, 17% up to 15, and 20% and more beyond.
  A look from more than 10 degrees away now counts as not seen.
- **Patch features as a surplus**, as the recall work found: they help medium and large spots
  (medium 85% to 90%) and do nothing for small ones at this resolution.

**Did not work:**

- **Depth inside the spot.** Useless for small spots (AUC 0.64) and no gain in the sum.
- **Fine structure** adds nothing once colour is in.
- **Subtracting the baseline's own disagreement from the colour** made small spots worse (68% to
  60%). It is kept for the patch features only.
- **Plain patch features**, again: AUC 0.66 on small spots.
- **Matching a brightness gain as well as an offset** (measured as `a_top` in `watch.py`, no
  weight): the same in matching lighting, 23 false alarms instead of 30 in other lighting at the
  "5%" limit, no difference at the "1%" limit. Not adopted: 7 spots, and that comparison used
  the report scenes.
- **Taking only the main surface in the box as the spot** (not what shows past it) was neutral
  on the numbers (small 58% to 62%, medium 92% to 88% on the choose scenes). Kept, because
  otherwise the box lands as a long strip in other views.

## Seen on the contact sheets

`watch/out/sheets/<scene>.jpg` on Iridis: every spot of the scene after merging the regions of
one object (first one met in a shuffled order), each with a control box, checked in the live
mode (12 new photos, 518 pixels, limit 1.0). Nothing left out.

| Scene | Spots | Changed: right / wrong | Controls: right / false alarm / not seen |
| --- | --- | --- | --- |
| Lounge | 21 | 11 / 2 | 7 / 1 / 0 |
| Meeting_room | 26 | 11 / 4 | 10 / 1 / 0 |
| Lunch_room | 57 | 17 / 16 | 23 / 0 / 1 |

Lounge (looked at): found the stool taken away, the coffee drunk from the glass, the coffee
spilt on the table; missed two thin strips (31 x 5 and 19 x 8 pixels) that sit beside the
changed thing, not on it; one false alarm on a ceiling light. Meeting_room (looked at): found
the pens, the keyboard, the erased whiteboard marks; missed a clear plastic sheet on the white
table, two regions on a chair back and a 16 x 4 pixel strip; one false alarm on a glass door. Lunch_room is the hard scene of
`RESULTS.md` too (small things far away in a large room): half of its 24 small changes are
missed. I did not look at its sheet.

## What was not tested

- **A second visit to an unchanged room.** PASLCD's unchanged pairs come from one capture. The
  matched boxes in the changed pairs (independent walk) are the better evidence, and with other
  lighting they give 13% and 3%.
- **Video and the glasses' camera:** these are sharp 12 MP stills. No blur, no rolling shutter.
- **Boxes drawn by a person.** Here a box is the tight box around a labelled change. A loose box,
  or a box around an object that then moves a little inside it, was not tried.
- **Changes under 50 pixels of area**, and what a person standing in front does: "blocked" was
  only checked in the synthetic test.
- **The close-up features** beyond the side experiment above, and bigger pictures through the
  model itself (no GPU job).
- **Large unchanged spots:** only 26 matched boxes, so false alarms on large boxes are unknown.
- **Run-to-run spread:** the random control boxes use one seed. The final numbers were computed
  once; three earlier runs of the harness with changed rules are not comparable.
- **The live worker:** `watch.py` is not wired in. Speed, measured: `check` took 2.4 to 6.5 s
  for 21 to 57 spots with 24 baseline and 12 new frames on 4 CPU cores, about 0.1 s per spot.
- **A reconstruction without patch features** (`r.feat` is None): the score then rests on colour
  with the same limit, so it is less sensitive. Not measured.

## What this means for the demo

- **It answers Seb's question and is ready to show.** Mark the pill box in a baseline photo; a
  check says `same`, `changed` or `not_seen` with a reason and the box in the new frame. Changes
  the whole-room detector cannot see (a pen, a glass emptied, a spill, a whiteboard mark) are
  found about 2 times in 3.
- **Use limit 1.0 for the demo, about 1.8 for anything that alerts a carer.** In the live mode
  limit 1.0 gave up to 4% false alarms in matching lighting and 10% when the baseline was taken
  in other light; limit 1.8 (the "1%" limit) gave 0 to 1% and 2%, and still caught a bit over
  half of the changes on the indoor scenes.
- **`not_seen` is a normal answer, not a failure.** The wearer has to look at the spot from
  within about 10 degrees of where some baseline photo saw it, close enough for it to cover 14
  pixels across. More baseline photos around a watched spot mean more directions that count.
- **Draw the box tight around a thing with some colour.** Glass, clear plastic, lamps and
  mirrors are the misses and false alarms seen here.
- **Keep the whole-room detector for big things.** It finds 65 to 82% of large changes without
  false alarms and needs no box; the watched spot is for the small things that matter.
- **Baseline photos:** storing them (and the new frames) at 1036 pixels is worth it if small
  spots matter, not more than that. The larger step is the close-up feature, which needs no
  bigger photos but one backbone pass per spot.
- **To try it live:** `watch/out/live_Lounge/` on Iridis has the 24 baseline and 12 new frames of
  Lounge as `000.jpg` to `035.jpg` (long side 518), `spots.json` (name, baseline frame, box,
  labelled truth) and `expected.json` (what `watch.check` answered offline on the saved
  reconstruction; a fresh reconstruction of the JPEGs can differ slightly).

## How to re-run

On Iridis, in `/scratch/$USER/junction-recon`, with the saved reconstructions in `recall/out/`:

```bash
mkdir -p watch/code watch/logs
# copy watch.py, recon_change.py, eval/run_bench.py and eval/watch_*.py, eval/watch_*.slurm into
# watch/code/ ; after scp from Windows: sed -i 's/\r$//' watch/code/*
sbatch --array=0-9 watch/code/watch_eval.slurm 1 recall/out/base
sbatch --array=0-4 watch/code/watch_eval.slurm 2 recall/out/base_inst2
```

```bash
python watch/code/watch_report.py --eval watch/out/eval      # every table above
python watch/code/watch_explore.py --eval watch/out/eval     # the choice of evidence, choose scenes only
sbatch --array=0-9 watch/code/watch_crop.slurm               # close-up features, then watch_crop_report.py
sbatch watch/code/watch_sheets.slurm --scene Lounge --live   # contact sheet and live inputs
```

Each array task takes 2 to 3 minutes on 8 cores; the close-up features up to 6 minutes on 16.
The numbers in this file are from `watch/out/eval5`. `python test_watch.py` (no GPU, no data)
checks the projection, a blocked spot, a spot out of view or too small, a changed against an
unchanged spot, and an unchanged spot in brighter light (which passes with little room: score
0.87 against the limit 1.0 for a quarter more brightness on a high-contrast spot).
