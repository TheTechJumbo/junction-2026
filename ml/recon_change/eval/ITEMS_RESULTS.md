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

# Finding more (second round, Sat 14 Nov, branch `jun-26-items-recall`, PR 25)

The first round's finder says nothing rather than guess, and misses most things: 97 of 303
labelled objects found, keys 7 of 56. But the detector does see most of them and scores them
low. This round tries to accept those weak boxes safely. Code: `items.py` (everything new is off
unless set), `eval/items_recall.py` (every rule scored from saved boxes, no GPU),
`eval/items_second_look.py`, `eval/items_enrol.py`, `eval/items_enrol_real.py`.

## In short

| | found, over 64 px | keys over 64 px | found, all sizes | right answers | wrong answers | false on item-free photos |
|---|---|---|---|---|---|---|
| words only, limit 0.5 (deployed) | 68 of 148 (46 %) | 4 of 25 (16 %) | 97 of 303 | 77 | 1 | 0 of 162 |
| best single-frame rule: a limit per item, and a second opinion (CLIP) on weak boxes | 107 of 148 (72 %) | 14 of 25 (56 %) | 201 of 303 | 137 | 3 | 0 of 162 |
| show it once (enrolled picture as the question) | not measured in full: the test is built, its GPU run was still queued behind other jobs when this was written | | | | | |
| evidence over several looks | not measurable from single photos: see "Adding up looks" for what the saved boxes say | | | | | |

Held-out half of the test photos, and the half of the 324 item-free photos that no setting was
chosen on. The target was 70 % of all items over 64 px, 60 % of keys over 64 px, and at most 1
false answer per 100 item-free photos. **All items: met (72 %). False answers: met (0 of 162).
Keys: not met, 56 %, one object short of 60 % on 25 keys.** A stricter variant that also asks the
zoomed detector reaches 64 % for keys and 74 % overall with 2 false answers in 162 photos, which
is over the limit. With 25 keys neither difference means much.

## What was tried, one step at a time

Every setting was chosen on the tuning half (test photos with an even id, plus every second
item-free photo) and is reported on the other half. A setting may make at most one error per item
on the tuning half, and a margin of 0.025 is added to every limit found. "Wrong" is an answer on
a test photo that is not on the item; "false" is an answer on an item-free photo.

| rule | found under 32 px | found 32 to 64 px | found over 64 px | keys over 64 px | right answers | wrong answers | false on item-free photos |
|---|---|---|---|---|---|---|---|
| today: limit 0.5 for all | 5 of 65 (8 %) | 24 of 90 (27 %) | 68 of 148 (46 %) | 4 of 25 (16 %) | 77 | 1 | 0 of 162 |
| 1. a limit per item | 32 of 65 (49 %) | 57 of 90 (63 %) | 95 of 148 (64 %) | 9 of 25 (36 %) | 128 | 3 | 0 of 162 |
| 2. + second look with the same detector on a zoomed crop | 34 of 65 (52 %) | 61 of 90 (68 %) | 105 of 148 (71 %) | 16 of 25 (64 %) | 134 | 5 | 3 of 162 |
| 3. + second opinion from CLIP instead **(recommended)** | 33 of 65 (51 %) | 61 of 90 (68 %) | 107 of 148 (72 %) | 14 of 25 (56 %) | 137 | 3 | 0 of 162 |
| 4. + both must agree | 34 of 65 (52 %) | 62 of 90 (69 %) | 110 of 148 (74 %) | 16 of 25 (64 %) | 141 | 4 | 2 of 162 |
| second look that asks in more words | 35 of 65 (54 %) | 61 of 90 (68 %) | 104 of 148 (70 %) | 15 of 25 (60 %) | 134 | 5 | 4 of 162 |

1. **A limit per item** is most of the gain and costs nothing: keys 0.425, glasses 0.225, wallet
   0.275, phone 0.4, remote control 0.5. The single limit of 0.5 was set by the remote, which is
   the item that gets confused with other things; glasses and phones are almost never wrong.
2. **A second look with the same detector**: a box from 0.25 up to its item's limit is cropped
   (4 box sides wide), blown up to the detector's input and asked again. It finds more keys but
   lets false answers in (3 in 162 photos): the detector agrees with itself too easily.
3. **A second opinion from another model**: CLIP (`openai/clip-vit-large-patch14`, MIT) looks at
   two crops around the weak box and says how much of its belief goes to the item rather than to
   37 look-alikes and bits of scenery ("a tube", "a whiteboard eraser", "a computer keyboard"
   ...). The box is accepted when that share is at least keys 0.7, glasses 0.1, wallet 0.1, phone
   0.35, remote control 0.5. This is the recommended rule: more finds than rule 2 and no false
   answer on the held-out item-free photos.
4. **Both** finds a little more and lets 2 false answers in.

With no error at all allowed on the tuning half the same steps give 60 % (limits alone) and 65 %
(with second looks) of objects over 64 px, 2 wrong answers and 0 or 1 false.

Per item, held-out half, recommended rule:

| item | found, under 32 px | found, 32 to 64 px | found, over 64 px | right answers | wrong: item not there | wrong: on something else | false on item-free photos |
|---|---|---|---|---|---|---|---|
| keys | 2 of 14 (14 %) | 9 of 17 (53 %) | 14 of 25 (56 %) | 12 | 0 of 33 | 1 | 0 of 162 |
| glasses | 18 of 22 (82 %) | 22 of 25 (88 %) | 20 of 24 (83 %) | 39 | 1 of 28 | 0 | 0 of 162 |
| wallet | 0 of 0 | 1 of 1 | 11 of 19 (58 %) | 11 | 0 of 28 | 1 | 0 of 162 |
| phone | 12 of 16 (75 %) | 14 of 18 (78 %) | 28 of 33 (85 %) | 42 | 0 of 20 | 0 | 0 of 162 |
| remote control | 1 of 13 (8 %) | 15 of 29 (52 %) | 34 of 47 (72 %) | 33 | 0 of 31 | 0 | 0 of 162 |
| **all** | 33 of 65 (51 %) | 61 of 90 (68 %) | 107 of 148 (72 %) | 137 | 1 of 140 | 2 | 0 of 162 |

**More ways to say it did not help.** Asking for keys as "keys, a key, a bunch of keys, a key
ring, car keys, a house key, a keychain" (and likewise for the others) over whole pictures needs
higher limits to stay clean and ends no better: 91 of 148 over 64 px and 5 of 25 keys with limits
per item, against 95 and 9 for the shipped phrases.

**Not run: a bigger detector.** `google/owlv2-large-patch14-ensemble` and Grounding DINO are
downloaded (`items/hf`) and `eval/items_second_look.py detect --model ...` saves their boxes in
the same form, but the GPU was shared with the live worker and three other jobs and there was no
slot for the 15 to 20 minutes it needs. It is the next thing to try for keys.

## Is the test fair for our case?

Partly, and not in the direction hoped. In the demo an item lies on a table 1 to 2 m from a 1280
pixel camera.

- **Lying, not held.** The COCO labels box every person, so the labelled objects can be split
  into those inside a person's box (held, worn, in a pocket) and the rest. Objects over 64 px
  that nobody holds, both halves: 181 objects in 84 photos (keys 45 in 18 photos, glasses 5 in 5,
  wallet 23 in 18, phone 42 in 24, remote 66 in 35). Found: 79 of 181 (44 %) today, 129 of 181
  (71 %) with the recommended rule (keys 28 of 45, wallet 15 of 23, phone 32 of 42, remote 53 of
  66, glasses 1 of 5). In photos with nobody in them at all: 59 photos, 145 objects, 59 found
  today (41 %), 98 (68 %) with the rule. So lying items are **not** easier than held ones on
  these photos: many are flat-lays of everything in a bag, with dozens of small things side by
  side. These counts mix the tuning half in, because the held-out half alone is too small.
- **"Uncluttered" could not be isolated** from the labels, and I did not go through the 84
  photos by eye.
- **Size is the real gap.** Keys are about 8 cm. At 1 to 2 m from a 1280 px camera with a 70
  degree view they are 27 to 55 px long in the detector's whole-picture pass. "Over 64 px" is
  keys closer than 0.9 m. For the demo distance the columns to read are the two smaller ones:
  keys 2 of 14 under 32 px and 9 of 17 at 32 to 64 px with the recommended rule. A phone at 2 m
  is about 50 px, a remote about 60.
- The detector was partly trained on LVIS training photos (other photos, same label names),
  which flatters all of these numbers.

## Show it once (enrolment)

**What is built.** A folder `<project root>/enrolled/<item name>/NN.jpg` (or `RECON_ITEMS_ENROLLED`)
is read at the next fix whenever a file in it changes. Each picture becomes one more question to
the detector, next to the words: the detector finds the item's box in the picture (with the
words; if they find nothing, the box that fills the picture) and remembers what that box looks
like. In every fix each proposed box is compared with it (cosine similarity of the detector's own
description of the two boxes: OWLv2's image-guided detection, but scored by similarity, because
on the words' scale every box scores 1). A box at `RECON_ITEMS_ENROLLED_SCORE` or more is
accepted and carries `"matched": "enrolled"`; every other result carries `"matched": "text"`. An
enrolled name that is not in `RECON_ITEMS` is looked for as well. It costs one pass per enrolled
picture when the folder changes and no extra pass per fix. No folder: nothing changes.

**The test** (`eval/items_enrol.py`) is synthetic and kinder than real life: 40 "own things" (8 per
kind) are labelled items cut out along their LVIS outline. Each is enrolled from a crop of the
photo it comes from and pasted, 40 to 120 px long, turned any way, brighter or darker, a little
blurred, into 4 indoor item-free photos, each time next to two other things of its kind
(somebody else's keys). The same photos with only the two others are the "not there" pictures.
It is the very pixels it was enrolled from, never another side of the thing. Saved per picture:
the boxes for the words and for the enrolled picture (alone, and turned three ways), and for every
text candidate how alike CLIP and DINOv2 find it to the enrolled crop (instance matching).

**What was measured: only a small pre-run.** The full run (320 pictures, 160 item-free photos)
needs about 10 minutes of GPU and was still queued (job `jun-items-enrol`) when this was written:
the user's two GPU slots were held by the live worker and other agents' jobs. `python
eval/items_enrol.py score --out out/enrol` prints the tables once it has run. The pre-run covers
16 pictures of 2 key things and 16 kitchen photos searched for all 40 things:

- Item-free kitchen photos: how alike a box must be before the enrolled questions stay quiet.
  Of 320 thing-and-photo pairs (held-out half of the things): 74 false answers at similarity
  0.6, 1 at 0.7, 0 at 0.8. Most boxes look somewhat like anything, so the limit has to be high.
  The default is 0.75 until the full run says otherwise.
- The two key things: at 0.7 the own keys were found in 0 of 4 held-out pictures (1 of 4 on the
  tuning half at 0.625); the words at limit 0.3 found 2 of 4. Their similarity to the enrolled
  picture was 0.52 to 0.66, other people's keys 0.41 to 0.61, the best background box 0.40 to
  0.70. On this little evidence the detector's own description of a box does **not** tell small
  keys apart from other keys or from background, and showing the keys once did not help.
  Phones, wallets and remotes, which are larger and more distinct, were not in the pre-run.
- Instance matching with CLIP or DINOv2 on text candidates: saved by the full run only.

So this route is wired in and switchable, but its value is **not shown**. A real check needs a
real clip: `eval/items_enrol_real.py --clip keys.mp4 --item keys --out <folder>` enrols from the
first sighting (or `--enrol pictures`, or `--box`) and counts answers in the later frames three
ways (words today, words with the settings in force, the enrolled picture), with a sheet of
every answer for a person to check and `--absent` for a clip without the thing. It was tried on
four test pictures, not on a real clip. No multi-view object set (CO3D or similar) was fetched:
there was no time to look for a slice small enough to fetch in minutes.

## Adding up looks (`items_candidates`)

With `RECON_ITEMS_CANDIDATES=0.25` a fix also lists every box from 0.25 up that could be placed,
accepted or not: name, score, `confidence_pct`, position, box, `matched`. From the saved boxes,
what adding up such weak sightings over several looks could buy, and what it cannot fix:

How strongly a present item scores (best box on each labelled object, both halves):

| item | size | objects | 0.5 and up (accepted today) | 0.25 to 0.5 (a candidate) | below 0.25 (not seen) |
|---|---|---|---|---|---|
| keys | under 32 px | 25 | 1 (4 %) | 9 (36 %) | 15 (60 %) |
| keys | 32 to 64 px | 29 | 5 (17 %) | 15 (52 %) | 9 (31 %) |
| keys | over 64 px | 50 | 13 (26 %) | 27 (54 %) | 10 (20 %) |
| glasses | under 32 px | 39 | 1 (3 %) | 19 (49 %) | 19 (49 %) |
| glasses | 32 to 64 px | 49 | 5 (10 %) | 33 (67 %) | 11 (22 %) |
| glasses | over 64 px | 55 | 21 (38 %) | 29 (53 %) | 5 (9 %) |
| wallet | 32 to 64 px | 3 | 0 | 1 | 2 |
| wallet | over 64 px | 26 | 10 (38 %) | 8 (31 %) | 8 (31 %) |
| phone | under 32 px | 27 | 3 (11 %) | 17 (63 %) | 7 (26 %) |
| phone | 32 to 64 px | 30 | 8 (27 %) | 18 (60 %) | 4 (13 %) |
| phone | over 64 px | 78 | 30 (38 %) | 42 (54 %) | 6 (8 %) |
| remote control | under 32 px | 26 | 5 (19 %) | 15 (58 %) | 6 (23 %) |
| remote control | 32 to 64 px | 60 | 33 (55 %) | 19 (32 %) | 8 (13 %) |
| remote control | over 64 px | 85 | 59 (69 %) | 18 (21 %) | 8 (9 %) |

So about half of the present items are candidates that a vote could confirm: that is the room
there is. But a look-alike is as steady as the real thing. In the item-free rooms, photos (of 30
per room, 24 for the kitchen) with a false candidate from 0.25 up:

| photos of | photos | 'keys' | 'glasses' | 'wallet' | 'phone' | 'remote control' | any |
|---|---|---|---|---|---|---|---|
| Cantina | 30 | 1 | 0 | 0 | 0 | 0 | 1 |
| Lounge | 30 | 0 | 1 | 0 | 0 | 11 | 11 |
| Lunch room | 30 | 0 | 0 | 0 | 0 | 11 | 11 |
| Meeting room | 30 | 4 | 0 | 3 | 4 | 9 | 15 |
| Printing area | 30 | 3 | 0 | 0 | 1 | 1 | 5 |
| kitchen baseline | 24 | 0 | 0 | 0 | 0 | 1 | 1 |
| five outdoor scenes | 150 | 0 | 0 | 0 | 0 | 1 | 1 |

In three of the five indoor rooms a false "remote control" comes back in about a third of the
views. On the contact sheet of the detections from 0.3 up these are a few things seen again and
again (a white tube on the lunch-room table, an eraser on the lounge floor, the edge of the
meeting-room TV). I did not tie the boxes together in 3D, so "same spot" is by eye, not by
measurement. **Voting over looks will
confirm these, not remove them.** What separates them from real items is the second opinion
(above) or a picture of the wearer's own thing, not more looks. A vote should therefore count
only candidates that the second opinion did not reject, or weigh by `confidence_pct`.

## Confidence that means what it says (`confidence_pct`)

Every entry of `items` and `items_candidates` carries `confidence_pct`: of the answers with the
same kind of evidence on the tuning half, the share that were right. The evidence is the item,
the band its score is in (0.25, 0.35, 0.5, 0.6 and up) and, for a weak box the judge was asked
about, whether the judge agreed. An item with few answers in a band is pulled towards the share
of all items in that band (as if 4 more answers at that rate had been seen). `score` is unchanged.

Stated against measured, held-out half (answers = best box per item and photo from 0.25 up, on
test photos where the truth is known and on item-free photos):

| stated confidence | answers | right | measured |
|---|---|---|---|
| 0 to 20 % | 13 | 3 | 23 % |
| 20 to 40 % | 18 | 3 | 17 % |
| 40 to 60 % | 7 | 6 | 86 % |
| 60 to 80 % | 4 | 4 | 100 % |
| 80 to 100 % | 127 | 125 | 98 % |

The top and the bottom hold. The middle is thin (11 answers) and came out better than stated.
Where the table behind it is thin: keys have 5 to 10 answers per band on the tuning half, wallets
0 or 1 (their numbers are mostly the all-items rate), and nothing above 0.6 for keys. The full
table is printed by `python eval/items_recall.py ... --confidence`. Two cautions: the share is
measured on test photos where the item is present in about half of the photos that count, so at
home, where it mostly is not in view, a weak box will be right less often than stated; and a box
matched to an enrolled picture states a flat 50 %, because nothing real has measured it yet.

## Settings to switch it on

Nothing changes until these are set (all are read at worker start):

```
RECON_ITEMS_LIMITS="keys=0.425,glasses=0.225,wallet=0.275,phone=0.4"     # a limit per item (remote control keeps 0.5)
RECON_ITEMS_LOOK_FROM=0.25                                               # weak boxes from here get a second opinion
RECON_ITEMS_JUDGE=openai/clip-vit-large-patch14                          # who gives it (weights: items/hf, 1.6 GB)
RECON_ITEMS_JUDGE_LIMITS="keys=0.7,glasses=0.1,wallet=0.1,phone=0.35,remote control=0.5"
RECON_ITEMS_CANDIDATES=0.25                                              # list weak boxes too, as items_candidates
```

Others: `RECON_ITEMS_LOOK_LIMITS` (the zoomed second look, rule 2: `keys=0.225,glasses=0.225,
wallet=0.225,phone=0.525,remote control=0.6`), `RECON_ITEMS_LOOK_MAX` (6 second looks per
picture), `RECON_ITEMS_ENROLLED` (folder, default `<project root>/enrolled`),
`RECON_ITEMS_ENROLLED_SCORE`. No new package: CLIP is in the `transformers` already in
`items/pkgs`. Weights are in `items/hf` (`iridis/setup_items_more.sh` fetches them elsewhere).

Seconds added per fix:

| | L4 GPU | 32 CPU cores |
|---|---|---|
| a limit per item | nothing | nothing |
| one second opinion (CLIP, two crops of one weak box) | 0.041 s | not measured |
| one zoomed second look (rules 2 and 4) | 0.246 s | about 1.4 s (one detector pass, from round one) |
| loading CLIP at worker start | not measured | 23 s on a throttled login node |

Measured over 1031 candidates in the second-look job. How many second opinions a picture needs
under the recommended limits: 0.55 on average on the test photos (none in 69 % of them, 2 at the
90th percentile, at most 6) and 0.19 on item-free photos (none in 86 %). So the recommended rule
adds about 0.02 s to an average fix on the GPU and at most 0.25 s. A whole fix through
`worker.where()` with these settings was queued on both the GPU and the CPU node and had not run
when this was written (jobs `jun-items-full`, `jun-items-full-cpu`).

## Not tested in this round

- **A whole fix with the new settings.** The rules were scored from saved boxes and saved second
  opinions; `items.py` applies the same rule and the offline checks cover its logic with
  stand-ins, but the judge has not yet run inside `worker.where()` on a worker node. On a login
  node it loaded, and the enrolled folder was picked up.
- **Show it once, in full**, and anything real for it (see above).
- **A bigger detector** and Grounding DINO.
- **Whether the repeated false candidates are at the same spot in 3D.**
- **The judge on the CPU worker** (seconds).
- A real room, real items, the glasses. Still nothing.
- The selection rule is looser than round one's (one error per item allowed on the tuning half).
  And the held-out item-free photos are other views of the same eleven places the limits were
  chosen on, not new rooms: "0 of 162" is weaker evidence than it looks.
