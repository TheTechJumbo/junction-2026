# Lost items: how well the detector finds small things

Sat 14 Nov 2026. Branch `jun-26-lost-items`, PR 15. Code: `items.py`, the hook in `live/worker.py`,
`eval/items_fetch.py`, `eval/items_eval.py`, `eval/items_fullpath.py`, `eval/test_items.py`.

## In short

- The position fix now also looks for `keys, glasses, wallet, phone, remote control` in the wearer's
  newest picture and puts each one found on the room map. Detector: OWLv2
  (`google/owlv2-base-patch16-ensemble`, Apache 2.0) through `transformers`.
- It is set to say nothing rather than guess. At the chosen score limit of 0.5 it gave **no false
  detection on 324 photos that hold none of the items**, and 1 wrong answer in 78 on test photos.
- The price: it **misses most small things**. Of labelled objects it found 8 % of those under 32
  pixels, 27 % of those 32 to 64 pixels and 46 % of those over 64 pixels (long side, as the
  detector sees the whole picture). Keys are the worst item, remotes the best.
- It adds **0.8 s to a fix on the L4 GPU** (2.4 s to 3.2 s) and **5.0 s on the CPU worker** (26.3 s
  to 31.4 s).
- Looking again at a remembered spot (`items_checked`) is in. It uses a lower limit of 0.3 for
  that one question; even so about one clear, close look in five at a present item comes back
  "not detected" on the test photos (keys one in four, wallets one in two).
- Nothing here was tested on a real room with real keys in it. The 3D placement was checked with
  an item painted into a photo of the kitchen baseline: 2 cm from a named place at the same spot.

## The test photos

There is no recording of a real home yet, so the test set is public: ordinary photos from COCO
with the hand-drawn LVIS v1 validation labels (no sign-up for either). `eval/items_fetch.py` took,
for each item, up to 70 photos that have it (keys only have 43, wallets 24) and 50 photos that
were checked not to have it: 515 photos, 582 labelled objects. Photos with an even id are the
"tune" half, used to choose the settings; the tables below are from the other, "report" half
unless they say otherwise.

Read the numbers with these in mind:

- The detector was partly trained on the LVIS training photos with the same label names. The test
  photos are not among them, but the numbers are probably kinder than a real home would be.
- On the other hand these photos are hard in ways a table top is not: phones held to an ear,
  glasses on faces across a street, keys hanging in a lock.
- Keys and wallets are few (104 and 29 objects), so their rows are rough.
- LVIS labels a different thing than we ask for with keys: every single key gets a box, the
  detector boxes the bunch. So besides the usual strict rule (boxes overlap with intersection over
  union of 0.5) the tables use a looser one, which is what an arrow needs: the middle of the answer
  lies inside the label, or the middle of the label inside the answer and the answer is at most 6
  times the label's area. I looked at 20 answers that only pass the loose rule: nearly all were
  on the item or on the hand holding it. Tables give the loose count and the strict one in
  brackets.
- "Wrong" is counted per answer the glasses would give (the best box per item and photo): the
  photo was checked not to have the item, or has it with every instance labelled and the answer
  is on none of them.
- Object size is the long side of the label in pixels after the photo is scaled to the detector's
  input (long side 960). A 1280 pixel picture from the glasses is shrunk to 3/4 for that pass, so
  "under 32" means under 43 pixels in `big.jpg`.

## Choosing the settings (tune half)

What the detector is asked for matters: "glasses" alone also finds drinking glasses. The shipped
phrases are `keys / a key`, `eyeglasses / spectacles`, `a wallet`, `a mobile phone / a cell
phone`, `a remote control`. A wider list found a few more and had more false detections on
item-free photos at every limit below 0.45; the plain names found fewer.

Looking at overlapping square tiles besides the whole picture (2 extra passes for a 16:9 or 4:3
picture) helps at every limit. Tune half, 279 labelled objects, 132 item-and-photo pairs where the
item is checked absent:

| tiles | limit | found under 32 px | found 32 to 64 px | found over 64 px | right answers | wrong answers | false on 174 item-free photos |
|---|---|---|---|---|---|---|---|
| none | 0.3 | 18 of 52 | 54 of 81 | 122 of 146 | 122 | 6 | 1 |
| none | 0.4 | 10 of 52 | 34 of 81 | 97 of 146 | 93 | 1 | 0 |
| none | 0.45 | 6 of 52 | 24 of 81 | 72 of 146 | 69 | 0 | 0 |
| none | 0.5 | 3 of 52 | 20 of 81 | 53 of 146 | 55 | 0 | 0 |
| 1 row | 0.3 | 20 of 52 | 61 of 81 | 123 of 146 | 126 | 6 | 1 |
| 1 row | 0.4 | 14 of 52 | 44 of 81 | 111 of 146 | 109 | 2 | 0 |
| 1 row | 0.45 | 11 of 52 | 37 of 81 | 87 of 146 | 92 | 0 | 0 |
| 1 row | 0.5 | 5 of 52 | 25 of 81 | 63 of 146 | 68 | 0 | 0 |
| 2 rows | 0.3 | 23 of 52 | 62 of 81 | 124 of 146 | 125 | 8 | not run |
| 2 rows | 0.4 | 18 of 52 | 53 of 81 | 111 of 146 | 115 | 7 | not run |
| 2 rows | 0.45 | 14 of 52 | 50 of 81 | 97 of 146 | 109 | 5 | not run |
| 2 rows | 0.5 | 11 of 52 | 42 of 81 | 85 of 146 | 95 | 3 | not run |

One row of tiles is the shipped setting. Two rows (about 9 passes) find clearly more of the small
things at the same limit, but they also bring wrong answers back (3 at 0.5, where one row has
none), cost three times the time, and were not run on the item-free photos. They are worth a
second look with a limit of their own (`RECON_ITEMS_TILES=2`).

How the limit was chosen, in the order it happened:

1. From the table above I took 0.45 with one row of tiles: the lowest limit with no wrong answer
   on the tune half and no false detection on the item-free photos I had then (the 24 kitchen
   baseline photos and 150 PASLCD photos, which turned out to be the five outdoor scenes only).
2. Then I ran the five indoor PASLCD scenes (150 photos). At 0.45 there were 2 false detections,
   both "remote control 0.49" on the same white tube lying on a lunch-room table.
3. Because a wrong "it is over there" is worse than "I have not seen it", the shipped limit is
   **0.5**: no false detection on any of the 324 item-free photos. `RECON_ITEMS_SCORE=0.45` gets
   back about half again as many finds (see below) at the price in step 2.

## Results (report half, 1 row of tiles)

Limit 0.5, the shipped setting. 303 labelled objects.

| item | found, under 32 px | found, 32 to 64 px | found, over 64 px | found, all | photos: right answer | wrong: item not there | wrong: on something else | said nothing, item there |
|---|---|---|---|---|---|---|---|---|
| keys | 1 (0) of 14 | 2 (1) of 17 | 4 (3) of 25 | 7 (4) of 56 | 4 | 0 of 33 | 0 | 15 |
| glasses | 1 (1) of 22 | 3 (3) of 25 | 9 (9) of 24 | 13 (13) of 71 | 13 | 0 of 28 | 0 | 29 |
| wallet | 0 (0) of 0 | 0 (0) of 1 | 8 (8) of 19 | 8 (8) of 20 | 7 | 0 of 28 | 1 | 8 |
| phone | 2 (2) of 16 | 4 (4) of 18 | 16 (16) of 33 | 22 (22) of 67 | 21 | 0 of 20 | 0 | 29 |
| remote control | 1 (1) of 13 | 15 (14) of 29 | 31 (29) of 47 | 47 (44) of 89 | 32 | 0 of 31 | 0 | 5 |
| **all** | 5 (4) of 65 | 24 (22) of 90 | 68 (65) of 148 | 97 (91) of 303 | 77 | 0 of 140 | 1 | 86 |

Limit 0.45, for comparison:

| item | found, under 32 px | found, 32 to 64 px | found, over 64 px | found, all | photos: right answer | wrong: item not there | wrong: on something else | said nothing, item there |
|---|---|---|---|---|---|---|---|---|
| keys | 1 (0) of 14 | 4 (3) of 17 | 6 (5) of 25 | 11 (8) of 56 | 7 | 0 of 33 | 1 | 11 |
| glasses | 3 (3) of 22 | 6 (6) of 25 | 15 (15) of 24 | 24 (24) of 71 | 21 | 0 of 28 | 0 | 21 |
| wallet | 0 (0) of 0 | 1 (1) of 1 | 8 (8) of 19 | 9 (9) of 20 | 8 | 0 of 28 | 1 | 7 |
| phone | 7 (7) of 16 | 10 (10) of 18 | 21 (20) of 33 | 38 (37) of 67 | 30 | 0 of 20 | 0 | 20 |
| remote control | 2 (1) of 13 | 17 (16) of 29 | 41 (37) of 47 | 60 (54) of 89 | 34 | 0 of 31 | 0 | 3 |
| **all** | 13 (11) of 65 | 38 (36) of 90 | 91 (85) of 148 | 142 (132) of 303 | 100 | 0 of 140 | 2 | 62 |

Bigger is not better beyond 64 pixels on these photos (both halves, limit 0.5): 65 of 148 found at
64 to 128 pixels, 42 of 89 at 128 to 256, 24 of 57 above. What is missed at that size is missed
for other reasons than size: half hidden, held in a hand, seen edge-on.

## Photos that hold none of the items

Best answer per item and photo, 1 row of tiles. PASLCD photos were shrunk to 1280 pixels first,
as the relay does. I did not check by eye that all 324 photos are free of the five items; I
looked at every detection from 0.3 up, and none was on one of them except "keys 0.36" on a key in
a cupboard lock, and possibly a control panel next to a TV called a remote.

| photos | number | false at 0.3 | at 0.4 | at 0.45 | at 0.5 |
|---|---|---|---|---|---|
| kitchen baseline (the demo room) | 24 | 1 | 0 | 0 | 0 |
| PASLCD outdoor scenes (Garden, Playground, Porch, Pots, Zen) | 150 | 0 | 0 | 0 | 0 |
| PASLCD indoor scenes (Cantina, Lounge, Lunch room, Meeting room, Printing area) | 150 | 18 | 3 | 2 | 0 |

Nearly all of them are "remote control" on something long and boxy: a whiteboard eraser, a white
tube, the edge of a TV. One was "keys" on a keyboard, one "phone" on a printed picture of a phone.

## Seconds

Detector alone on a 1280 x 720 picture, median of repeated calls, and a whole fix through
`worker.where()` (kitchen baseline, one new frame plus `big.jpg`):

| | L4 GPU (12 baseline photos) | 32 EPYC cores (8 baseline photos) |
|---|---|---|
| whole picture only (1 pass) | 0.25 s | 1.39 s |
| 1 row of tiles (3 passes), shipped | 0.76 s | 4.94 s |
| 2 rows of tiles (9 passes) | 2.28 s | 13.85 s |
| fix without items | 2.38 s | 26.3 s |
| fix with items | 3.16 s | 31.4 s |
| loading the detector at worker start | 2 s | under 1 s |

On the CPU worker `RECON_ITEMS_TILES=0` brings the added time down to about 1.4 s and loses
roughly a fifth of the finds (tune half at limit 0.5: 76 objects found instead of 93).
The detector's load time is with its weights already in the file cache.

## Where an item goes on the map

`items.place` maps the box from the picture the detector saw (`big.jpg`, uncropped) into the
cropped 518 pixel frame of the fix, reads the depth in the middle half of the box, and puts the
point on the map with `locate.map_frame`, the same transform the named places use (it now lives
in that one function). Distance is along the floor, bearing 0 is straight ahead and positive is
to the right, as for places. `eval/test_items.py` checks offline that an item box on a spot gets
the same position, distance and bearing as a place picked at that spot.

### One whole fix, with items painted in (`eval/items_fullpath.py`)

No recording has a real item in it, so a labelled phone from a test photo was pasted, 80 pixels
long, into a full-size photo of the kitchen baseline, and a remote onto a mirrored strip added
at each side to make the wearer's picture 16:9 (1280 x 720), wider than the baseline photos. The
paste was picked so that the detector finds it: this run tests the placement and the plumbing,
not the detection rate. The same spot is also named as a place in the baseline photo of that
view, so one fix gives two independent positions for it: the place from the baseline frame's
depth, the item from the new frame's depth. The fixes went through `worker.where()` itself.

| case | what came back |
|---|---|
| phone pasted on the table mat, L4 GPU, 12 baseline photos | found (match 1.0). Phone 0.59 at [0.69, 1.37, -0.88], 1.4 m, -14 degrees. The place picked at the same spot: [0.70, 1.38, -0.87], 1.4 m, -13 degrees. **2 cm apart.** Placed with the help of the surface around it (see below). |
| the same, CPU worker settings (8 baseline photos) | found (match 1.0). Phone 0.59 at [0.70, 1.41, -0.74], 1.2 m, -15 degrees; the place at [0.71, 1.42, -0.73], 1.2 m, -14 degrees. **2 cm apart.** |
| the same without `big.jpg` (the detector sees the 640 pixel frame) | found, but no item: the pasted phone scores 0.48 there, below the limit. The full-size picture matters. |
| item pasted on the strip outside the crop (runs at limit 0.3; at 0.5 it was not found) | listed with box and crop, no position, note "Seen at the edge of the picture, outside the part the position fix used." |
| photo of another place | state `lost`, nothing placed. (The offline check covers an item found on a lost fix: listed, position null.) |
| detector not loaded (simulated) | the fix comes back as before with `"items": []`, `"items_looked_for": []` and an `items_note` that says why |
| unreadable `big.jpg` | the fix comes back; the detector used the newest numbered frame instead |
| 3D view asked for | the splat is written with one more ball, for the item (not looked at in the viewer) |

**One thing this showed, and where the contract was bent.** The depth on the pasted item itself
was not trusted by the position fix's own rule in any run (for example 34 of 231 pixels in the
middle of the box): the model is unsure about something that is in one frame and not in the
baseline photos. By the letter ("no position on untrusted depth") no pasted item would ever have
been placed. So `place` now asks the surface in a band around the box: if enough of it is
trusted and the model's depth on the item is within 15 % of that surface's depth, the item is
placed with its own depth and says so in its `note`; otherwise it gets no position as before.
`RECON_ITEMS_SURFACE=0` switches this off. Whether real objects behave like pasted ones is not
known.

Looking again at the remembered spot, same runs (the GPU and the CPU run agree):

| the newest frame shows | in_view | blocked | close_enough | detected_near |
|---|---|---|---|---|
| the item still there | true | true (the depth on the item is not trusted) | true (65 px at 1.4 m) | true |
| the item taken away | true | false (depth gap -0.01 m) | true | false |
| a grey card painted over the spot | true | true (depth not trusted) | true | false |
| a remembered spot 68 m away, behind the wearer | false | false | false | false |

A thing that is new at the spot (the item itself, or a card over it) made the depth there
untrusted in these runs, which reads as `blocked`. That errs on the safe side: such a look does
not count as a clear look at an empty spot.

## Looking again at a remembered spot (`items_checked`)

When the request carries `remembered` (name and map position of earlier sightings), the fix
reports four facts per spot for the newest frame, and no verdict:

- `in_view`: the spot projects in front of the camera and inside the frame the fix used, at
  least 3 % of the frame away from its edge.
- `blocked`: the depth seen at that pixel is nearer than the spot by more than 12 % and more than
  10 cm, or the depth there is not trusted.
- `close_enough`: the item would be at least **64 pixels** long in the detector's whole-picture
  pass, from the rough real sizes keys 8 cm, glasses 14, wallet 10, phone 15, remote 18 and the
  frame's focal length. For a 1280 x 720 picture with a 70 degree view that is roughly keys within
  0.9 m, glasses 1.5 m, wallet 1.1 m, phone 1.6 m, remote 1.9 m. 64 pixels is where the tables
  above stop getting better with size. Map metres are rough (the model has no real scale), so
  these distances are too.
- `detected_near`: the detector found that item within 0.5 m of the spot. For this question only
  the score limit is 0.3 (`RECON_ITEMS_CONFIRM`): the place is already known, so a weaker box
  there is enough, and a low limit misses a present item far less often. It never makes a sighting.

Also given: `pixel` (0 to 1 in the picture the detector saw), `distance_m` (along the floor),
`size_px` and `depth_gap_m` (what the two decisions were made from).

**How often a present item is reported as not detected**, per look, at the close-enough size
(labelled objects over 64 pixels, both halves, 1 row of tiles):

| item | missed at limit 0.3 (used for `detected_near`) | missed at limit 0.5 |
|---|---|---|
| keys | 13 of 50 (26 %) | 37 of 50 (74 %) |
| glasses | 6 of 55 (11 %) | 34 of 55 (62 %) |
| wallet | 13 of 26 (50 %) | 16 of 26 (62 %) |
| phone | 11 of 78 (14 %) | 48 of 78 (62 %) |
| remote control | 12 of 85 (14 %) | 28 of 85 (33 %) |
| **all** | 55 of 294 (19 %) | 163 of 294 (55 %) |

So even with the lower limit about one clear, close look in five at a present item comes back
"not detected" on these photos (one in four for keys, one in two for wallets). Two looks at the
same thing from the same side are not two independent tries: what hides the item once hides it
again. Two missed looks are thin evidence that an item is gone; "I am not sure" is the honest
wording until several looks from different sides agree. The other error also exists: at 0.3 the
detector made 18 false detections on 150 indoor item-free photos, so now and then something else
within 0.5 m of the spot will keep a sighting alive.

## What was not tested

- A real room, real keys, the glasses' camera, motion blur, dim light, items partly under
  something. Every number above is from public photos or painted-in items.
- Whether an item that is new in the room gets trusted depth. In the painted-in runs it did not
  (see above); real objects may behave differently.
- The same item seen from the glasses at 2 to 4 m: by the size tables it will mostly be missed.
- More than one item of a name in view: the fix reports the best one only.
- GPU memory with both models loaded was not measured (VGGT with 15 frames plus OWLv2 base fit on
  the 23 GB L4 without trouble).
- `transformers` newer than 4.46.3, and other OWLv2 sizes (the large one may find more, slower).
- 2 rows of tiles on the report half and on item-free photos.
- A worker started by `iridis/worker.slurm` with the detector: the runs here call
  `worker.where()` from a test script, not through the queue.

## Run it again

```
bash iridis/setup_items.sh /scratch/$USER/junction-recon/items      # packages and weights, next to .venv-recon
python eval/test_items.py                                           # offline checks, no model
python eval/items_fetch.py --lvis lvis_v1_val.json --out items/data/lvis
cd items && sbatch code/eval/items_eval.slurm                       # boxes for every photo (GPU)
python code/eval/items_eval.py score --set data/lvis/set.json --img data/lvis/img --out out/eval --tiles 1 --limit 0.5
sbatch code/eval/items_fullpath_gpu.slurm ; sbatch code/eval/items_fullpath_cpu.slurm
```
