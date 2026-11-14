# Route finding laid on real rooms (TUM RGB-D)

Measured on 14 November 2026 on CPU cores of Iridis: the way to five places from each of 472
position fixes in three rooms, laid on the rooms' true obstacles (Kinect depth). `route.py` had so
far only been tested on a made-up room (`eval/test_route.py`).

**Which run these numbers come from.** One full run finished and is reported here. A second,
stricter run is written and has NOT run: the cluster could not be reached from the laptop when it
was ready. The differences are listed under "The run that is still owed". Until it has run, read
the numbers below as a first measurement, and the per-room explanations as not yet separated.

## Short answer

1. **On the map it is given, the route does what it should.** In the made-up room it goes around
   the table, keeps half a body away from it, uses a 1.0 m gap and refuses a 0.5 m gap. On the real
   rooms the pictures show the same: routes go around everything the floor plan shows.
2. **On real rooms, with the floor plan a fix carries today, it is better than the straight arrow
   and not good enough to trust.** What the display would show is clear of true obstacles for 28%
   of the ways (593 of 2090), against 12% for today's straight arrow. On average 33% of the shown
   line lies over something that is truly in the way, against 65% for the arrow.
3. **The floor plan is the limit, not the search.** `map.points` was made for drawing: 1500
   points, heights between the 40th and 90th percentile. Furniture below that band, or with only a
   few of the 1500 points, is not on it, and the route walks through it. With the same model points
   cut from ankle to head height above the floor (the floor found in the model's own points, no
   truth needed) 53% of the ways are clear. In the one room where the map is otherwise sound
   (fr3_office) it is 97%, and 100% when every point is kept instead of 1500.
4. **Two rooms stay bad with every floor plan tried**: the cluttered office (fr1_office, 4% to 13%
   clear) and the desks in the hall (fr2_desk, 44% to 46%). The causes are not separated yet.
   Known candidates: the map's "up" is tilted by 19 and 11 degrees there, the office map is bent
   (`LOCATE_WALK_RESULTS.md`), the Kinect is held out over desks so a fifth of the walks start
   "inside" something, and the yardstick of this run counts a chair that was moved between two
   recordings as blocked in both spots.
5. **A route was found for 86% of the ways.** For the rest the answer is the straight arrow with
   the reason "no way through on the map".
6. **A route is longer**: 1.36 times the straight line (median), 2.3 times at the 90th
   percentile, 5.3 times at worst. It has 3 corners (median), 6 at the 90th percentile.
7. **It costs 29 ms per fix** (five places, plain Python, one core; slowest 37 ms).
8. **What did not help:** keeping to within 1 m of where the baseline camera walked (a quarter
   fewer routes: 63% instead of 86%, and the rest no clearer), a narrower or a wider body, and dropping the extra cost
   near things (worse).

What follows from this: the display can show routes today, with `&route=0` as the way back to the
straight arrow. For a wearer to rely on them, a fix has to carry a floor plan cut for walking.
`route.py` already uses it when it is there (`map.obstacles`, see "What a fix would need to carry").

## What was built

`route.py` (plain Python, no numpy). In the map's east/north metres:

- **Grid:** cells of 0.1 m over everything the floor plan covers.
- **A cell is solid** when a map point fell in it. **Closed** when it is solid, or closer than
  half a body to a solid cell, or outside the outline of everything the baseline saw (the convex
  hull of the points and the baseline cameras). **Open** otherwise. Open means "nothing was seen
  there", not "the floor was seen there": a fix carries no floor.
- **A person is 0.6 m wide**, in the map's metres. Those are 24% to 54% too long in these rooms,
  so here it is 0.39 to 0.48 true metres.
- **Cost:** a cell within 0.3 m of the body's edge costs up to twice its length, and a cell far
  from where the baseline camera walked costs up to half as much again.
- **Search:** one shortest-path search from the wearer over the open cells (Dijkstra, which is A*
  without a guess; one search serves every place). The cell path is cut down to corners; corners
  closer together than 0.5 m are made one.
- **End:** a place is usually on something, so the route ends at the open cell nearest to it, at
  most 1.2 m away, and the last leg is the reach from there to the place.
- **No route:** the straight line, with the reason (`too few points`, `the map is too large`,
  `no open floor where you stand`, `no way through on the map`). A lost fix gets no route.

## What was tested

**The fixes.** The walking test's 537 same-room fixes with 12 baseline frames and 1 query frame
(`LOCATE_WALK_RESULTS.md`): 453 found, 19 unsure, 65 lost. The 472 that are not lost each ask for
the way to the room's five places. Nothing went through the GPU again: the test had kept every
camera and the baseline's 3D points per fix, and the floor plan is rebuilt from those with
`locate.py`'s own rule (commit 48813b7). It is the same kind of plan, not the identical 1500 points.

**The truth.** Kinect depth moved into the room with the motion-capture pose of each frame, in
cells of 5 cm. A cell is truly blocked when at least three depth pictures put two or more points
between 0.12 m (ankle) and 1.9 m (head) above the floor there; truly free when at least three saw
the floor there and it is not blocked; not measured otherwise. The floor is a plane fitted to the
Kinect points. Depth pixels at depth edges and beyond 3.5 m are left out. In this run the truth
uses every recording of a room (five for fr1_office, two for fr2_desk, one for fr3_office).

| | fr1_office | fr2_desk | fr3_office |
| --- | --- | --- | --- |
| Area the Kinect measured | 29 m2 | 18 m2 | 24 m2 |
| Of it truly blocked | 72% | 54% | 24% |
| Map metres per true metre (median) | 1.54 | 1.25 | 1.31 |

**Laying a route on the truth.** The model's baseline cameras are fitted to their true positions
(one fit per fix, as in the walking test). A route point is taken at the wearer's own height on the
map, because that is where the next fixes would see the wearer's head, and moved into the truth
with that fit. So the tilt of the map and the error of the fit are in the result, as they would be
in a home.

**What is judged.** The line the display would lead along: the route, or the straight line when
there is no route. Not judged: the first 0.3 m (the Kinect is held out in front of the person),
and the last leg, the reach to the place (a place is on a desk). The straight arrow is cut by the
same length at its end. Only ways to places truly 1 m away or more are judged: about 2090.

**Clear** means at most 0.1 m of the judged line lies over truly blocked cells.

## Results

All 472 fixes. "Clear" is about what the display would show (the route, else the straight line).

| Setting | A route was found | Clear: all rooms | fr1_office | fr2_desk | fr3_office | Truly blocked share of the line, mean | Route / straight line: median / 90th / longest |
| --- | --- | --- | --- | --- | --- | --- | --- |
| today: the straight arrow | - | 241 of 2092 (12%) | 0 of 605 (0%) | 158 of 778 (20%) | 83 of 709 (12%) | 65% | 1.00 |
| **`route.py` as committed** | 1789 of 2090 (86%) | 593 of 2090 (28%) | 24 of 605 (4%) | 342 of 777 (44%) | 227 of 708 (32%) | 33% | 1.36 / 2.30 / 5.31 |
| lone map points dropped as noise | 1790 of 2092 (86%) | 578 of 2092 (28%) | 20 of 605 (3%) | 342 of 778 (44%) | 216 of 709 (30%) | 34% | 1.35 / 2.31 / 4.88 |
| unseen floor costs more (5 instead of 0.5) | 1786 of 2088 (86%) | 602 of 2088 (29%) | 31 of 605 (5%) | 342 of 777 (44%) | 229 of 706 (32%) | 32% | 1.38 / 2.33 / 4.93 |
| only within 1.0 m of the walked line | 1322 of 2092 (63%) | 596 of 2092 (28%) | 18 of 605 (3%) | 344 of 778 (44%) | 234 of 709 (33%) | 42% | 1.31 / 2.09 / 4.91 |
| only within 0.6 m of the walked line | 855 of 2091 (41%) | 556 of 2091 (27%) | 0 of 605 (0%) | 383 of 776 (49%) | 173 of 710 (24%) | 49% | 1.34 / 2.05 / 3.40 |
| no extra cost near things | 1796 of 2098 (86%) | 530 of 2098 (25%) | 7 of 605 (1%) | 313 of 778 (40%) | 210 of 715 (29%) | 36% | 1.28 / 2.10 / 4.41 |
| body 0.4 m | 1926 of 2109 (91%) | 494 of 2109 (23%) | 31 of 605 (5%) | 262 of 790 (33%) | 201 of 714 (28%) | 35% | 1.28 / 2.12 / 5.13 |
| body 0.8 m | 1662 of 2094 (79%) | 489 of 2094 (23%) | 21 of 605 (3%) | 231 of 780 (30%) | 237 of 709 (33%) | 34% | 1.51 / 2.59 / 6.77 |
| floor plan cut by the model's own floor, 1500 points | 1785 of 2088 (85%) | 1099 of 2088 (53%) | 54 of 605 (9%) | 359 of 775 (46%) | 686 of 708 (97%) | 23% | 1.46 / 2.44 / 6.36 |
| the same, every point (about 31 000) | 1756 of 2062 (85%) | 1116 of 2062 (54%) | 70 of 605 (12%) | 352 of 763 (46%) | 694 of 694 (100%) | 22% | 1.50 / 2.54 / 7.25 |
| floor plan cut by the TRUE floor, 1500 points | 1803 of 2077 (87%) | 1108 of 2077 (53%) | 63 of 605 (10%) | 351 of 767 (46%) | 694 of 705 (98%) | 22% | 1.46 / 2.42 / 6.40 |
| the same, every point (about 33 000) | 1786 of 2062 (87%) | 1124 of 2062 (55%) | 78 of 605 (13%) | 352 of 763 (46%) | 694 of 694 (100%) | 21% | 1.50 / 2.52 / 7.25 |

The committed row and the two 1500-point rows of the cut floor plans count lone map points; the
other rows drop them (they were run before that default changed, see "How sure"). The counts of
ways differ by a few between rows because a different end of the route changes which stretch is
judged. Corners were counted before close corners were merged, so today's routes have fewer.

Only the 639 ways that start where the wearer truly stands on free floor with 0.2 m to spare (the
Kinect was over free floor in 284 of the 472 fixes, over something in 97, over cells never
measured in 91):

| Setting | A route was found | Clear: all rooms | fr1_office | fr2_desk | fr3_office | Truly blocked share of the line, mean |
| --- | --- | --- | --- | --- | --- | --- |
| today: the straight arrow | - | 93 of 639 (15%) | 0 of 153 (0%) | 29 of 115 (25%) | 64 of 371 (17%) | 61% |
| **`route.py` as committed** | 561 of 639 (88%) | 190 of 639 (30%) | 1 of 153 (1%) | 63 of 115 (55%) | 126 of 371 (34%) | 26% |
| floor plan cut by the model's own floor, 1500 points | 569 of 638 (89%) | 443 of 638 (69%) | 18 of 153 (12%) | 67 of 116 (58%) | 358 of 369 (97%) | 14% |
| the same, every point | 561 of 630 (89%) | 446 of 630 (71%) | 23 of 153 (15%) | 60 of 114 (53%) | 363 of 363 (100%) | 13% |

Longer walks, starting where each of the baseline cameras stood instead of at the wearer (every
eighth fix, 3432 ways, lone points dropped): a route for 85%, clear 25% (7%, 36% and 27% per room),
1.28 times the straight line.

From a first try on 24 fixes only (8 per room), not repeated on all: every fallback was "no way
through on the map"; a route walked 0.8 m more than the straight line (median, true
metres), 2.5 m at the 90th percentile, 7 m at worst; and it ended 0.3 m from the true place
(median), 0.9 m at worst.

## Reading the numbers

**fr3_office, two desks with a partition, a quarter of the room blocked.** Today's floor plan: 32%
clear. Cut for walking: 97%. Nothing else changed between those two rows, so in this room the
height band is what stands between the route and a clear way. The 40th to 90th percentile is not a
height: where the camera mostly looks down at desks, the 40th percentile lies above the desk tops,
and the desks drop out of the plan with the floor.

**fr2_desk, two desks in a large hall.** 44% to 46% whatever the floor plan, and 20% for the
straight arrow. In the pictures the routes go around the desks the right way. What they cross is
blocked cells at the rim of the hall, where the plan has no points at all, with any band. In one
picture the baseline camera's own true positions lie over such cells, which a person can only do
by holding the Kinect out over something, or which means the truth is too fat there. Not settled.

**fr1_office, a cluttered office, 72% of what the Kinect measured is blocked.** Under 13% with
everything tried, and 0% for the straight arrow. Three things are known to be wrong at once: the
free floor is a narrow, winding strip; the map is bent and its "up" is 19 degrees off, so a thing
at desk height lands 0.2 to 0.3 m beside where it truly is, which is the whole body margin; and
twelve baseline frames do not see every side of the room, so large pieces of furniture are not in
the model's points at all.

**Keeping to where the baseline camera walked** sounded right (it is the only floor known to be
free) and did not pay: the routes that remain are no clearer, and the ways that lose their route
get the straight arrow, which is worse.

**How sure.** The fixes of one room share one baseline and their floor plans are near copies of
each other. So these are three rooms, not 472 samples: read the percentages as "about". The one
default that was changed after seeing numbers (lone points count) moved the result by less than
that.

## What a fix would need to carry

Asked of `locate.py` and the worker (not touched here; position fix v2, pull request 28, improves
"up" and the metres and keeps the same floor plan):

- **`map.obstacles`**: the same kind of list as `map.points`, but every point from 0.12 m to 1.9 m
  above the floor. The floor can be found in the model's own points: the lowest large flat surface
  (`eval/locate_walk_tables.py`, `floor_plane`) with the baseline cameras taken to be 1.6 m above
  it gave the same result as the true floor (53% and 53%), and failed to find a floor in 6 of 472
  fixes. `route.py` routes on `map.obstacles` whenever a fix has it.
- **More than 1500 points, or cells instead of points.** Every point did a little better than 1500
  (54% against 53%, and 100% against 97% in fr3_office). A list of the solid 0.1 m cells says the
  same in fewer bytes; `/where` must stay under 64 kB for the event API.
- **The floor itself**, so that "open" can mean "the floor was seen there". Not tried here.

## The run that is still owed

`route_eval.py` as committed differs from the run reported above (commit bf45706, started as
`--every-recording --every 3`, and `route.py` before corners were merged and before lone points
counted by default). It has not been run. What it adds:

- The truth from the baseline recording alone, the room as it was when the map was made (the
  default; `--every-recording` gives the old one).
- The yardstick judged by itself: the path the Kinect was truly carried along is laid on the truth
  like a route. Whatever share of it is "not clear" is the hand-held camera or the truth, not a
  route, and is the ceiling for every number above.
- How deep a line goes into something: brushing an edge (never more than 0.15 m inside) against
  walking through it.
- An upright map: "up" from the floor found in the model's points instead of the cameras' average,
  as a stand-in for position fix v2. This is what should separate the tilt from the rest in
  fr1_office and fr2_desk.
- The full tables per room (reasons for the straight arrow, extra metres, where routes end) on all
  472 fixes, which this file has only from 24.

To run it, from `<work folder>/route/` with `code/` holding `route_eval.py`, `locate_walk_eval.py`,
`locate_walk_tables.py` and `route.py`:

    sbatch code/route_eval.slurm --out out_final --pictures 3

About ten minutes on four cores. It reads the walking test's folder and writes only its own.

## What was not tested

- A home, a doorway, a corridor, a second room. All three rooms are one open space; a map is one room.
- The glasses, and the live relay with the cluster. The relay's blocks were tested offline against
  a stand-in gateway, the display in a browser on made-up fixes.
- A person walking a route. Routes were laid on depth measurements; nobody followed the arrow.
- A wearer who moves between fixes. A route is as old as its fix; between fixes the display only
  turns with the compass.
- The steady position of the tracker (pull request 31). Routes start at the raw fix.
- A room with a known distance set, where the metres and so the body width would be right.
- 24 baseline frames, and 3 query frames.
- Things moved since the baseline, people, pets, doors open or shut.
- Steps, stairs, rugs, thresholds, and anything lower than 0.12 m.
- Unsure fixes on their own: 19 of the 472.
- Whether a route can do harm where the straight arrow was fine. On 24 fixes it was clear in 3 of 4
  such ways; the full count is part of the run that is still owed.
