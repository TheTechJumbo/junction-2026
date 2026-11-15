# Find me X: how the orange box is recognised (15 Nov 2026)

`live/finder.py` on the relay decides from what the worker's item finder (OWLv2, `items.py`) reports for each position
fix. This file has the numbers behind its settings for "orange box". No pictures are kept in the repository.

## False positives: the venue without a planted box

`eval/find_measure.py` on the GPU worker (L4, OWLv2 base patch16 ensemble, whole picture + 1 row of tiles, 0.27 s a
picture without a fix), over 1,087 distinct pictures of the venue:

- 96 keyframes of the kitchen recording (02:59)
- 48 kitchen pictures
- 325 glasses photos sent for fixes last night (several zones)
- 618 frames (every 10th) of the 5 kitchen recordings

The validate-room keyframes are the same recording again, so they are not counted. The venue has many orange things:
a tall orange carton on our table, chip bags, cans and cartons among bottles, and orange drinks in a fridge.

Pictures whose best box reaches a score, for two phrases. "+colour" means at least 20% of the middle of the box is
orange (`items.colour_share`):

| score ≥ | "orange box" | + colour | "an orange box" | + colour |
|---|---|---|---|---|
| 0.25 | 41 (3.8%) | 29 (2.7%) | 33 (3.0%) | 27 (2.5%) |
| 0.30 | 19 (1.7%) | 15 (1.4%) | 14 (1.3%) | 14 (1.3%) |
| 0.35 | 7 (0.6%) | 7 (0.6%) | 5 (0.5%) | 5 (0.5%) |
| 0.40 | 1 (0.1%) | 1 (0.1%) | 1 (0.1%) | 1 (0.1%) |
| 0.50 | 0 | 0 | 0 | 0 |

"an orange cardboard box" fires on brown cartons: 17 pictures at 0.30, 6 with the colour check. "a box" fires on
anything boxy: 206 pictures at 0.30.

What the 24 best "orange box" boxes are (looked at by eye):

- the tall orange carton on our table: 6 photos, 0.31 to 0.39
- small orange cartons or cans 3 to 5 m away, 11 to 39 px tall: 7
- a blurred orange thing on the table: 0.40 and 0.36
- an orange rectangle held in a hand: 0.32
- orange stripes on a metal frame, blurred: 2 at 0.345
- a plastic box with an orange label (colour 0.007) and orange drinks in a fridge (colour 0.13): both fail the colour check

No box scored 0.5, the worker's general limit, so the worker never accepts "orange box" on its own. The relay reads its
candidates instead (RECON_ITEMS_CANDIDATES=0.25 on the cluster).

## Settings (finder.LIMITS, SIZES_M, COLOUR_LEAST, AGREE_*)

- Phrase: the name itself, "orange box". "an orange box" scores a little lower on the same things.
- Score at least 0.30: 1.4% of venue pictures with the colour check, most of them orange cartons.
- Colour: at least 20% of the middle of the box orange (OpenCV hue 7 to 22, saturation at least 120, value at least 110).
- Size: the box's long side 0.1 to 0.6 m, from the fix's depth (`items.box_size_m`). This removes the small far-off
  cartons and cans. It cannot be measured on these pictures (they have no fix depth); it runs only on fixes.
- Two fixes agree within 1.0 m. Two real fixes of the carton on our table placed it 0.74 m apart (one "found", one
  "unsure"), which 0.6 m would have split. A move is a new sighting more than 1.5 m from the last.

A static orange thing that passes every check (the carton) is a sighting of "orange box". That is the remaining false
positive: two orange boxes on one table cannot be told apart without showing the real one (`/enroll`).

## Through the whole path (relay → shared worker, zone find-test = kitchen's pictures)

- Photo 214704 (last night, our table): fix found in 5.7 to 8.5 s (15 s the first time, while the gateway started).
  "orange box" 0.32 on the carton, placed 0.88 m from our table.
- Photo 214708: an unsure fix, 0.31, 0.74 m from the first. Result: a sighting, "The orange box was seen on our table".
  - It went into the day log as an item entry and onto the event API as `object_appeared` (vision, zone find-test).
- /ask "find me the orange box" → "The orange box is on our table, about 0.8 metres ahead. I last saw it at 07:30.
  Say take me there and I will guide you."
- /navigate?to=orange box → 1.3 m, "turn left".

That worker ran the deployed `items.py`, without size_m and colour (the relay lets such boxes pass both checks).

## Latency (replay of last night's glasses photos at our table, about 3 a second, like the app's live camera)

Relay with --quick, on its own L4 worker running this branch (zone find-test, after one warm-up photo):

| Step | Time after the first photo showing the carton |
|---|---|
| Full fix sees it | 10.6 s (one view: "found", "I may have seen…") |
| Second full fix agrees: sighting | 17.5 s ("The orange box is on our table…"; day log and event API) |

- With this branch's items.py, the full fix's box passed every check: score 0.32, size 0.12 m from depth, colour 0.28.
- Live outline (op detect, the item finder alone on the newest photo, while /find is polled): 1.1 to 1.3 s from the
  photo to the detection when the worker is free, so a /find poll sees it 1.5 s after the photo. When it queues
  behind a full fix (5 to 6 s each, one every ~10 s with --quick), it is 6 to 7 s.
- A live detection from the same standpoint (within 1 m, 35°, 20 s) also confirms a single view. In this replay the
  instant fixes' coarse positions were too far from the full fix's, so the second full fix confirmed it.

## Not measured yet

- The true-positive rate on Seb's real orange box. It needs the box shown to the glasses camera in zone find-test.
- The latency from putting the real box down to the answer saying the new spot (live, with Seb).
- The size check on real fixes. It needs this branch's `items.py` on the worker.
