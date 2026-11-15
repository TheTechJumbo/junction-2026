# The object map: what is in the room, and what was done with it

Sat 14 Nov 2026. Branch `jun-26-object-map`. Code: `objects.py`, `eval/objects_dump.py` (runs the
two models once per scene), `eval/objects_eval.py` (everything after that, numpy only),
`eval/objects_watch.py` (optional), `eval/test_objects.py` (offline checks, made-up detections).

## In short

- `objects.py` keeps a list of the things in a room on the room map: labelled once from the
  baseline photos (inventory), then only updated by each position fix. It answers "where is my
  cup?" from the map and writes a diary of `object_moved`, `object_gone` and `object_appeared`.
- **The inventory is short and mostly right, not complete.** On the four scenes it was not tuned
  on it kept 14 to 45 objects per room (102 in all); by my own count from the contact sheets 84 of
  the 102 (82 %) are a real thing with the right name, 4 are a second entry for the same thing and
  14 have a wrong name.
- **The diary says little, and what it said about the changed rooms was right.** In four changed
  rooms it reported 2 events, both on a hand-labelled change and both of the right kind by eye. It
  held 14 more back as "unconfirmed", 11 of them on a labelled change. With the watched-spot check
  of `watch.py` switched in it reported 4, all right. Against the labelled regions
  it hit 0 of 151 small, 6 of 103 medium and 8 of 81 large ones, where the whole-room detector hits
  2 %, 20 % and 62 % indoors. So it does not replace the whole-room detector; it adds names.
- **It does false-alarm.** In the unchanged control (12 held-out photos per room, 48 fixes) it
  reported 3 false events (one hanging lamp twice, one stool) and held 12 more back; with the
  watched-spot check, 2.
- **`confidence_pct` means roughly what it says for objects** (by my count: 42 % right among those
  marked 40 to 59, 77 % among 60 to 74, 93 % among 75 to 89, 83 % among 90 and up). For events it
  does not hold up: 3 of the 5 events reported in all were the false ones, at 70 to 87 %.
- **Agreement across photos did not rescue weak boxes.** That was the idea, and on the tuning
  scene it failed: the first version called 97 things "95 % sure", among them a laptop, a keyboard,
  headphones and a tablet that are all one picture shown on a television, found in up to 24 of 24
  photos. A weak name that repeats is the same mistake repeated. What the photos add is the
  place (one object instead of 20 boxes), not the name. Confidence is therefore capped by the
  object's three best detector scores, which brings the usable limit back to about the 0.5 that
  `items.py` already uses.
- "Where is X?": the stored place, looked up in photos that were not used for the inventory, was
  on the object in 60 of 66 cases by my own count; the detector put a box of that name there in 211
  of 224 clear looks, a median 5 to 15 cm from the stored place.
- An update costs no model time beyond one detector pass (0.8 s on the L4, 5 to 7 s on 32 CPU
  cores); the map logic itself takes milliseconds. An inventory takes about 25 s on the L4 and
  about 4 minutes on 32 CPU cores.

## What was built

| Part | Call | Where it runs |
| --- | --- | --- |
| Vocabulary | `objects.VOCABULARY`: 81 labels, 115 phrases | |
| Find | `objects.find(detector, picture)` | worker (detector) |
| Inventory once | `objects.inventory(found_per_photo, r, base=...)` -> map | worker (needs the reconstruction) |
| Look, per fix | `objects.observe(found, map, r, base, q, valid, window, state, picture.shape)` -> `objects_seen`, `objects_checked` | worker |
| Update, per fix | `objects.update(map, objects_seen, fix)` -> `(map, newly reported events)` | relay (plain Python, no numpy) |
| Diary | `objects.diary(map)`, `objects.describe(event, places, clock)`, `objects.events(before, after)` | relay |
| Ask | `objects.where_is(map, question, fix, places, clock)` | relay |
| 3D view | `objects.marks(map, room)` for `splat.write_splat(..., marks=...)`, `objects.labels(map, room)` | worker |

The map is plain JSON. Each object: `id`, `label`, `names` (every name the detector gave it, with
summed scores), `position` (east, north, up in rough metres), `size_m`, `views` (photos or
standpoints that saw it), `could_see` (photos that had a clear look at its place), `evidence`
(summed scores), `best_score`, `crop`/`best_view`/`best_box`, `first_seen`, `last_seen`, `state`
(`present`, `moved`, `missing`, `unconfirmed`) and `confidence_pct`.

**The vocabulary** was chosen before any test photo was looked at: the five things the product
already looks for; what a carer asks about (medicine, glasses, keys, wallet, phone, walking stick,
handbag); and movable indoor things from the COCO and frequent LVIS category names, which the
detector's training data uses, room by room. Left out on purpose: the building (walls, doors,
shelves), people, pets, outdoor things. Two words for one thing are one label ("cup": cup, mug).
I had read `eval/RESULTS.md` before, which names some things in these scenes (keyboard, pens,
boxes), so the choice was not blind to the test data.

**Fusing.** Every box of at least 0.2 is put on the map with the baseline reconstruction's depth
(`items.place`, unchanged). Boxes of one name within reach of each other (0.3 m, or 0.6 of the
thing's size) from different photos are one object; two boxes of one name in one photo are two
things, unless one centre lies well inside the other (a part and the whole). Different names at
the same place with the same size are one object with two names.

**Updating.** Detections of the newest frame refresh the object they land on. An object that
had a clear look (in view, not blocked, at least 48 pixels long in the detector's picture: the
facts of `items.check`, with the object's own measured size) and was not found loses belief. The
same name confirmed elsewhere while its old place is empty is one `object_moved`. Looks from the
same standpoint (within 0.3 m and 15 degrees) count once.

### How `confidence_pct` is computed

Every object carries a sum of evidence (log-odds, "belief"); `confidence_pct = 100 / (1 + exp(-belief))`.

| What happens | Effect on the belief |
| --- | --- |
| Start | -1.5 (18 %): one box alone is probably nothing |
| A sighting from another photo or standpoint | + 1.2 x score / 0.5 (between 0.36 and 1.8). In updates, the second in a row counts double |
| Ceiling | never above what the mean of its three best scores allows: 0.35 -> 45 %, 0.45 -> 65 %, 0.55 -> 85 %, 0.62 and up -> 95 % |
| A clear look that does not find it, from another standpoint | - 0.75, the second in a row - 1.5, from the third on - 2.25. Half of that if the depth still shows a surface at its place, 1.3 times if the camera now sees farther. With the watched-spot check: a quarter if the place looks as in the baseline, 1.3 times if it looks different |
| In the inventory, a photo with a clear look and no box | - 0.1 |
| Time without a look | halves towards 50 % every 24 hours |
| Bounds | 5 % to 95 % |

An object counts (`present`) from 60 %; it becomes `missing` at 25 % or less after at least two
clear looks in a row without it. One look never flips a state: from 85 % one miss gives about
70 %, a second about 35 %, a third `missing`. For a `missing` object the number is how sure the
map is that it is not at its place.

Events have their own number: `gone` = (how sure the object was before it was missed) x (how sure
its place is empty now); `appeared` = (how sure the new object is) x (0.95, 0.75 or 0.4 depending
on whether the baseline frames of the fix saw empty space, nothing, or a surface at that place);
`moved` = the smaller of the two ends. **An event is reported from 70 % on.** Below that it stays
in `map["events"]` with `status: "unconfirmed"`, keeps following the evidence, and is reported
later, withdrawn, or replaced by a move. `update` returns only newly reported events.

## The test

PASLCD, `Instance_1`, the five indoor scenes, the same split as `eval/RESULTS.md`: 24 photos of
the room before the change are the baseline, 12 more photos of that same capture are held out as
the unchanged control, and 12 photos after the change have hand-drawn change masks. Photos are
taken with a very wide lens (about 100 degrees), so things across the room are small: a mouse on a
desk 4 m away is 6 pixels wide in the detector's picture.

- Detector: the deployed one (`google/owlv2-base-patch16-ensemble`, tiles 1), on the photo scaled
  to 1280 pixels, as the relay sends `big.jpg`.
- Inventory: VGGT on the 24 baseline photos, map frame from the 12 of them a position fix uses.
- Update: one position fix per photo, as `live/worker.py where()` does it: those 12 baseline
  photos plus the one new photo through VGGT. 24 fixes per scene, each from another standpoint.
  All 120 fixes came back `found`.
- **Tuning.** All limits were set on Lounge only and frozen in commit `ec3f3ed` before any other
  scene was evaluated. The other scenes are the report. Not tuned at all, set by reasoning: the
  baseline-frame factors for `appeared` and the watched-spot weights.
- Counting by eye: I looked at every contact sheet named below myself (crop, label, views) and
  counted. It is one reader's count, and some crops are hard to judge; those are marked "unsure"
  and counted as right.

## 1. Does one physical thing become one map object?

Objects are what the map shows: confidence 60 % or more. The verdicts are my own count from the
contact sheets `objects/out/<scene>/inventory_*.jpg` on the cluster (one tile per object: the best box,
label, confidence, photos that saw it). "Wrong name" includes near misses a user would not find
by asking (a whiteboard eraser as "stapler", a book as "notebook", a filament spool as "fan", a
tape dispenser as "tissue box") and a picture of a thing (a cup on a poster).

| Scene | Boxes of 0.2 or more in 24 photos | Objects shown | Right thing, right name | Second entry for the same thing | Wrong name | Kept back as unconfirmed |
| --- | --- | --- | --- | --- | --- | --- |
| Cantina | 829 | 14 | 11 | 0 | 3 | 107 |
| Meeting_room | 842 | 20 | 17 | 1 | 2 | 82 |
| Printing_area | 1005 | 23 | 17 | 1 | 5 | 98 |
| Lunch_room | 1363 | 45 | 39 | 2 (a guess, see below) | 4 | 159 |
| **Four report scenes** | 4039 | **102** | **84 (82 %)** | **4 (4 %)** | **14 (14 %)** | 446 |
| Lounge (tuned on this one) | 1185 | 18 | 14 | 2 | 2 | 110 |

So about a thousand boxes per room become 14 to 45 objects, of which about four in five are
right, and second entries are rare. What that costs: the rooms hold far more things than that.
The inventory is the things the detector is sure of, not a list of everything. I did not count
what is missing, because there is no list of what is in these rooms.

Lunch_room is a large room with some thirty chairs and stools that look alike; 22 of its 45
objects are stools. There I could check the name but not whether two entries are the same stool,
so its two "second entries" are the stools that lie within 0.3 m of another one.

### Does the confidence mean what it says?

Same sheets, same count, the report scenes pooled. "Right" = a real thing with the right name
that is not a second entry.

| `confidence_pct` | Objects | Right | Share right |
| --- | --- | --- | --- |
| 40 to 59 (not shown: unconfirmed; three scenes, not counted in Lunch_room) | 53 | 22 | 42 % |
| 60 to 74 | 56 | 43 | 77 % |
| 75 to 89 | 28 | 26 | 93 % |
| 90 to 95 | 18 | 15 | 83 % |

It does, roughly, for objects: unconfirmed ones are right less than half the time, shown ones
about four times in five, and the shares sit near the numbers. The top row does not beat the one
below it (3 of its 18 are wrong names the detector is very sure of), so above 75 % the number
separates nothing. The curve was drawn on the Lounge sheet; these scenes did not take part. The
counts are small and from one reader.

## 2. Does it notice what was done?

Each scene's map was updated with the 12 photos taken after the change, one position fix each.
"On a labelled change": the event's place lands on a change mask in at least half of the after
photos that have it in view. "Right by eye": my reading of the before/after crops in
`objects/out/<scene>/events_0.jpg`. This table is `objects.py` as it runs from this branch alone
(without the watched-spot check, see 2b).

| Scene | Events reported (70 % or more) | of them on a labelled change | Right kind by eye | Events held back as unconfirmed | of them on a labelled change |
| --- | --- | --- | --- | --- | --- |
| Cantina | 0 | | | 2 | 2 |
| Meeting_room | 2 | 2 | 2 (a blue stool appeared; a dark screen by the wall is gone, its name "television" is doubtful) | 4 | 4 |
| Printing_area | 0 | | | 4 | 3 |
| Lunch_room | 0 | | | 4 | 2 |
| **Four report scenes** | **2** | **2** | **2** | **14** | **11** |
| Lounge (tuned on this one) | 1 | 1 | 1 ("the stool was moved": the yellow stool, both ends right) | 3 | 0 |

The held-back events are mostly true: a water bottle that appeared (25 %), a book that is gone
(63 %, named "notebook"), a box that is gone (61 %, as a move with a wrong destination), markers
and an eraser that were put on the table (30 % and 31 %), a microwave that was moved (38 %, as
"appeared"). Most are low because the object behind them was not sure enough, or because the
baseline frames show a surface at that place, which for a small thing on a table is the table.

### The control: 12 photos of the unchanged room

Every event here is a false alarm.

| Scene | Events reported | Held back as unconfirmed |
| --- | --- | --- |
| Cantina | 0 | 2 |
| Meeting_room | 2 (one hanging lamp: "gone" and "appeared") | 1 |
| Printing_area | 0 | 1 |
| Lunch_room | 1 (a stool "appeared", 83 %) | 8 |
| **Four report scenes, 48 fixes** | **3** | **12** |
| Lounge (tuned on this one) | 0 | 4 |

Reported events over the four report scenes, changed and unchanged rooms together: 5, of which
2 are right and 3 are false. That is too few to call the event confidence calibrated, and it
does not look calibrated: the false ones stood at 87 %, 83 % and (when reported) 70 % or more.
The lamp is a 1 m thing that the inventory had as two objects 0.6 m apart; the stool is one of
thirty look-alikes that the inventory had missed.

### 2b. With the watched-spot check (`watch.py`, branch `jun-22-watch-spots`)

For every inventory object the watched-spot check was asked, per photo, whether the object's
place looks as in the baseline (`eval/objects_watch.py`; 12 baseline + 3 new photos per pass,
each new photo asked on its own). A miss by the detector then counts a quarter when the place
looks the same and 1.3 times when it looks different, and a "changed" place counts as a look
without the object even when the object is too small for the detector. These weights were set by
reasoning, not tuned. Over the four scenes it answered "same" 671 times, "changed" 192 times and
"not seen" 1324 times.

| Scene | Reported, changed room | on a labelled change | Right kind by eye | Held back | on a labelled change | Reported in the unchanged control |
| --- | --- | --- | --- | --- | --- | --- |
| Cantina | 1 ("the paper roll is gone", 84 %) | 1 | 1 | 3 | 3 | 0 |
| Meeting_room | 3 (the two above and "the pen was moved", 75 %) | 3 | 3 | 3 | 3 | 2 (the same lamp) |
| Printing_area | 0 | | | 7 | 6 | 0 |
| Lunch_room | 0 | | | 4 | 2 | 0 (the stool is held back) |
| **Four report scenes** | **4** | **4** | **4** | **17** | **14** | **2** |

Two more right events and one false event fewer. The stool that "appeared" in the Lunch_room
control is now held back because other stools had just been missed at their places, which makes
the map wait; that is a side effect, not a cure. On Lounge nothing that was reported changed. It
is still a small sample.

### Next to the whole-room change detector

A labelled region is one blob of a mask in one photo (at least 50 pixels at 518 x 294), sized as
in `eval/RESULTS.md`. Here a region is hit when a reported event's place lands in it or within
the object's own radius of it; the whole-room detector's rule is 10 % of the region's pixels
flagged. The rules differ, so read this as a rough comparison. One object shows as a region in
every photo that sees it.

| | Small regions (50 to 199 px) | Medium (200 to 799) | Large (800 and more) | False events or clusters in the unchanged control |
| --- | --- | --- | --- | --- |
| Object diary, events reported at 70 %, four scenes | 0/151 | 6/103 (6 %) | 8/81 (10 %) | 3 in 4 rooms |
| The same with the watched-spot check, four scenes | 12/151 (8 %) | 11/103 (11 %) | 13/81 (16 %) | 2 in 4 rooms |
| Object diary, every event told, held-back ones too, four scenes | 36/151 (24 %) | 45/103 (44 %) | 37/81 (46 %) | 17 in 4 rooms |
| Whole-room detector, all five indoor scenes (`eval/RESULTS.md`) | 4/168 (2 %) | 27/136 (20 %) | 58/94 (62 %) | 0 in 5 rooms |

On these four scenes the whole-room detector hit 58 of 335 regions (Cantina 21/118, Meeting_room
11/69, Printing_area 22/90, Lunch_room 4/58); the diary at 70 % hit 14. So: as shipped, the
diary notices less than the whole-room detector and false-alarms more. What it adds is names and
small things: when every event is told, 13 of the 16 events of the changed rooms are on a
labelled change and a quarter of the small regions are hit, which the whole-room detector hardly
sees. But telling everything also tells 17 false events in 48 unchanged fixes.

## 3. Where is X?

For every shown object, every held-out photo with a clear look at its place (in view, not
blocked, big enough) was used: the stored map position was projected into that photo through
that photo's own position fix.

| Scene | Clear looks | Detector put a box of that name on the projected place | Stored place against that new sighting's place, median | 90 % within | Objects checked by eye | On the object by eye |
| --- | --- | --- | --- | --- | --- | --- |
| Cantina | 44 | 43 | 0.05 m | 0.11 m | 12 | 11 |
| Meeting_room | 67 | 60 | 0.07 m | 0.40 m | 13 | 11 |
| Printing_area | 52 | 50 | 0.05 m | 0.15 m | 16 | 14 |
| Lunch_room | 61 | 58 | 0.15 m | 0.41 m | 25 | 24 |
| **Four report scenes** | **224** | **211 (94 %)** | | | **66** | **60 (91 %)** |
| Lounge (tuned on this one) | 82 | 78 | 0.08 m | 0.32 m | 12 | not counted |

By eye (`objects/out/<scene>/where_0.jpg`, one tile per object: a held-out photo with a ring at
the projected place): the ring is on the object in 60 of 66. The six misses are a chair whose
stored point is the table edge in front of it (twice), a dish rack, two boxes behind a cupboard
door, and a broom. In Lunch_room "on the object" means on a stool, not provably on that stool.
Metres are the map's rough metres. This checks that the place is stable from fix to fix; it does
not check that the guidance ("2 metres to your left") leads a person there.

## 4. Seconds

Slurm jobs 2220689 (L4) and 2220688 (32 cores of an AMD node, five scenes). The detector was
asked all 115 phrases in one pass; on the L4 that takes 0.81 s per photo against 0.77 s for the
five deployed names, so the size of the vocabulary is nearly free.

| | L4 GPU | 32 CPU cores |
| --- | --- | --- |
| Inventory: VGGT on 24 photos | 3.2 s | 66 to 97 s |
| Inventory: detector on 24 photos | 18.9 s | 130 to 176 s |
| Inventory: placing, fusing, counting clear looks (numpy) | 3 to 4 s (measured on a login node, 4 threads) | 3 to 4 s |
| **Inventory of a room** | **about 25 s** | **about 3.5 to 4.5 minutes** |
| Update: the position fix itself (VGGT on 12 + 1 photos, locate) | 1.75 s | 30 to 44 s |
| Update: detector on the newest picture | 0.81 s | 5.3 to 7.3 s |
| Update: placing detections and looking at each known place (numpy) | 0.03 s | 0.03 s |
| Update: `objects.update` (plain Python, in the relay) | 0.005 s | 0.005 s |
| **One update, all in** | **about 2.6 s** | **about 35 to 52 s** |

If the object look shares the detector pass with `items.look`, an update adds only the last two
rows to a fix. Today `items.look` runs its own pass, so wiring it in as it is costs one more pass.
The watched-spot check was not timed here.

## 5. What the tuning scene showed, and the variants

- **First version, Lounge:** 97 objects at 60 % or more, nearly all "95 %". From a rough look at
  two of its three sheets, not a careful count: well under half were right, the rest second
  entries (one sofa as five, light strips as a dozen lamps) or wrong names. 3 false events in the control. Weak boxes (0.25
  to 0.4) that repeat in many photos had filled the belief. Hence the ceiling by the best scores
  and the part-and-whole merge; after that, 18 objects.
- **Report limit.** On Lounge at 50 % instead of 70 %: 2 false events in the control instead of 0,
  and 4 more events on the changed room that are not on a labelled change. So 70 %.
- **Depth at the place** (half weight for a miss when a surface is still there; switched off on
  Lounge, Cantina and Meeting_room to see): no reported event changed. Harmless, unproven.
- **Baseline frames for "appeared"** (not tuned; switched off on Cantina and Meeting_room to
  see): on Cantina they removed one false "microwave appeared" from the control and the true one
  from the changed room; on Meeting_room nothing reported changed. A wash on this data. Lounge's
  saved fixes lack the baseline frames' depth (an early version of the dump), so it ran without.
- **Finer look for the inventory** (2 rows of tiles, 2.6 times the detector time; Lounge, Cantina,
  Meeting_room): 25 to 35 objects instead of 14 to 20, no more events on labelled changes, and
  false events in the control of 1, 0 and 1 against 0, 0 and 2. Not used.

## What could not be measured

- **Time.** The test data has no clock: all "fixes" of a scene are seconds apart, so the fall of
  confidence with time (half-life 24 hours) is a design choice, checked only by the offline test.
- **Repeated looks from one standpoint**, which is what a wearer standing still produces: the test
  photos are all from different standpoints. The rule (count once) is checked only offline.
- **Kinds of change.** The masks do not say whether a region is an added, removed or moved thing,
  and do not link regions across photos. "Right kind" is my reading of the before/after crops.
- **The five product items** (keys, glasses, wallet, phone, remote control) do not occur in these
  rooms, apart from one wrong "remote control". Nothing here says how well keys are tracked.
- **A real home, a real wearer, video frames.** These are sharp 12 MP photos scaled down. Motion
  blur and the glasses' camera will lower the detector scores, and the confidence ceiling then
  hides more.
- **The live path.** `objects.py` is not wired into `live/worker.py` or `live/relay.py`, so no
  fix has gone through the queue with it. The worker-side helpers ran on saved reconstructions.
- **The map staying put when the baseline set changes.** A fix adds the photos of named places to
  its 12 baseline photos; the map's scale then shifts a little. Here every fix used the same 12.
- **Whether an "80 %" event is right 80 % of the time.** Five events were reported in all; three
  were false. The event numbers are not calibrated, and this data cannot calibrate them.
- **What the inventory misses.** There is no list of what is in these rooms.
- **The time the watched-spot check adds to a fix.**

## What this means for the demo

- **Show "where is my X?" from the map.** For the 15 to 45 things per room the map is sure of,
  the answer comes with a place that held to within 5 to 15 cm (median) from fix to fix and was
  on the object about 9 times in 10, and with a confidence that means roughly what it says. The room
  needs one inventory after the baseline is recorded: about 25 s on the L4.
- **Show the labels in the 3D view** (`objects.marks`): pink where a thing is, orange where a
  moved thing is now, red where a missing one was.
- **Expect the diary to be quiet, and stage the move.** In four changed rooms it reported two
  events (both right) and held fourteen back; with the watched-spot check, four. For a move to be reported, the thing must be one the
  detector knows well (a baseline score of 0.55 or more: a stool, a mug, a keyboard, a kettle, a
  bin), stand at least half a metre from where it was, and both its old place and its new place
  must be looked at clearly two or three times from different standpoints (a step or a turn of
  the head apart). Check on the day that the thing is in the inventory with 85 % or more.
- **Do not present the diary as a full record of what the person did.** It misses most changes
  (it hit 14 of 335 labelled regions; the whole-room detector 58), and in unchanged rooms it gave
  3 false events in 48 fixes. For a carer, the held-back list (`objects.diary(map,
  unconfirmed=True)`) is worth showing as "possibly": 11 of its 14 entries in the changed rooms
  were on a labelled change, but the unchanged rooms produced 12 such entries as well.
- **Large things without a clear middle, and rooms full of look-alikes, are the risk**: two of
  the three false events were one hanging lamp, the third one of thirty similar stools. Leaving `lamp`, `sofa` and `table` out of the vocabulary for the demo is
  a reasonable precaution; I did not test it.
- **Keys, glasses, wallet, phone, remote control were not in these rooms.** Keep `items.py`'s own
  last-seen memory for them until this map has been tried on a recording with them.
- It is not wired in yet. The calls are below.

## How to wire it in

Nothing in `items.py`, `locate.py`, `recon_change.py`, `watch.py` or `live/` was changed.

**Worker, once per baseline** (a new operation, or at the end of `set_baseline`):

```python
frames = [cv2.imread(str(f)) for f in files]                       # every baseline photo
r = rc.reconstruct(model, frames, long_side=LONG_SIDE)
found = [objects.find(ITEM_DETECTOR["model"], f, limit=objects.LIMIT_INVENTORY) for f in frames]
pick = sorted({int(i) for i in np.linspace(0, len(files) - 1, min(N_LOCATE_BASE, len(files))).round()})  # the photos where() uses
room_map = objects.inventory(found, r, base=pick, picture_shape=frames[0].shape)   # JSON: give it to the relay
```

**Worker, per position fix**, in `where()` after `locate.locate`, when the request carries `objects`:

```python
picture = items.big_frame(job / req["file"])            # as items.look chooses the picture; else seen[-1]
found = objects.find(ITEM_DETECTOR["model"], picture)   # one detector pass, 115 phrases
window = items.crop_window(seen[-1].shape, base[0].shape)
result.update(objects.observe(found, req["objects"], recon, list(range(len(base))), len(base) + len(new) - 1,
                              valid, window, result["state"], picture.shape))
# adds result["objects_seen"] and result["objects_checked"]; for the 3D view:
# marks += objects.marks(req["objects"], locate.map_frame(recon, list(range(len(base))), valid))
```

**Relay.** Store one map per zone as a JSON file (about 100 kB: every object, the unconfirmed
ones too, and the events). With every locate request send `objects.for_worker(room_map)` as
`"objects"`, the way `"remembered"` is sent today. With every result:

```python
room_map, told = objects.update(room_map, result["objects_seen"],
                                {"state": result["state"], "position": result["position"],
                                 "heading_deg": result["heading_deg"], "checked": result["objects_checked"]})
for event in told:   # newly reported: a diary line
    line = objects.describe(event, places=result["waypoints"], clock=lambda t: home_time(t).strftime("%H:%M"))
```

- "Where is my X?": `objects.where_is(room_map, question, fix=where.get(zone), places=fix["waypoints"], clock=...)`
  returns `answer`, `position`, `state`, `confidence_pct`, `distance_m`, `bearing_deg`; `known` is
  false for a word that is no thing of the vocabulary, `found` is false when it is not in the map.
- Carer: `objects.diary(room_map)`, `objects.diary(room_map, unconfirmed=True)`, `objects.listing(room_map)`.

**Changes in other files that would help** (not made here):

1. `items.look` could take the raw boxes of one shared `detector.detect` call (or hand them back),
   so that the five items and the object map cost one detector pass per fix, not two.
2. `worker.where()` adds the photos of named places to the 12 baseline photos of a fix. The map's
   scale comes from those photos (`locate.map_frame`), so the map shifts a little when places are
   added. Taking the map frame from the 12 evenly spread photos only would keep stored positions
   valid. Not measured.
3. With `watch.py` on the branch: `objects.observe(..., watcher=watch)` adds the watched-spot
   verdict for every inventory object (it needs `Recon.feat`, which `rc.reconstruct` returns).
   On the four report scenes that doubled the right events (2 to 4) and the false ones went from
   3 to 2 (section 2b).
