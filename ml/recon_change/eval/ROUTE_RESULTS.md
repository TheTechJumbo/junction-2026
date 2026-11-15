# Route finding laid on real rooms (TUM RGB-D)

Measured on 14 November 2026 on CPU cores of the cluster: the way to five places from each of 472
position fixes in three rooms, laid on the rooms' true obstacles (Kinect depth). `route.py` had so
far only been tested on a made-up room (`eval/test_route.py`). Code measured: `route.py` and
`eval/route_eval.py` as committed with this file.

## Short answer

1. **On the map it is given, the route does what it should.** In the made-up room it goes around
   the table, keeps half a body away from it, uses a 1.0 m gap and refuses a 0.5 m gap. On the real
   rooms the pictures show the same: routes go around everything the floor plan shows.
2. **On real rooms it beats the straight arrow on every count, and is still wrong half the time.**
   The fixes are the walking test's, made before position fix v2. What the display would show:

   | | Straight arrow (today) | Route, else the arrow |
   | --- | --- | --- |
   | Clear of true obstacles | 13% (273 of 2088) | 29% (601) |
   | Goes through something (more than 0.15 m inside it) | 79% (1648) | 50% (1040) |
   | Share of the line over something truly in the way, mean | 61% | 27% |

3. **The search is not the limit. Two things in the fix are:**
   - *The floor plan.* `map.points` was made for drawing: 1500 points, heights between the 40th
     and 90th percentile. Furniture below that band is not on it. With the same model points cut
     from ankle to head height above the floor (the floor found in the model's own points, no
     truth needed), 60% are clear and 16% go through something. In the room where the map is
     otherwise sound (fr3_office): 99% clear instead of 32%.
   - *The map's tilted "up"* (19, 11 and 8 degrees off in these rooms). With an upright map and
     today's floor plan, 46% are clear and 31% go through something; in the cluttered office
     36% are clear instead of 11%. Position fix v2, now in the base branch, sets "up" better.
     This was measured with a stand-in for it, not with v2 itself.
   - *Both together*, where a route is found: 68% clear, 10% through something. Per room 91% and
     1% (fr1_office), 97% and 0% (fr3_office), 33% and 22% (fr2_desk).
4. **fr2_desk cannot be judged finer than that.** The yardstick was laid on itself: the path the
   Kinect was truly carried along counts as clear for only 52% of its stretches there, and as
   going through something for 32%, because the hand-held Kinect was carried over things. The same
   check gives 60% and 0% in fr1_office, 90% and 10% in fr3_office.
5. **The cluttered office has no route for 40% of its ways** ("no way through on the map"), and
   the straight arrow shown instead goes through things. Over all rooms a route is found for 87%
   of the ways (2054 of 2360); every fallback has that one reason.
6. **A route is longer**: 1.33 times the straight line (median), 2.3 times at the 90th
   percentile, 5.6 times at worst. In true metres: 0.7 m more (median), 2.7 m at the 90th
   percentile, 6.5 m at worst. Two corners (median), four at the 90th percentile, eight at most.
7. **It rarely does harm where the arrow was fine.** Of the 273 ways where the straight arrow was
   clear, what is shown is clear too for 246 (90%). Of the 440 where the arrow went through
   nothing, what is shown goes through something for 35 (8%).
8. **It costs 28 ms per fix** (five places, plain Python, one core; slowest 32 ms).

What follows: the display can show routes today, with `&route=0` as the way back to the straight
arrow. For a wearer to rely on them, a fix has to carry a floor plan cut for walking. `route.py`
already uses one when it is there (`map.obstacles`, see "What a fix would need to carry").

## What was built

`route.py` (plain Python, no numpy). In the map's east/north metres:

- **Grid:** cells of 0.1 m over everything the floor plan covers.
- **A cell is solid** when a map point fell in it. **Closed** when it is solid, or closer than
  half a body to a solid cell, or outside the outline of everything the baseline saw (the convex
  hull of the points and the baseline cameras). **Open** otherwise. Open means "nothing was seen
  there", not "the floor was seen there": a fix carries no floor.
- **A person is 0.6 m wide**, in the map's metres. Those are 25% to 54% too long in these rooms,
  so here it is 0.39 to 0.48 true metres.
- **Cost:** a cell within 0.3 m of the body's edge costs up to twice its length. A cell far from
  where the baseline camera walked costs up to three times its length, because that walk is the
  only floor known to be free.
- **Search:** one shortest-path search from the wearer over the open cells (Dijkstra, which is A*
  without a guess; one search serves every place). The cell path is cut down to corners; corners
  closer together than 0.5 m are made one.
- **End:** a place is usually on something, so the route ends at the open cell nearest to it, at
  most 1.2 m away, and the last leg is the reach from there to the place.
- **No route:** the straight line, with the reason (`the map has too few points`, `the map is too
  large`, `no open floor where you stand`, `no way through on the map`). A lost fix gets no route.
- **Start:** the relay starts the route on the steady pose (`stable`) when there is one, else on
  the fix, because that is where the display stands.

## What was tested

**The fixes.** The walking test's 537 same-room fixes with 12 baseline frames and 1 query frame
(`LOCATE_WALK_RESULTS.md`): 453 found, 19 unsure, 65 lost. The 472 that are not lost each ask for
the way to the room's five places: 2360 ways. Nothing went through the GPU again: the test had kept
every camera and the baseline's 3D points per fix, and the floor plan is rebuilt from those with
`locate.py`'s rule as it was then (commit 48813b7). It is the same kind of plan, not the identical
1500 points. The wearer's position on the rebuilt map and the one `locate.py` gave are at most
5 mm apart.

**The truth.** Kinect depth of the baseline recording, moved into the room with the motion-capture
pose of each frame, in cells of 5 cm. A cell is truly blocked when at least three depth pictures
put two or more points between 0.12 m (ankle) and 1.9 m (head) above the floor there; truly free
when at least three saw the floor there and it is not blocked; not measured otherwise. The floor is
a plane fitted to the Kinect points. Depth pixels at depth edges and beyond 3.5 m are left out.

| | fr1_office | fr2_desk | fr3_office |
| --- | --- | --- | --- |
| Depth pictures used | 681 | 1130 | 1244 |
| Area the Kinect measured | 25 m2 | 18 m2 | 25 m2 |
| Of it truly blocked | 72% | 48% | 24% |
| Map metres per true metre (median) | 1.54 | 1.25 | 1.31 |

**Laying a route on the truth.** The model's baseline cameras are fitted to their true positions
(one fit per fix, as in the walking test). A route point is taken at the wearer's own height on the
map, because that is where the next fixes would see the wearer's head, and moved into the truth
with that fit. So the tilt of the map and the error of the fit are in the result, as they would be
in a home.

**What is judged.** The line the display would lead along: the route, or the straight line when
there is no route. Not judged: the first 0.3 m (the Kinect is held out in front of the person),
and the last leg, the reach to the place (a place is on a desk). The straight arrow is cut by the
same length at its end. Only ways to places truly 1 m away or more are judged: 2088 of the 2360.

- **Clear:** at most 0.1 m of the judged line lies over truly blocked cells.
- **Brushes an edge:** not clear, and never more than 0.15 m inside something.
- **Goes through something:** more than 0.15 m inside something at its deepest.

**The yardstick laid on itself.** The Kinect was truly carried along its recorded path, so a
person truly got through there. That path, cut into overlapping 2 m stretches and judged like a
route, is the best any route can score:

| The path the Kinect was truly carried along | fr1_office | fr2_desk | fr3_office |
| --- | --- | --- | --- |
| Stretches | 25 | 31 | 40 |
| Clear | 15 (60%) | 16 (52%) | 36 (90%) |
| Brushes an edge | 10 (40%) | 5 (16%) | 0 |
| Goes through something | 0 | 10 (32%) | 4 (10%) |

So "clear" is strict in fr1_office and fr2_desk: a hand-held Kinect sweeps over desk edges. "Goes
through something" is a fair test in fr1_office and fr3_office and not in fr2_desk.

## Results

All 472 fixes. Everything is about what the display would show (the route, else the straight
line). Per room in the order fr1_office / fr2_desk / fr3_office.

| Setting | A route was found | Clear: all rooms; per room | Goes through something: all rooms; per room | Truly blocked share of the line, mean | Route / straight line: median / 90th / longest |
| --- | --- | --- | --- | --- | --- |
| today: the straight arrow | - | 13% (273 of 2088); 7 / 20 / 11 | 79% (1648); 84 / 71 / 84 | 61% | 1.00 |
| **`route.py` as committed** | 86% (1787 of 2088) | 29% (601); 11 / 40 / 32 | 50% (1040); 80 / 11 / 66 | 27% | 1.33 / 2.26 / 5.56 |
| lone map points dropped as noise | 86% | 27% (573); 10 / 40 / 29 | 51% (1076); 82 / 11 / 69 | 28% | 1.32 / 2.27 / 5.57 |
| unseen floor costs 0.5 instead of 2 | 86% | 27% (572); 5 / 42 / 31 | 53% (1105); 89 / 11 / 68 | 28% | 1.33 / 2.27 / 5.60 |
| unseen floor costs 5 | 86% | 31% (649); 18 / 39 / 34 | 47% (981); 74 / 11 / 63 | 26% | 1.36 / 2.29 / 5.59 |
| unseen floor costs 10 | 86% | 30% (635); 18 / 37 / 34 | 47% (971); 74 / 10 / 63 | 25% | 1.39 / 2.28 / 5.56 |
| only within 1.0 m of the walked line | 63% | 29% (596); 8 / 40 / 34 | 48% (1010); 81 / 11 / 61 | 36% | 1.29 / 2.08 / 5.53 |
| only within 0.6 m of the walked line | 41% | 26% (543); 4 / 43 / 26 | 57% (1194); 84 / 25 / 70 | 46% | 1.34 / 2.05 / 3.25 |
| no extra cost near things | 86% | 29% (602); 8 / 45 / 29 | 52% (1082); 84 / 11 / 69 | 29% | 1.29 / 2.11 / 5.27 |
| body 0.4 m | 91% | 24% (510); 8 / 33 / 29 | 56% (1175); 83 / 24 / 68 | 29% | 1.26 / 2.12 / 5.41 |
| body 0.8 m | 80% | 29% (613); 17 / 27 / 42 | 44% (920); 75 / 22 / 41 | 27% | 1.51 / 2.54 / 6.91 |
| floor plan cut by the model's own floor, 1500 points | 86% | 56% (1158); 24 / 43 / 97 | 19% (405); 54 / 9 / 2 | 17% | 1.44 / 2.40 / 7.12 |
| the same, every point (about 31 000) | 85% | 60% (1236); 34 / 45 / 99 | 16% (327); 46 / 6 / 1 | 16% | 1.49 / 2.53 / 7.63 |
| floor plan cut by the TRUE floor, 1500 points | 87% | 56% (1171); 25 / 43 / 97 | 18% (370); 49 / 9 / 1 | 16% | 1.43 / 2.39 / 6.43 |
| the same, every point (about 33 000) | 87% | 60% (1245); 36 / 44 / 100 | 15% (304); 43 / 6 / 0 | 15% | 1.48 / 2.52 / 7.63 |
| upright map, today's floor plan | 91% | 46% (955); 36 / 32 / 70 | 31% (640); 51 / 25 / 20 | 20% | 1.50 / 2.49 / 6.11 |
| upright map, plan cut by the model's own floor, 1500 points | 85% | 56% (1144); 40 / 38 / 88 | 22% (452); 50 / 14 / 7 | 19% | 1.50 / 2.43 / 5.51 |
| the same, every point | 85% | 58% (1181); 45 / 33 / 97 | 22% (447); 47 / 22 / 0 | 19% | 1.56 / 2.60 / 6.66 |

"Upright map": "up" is the normal of the floor found in the model's points instead of the average
of the baseline cameras, and everything (wearer, places, floor plan) is laid out again with it. It
stands in for the better "up" of position fix v2. It is not v2's code.

The routes alone, without the ways that fell back to the straight arrow:

| Setting | Routes judged | Clear: all rooms; per room | Goes through something: all rooms; per room | Ends this far from the true place, true m: median / 90th / farthest |
| --- | --- | --- | --- | --- |
| `route.py` as committed | 1787 | 34% (599); 21 / 41 / 32 | 43% (771); 71 / 9 / 66 | 0.46 / 0.85 / 1.19 |
| floor plan cut by the model's own floor, every point | 1762 | 70% (1236); 67 / 45 / 100 | 3% (55); 3 / 6 / 0 | 0.66 / 0.88 / 1.04 |
| upright map, today's floor plan | 1881 | 51% (955); 51 / 32 / 70 | 26% (480); 35 / 25 / 20 | 0.58 / 0.81 / 1.06 |
| upright map, plan cut by the model's own floor, every point | 1731 | 68% (1180); 91 / 33 / 97 | 10% (173); 1 / 22 / 0 | 0.71 / 0.89 / 1.04 |

Why the straight arrow was shown, `route.py` as committed, of 690 / 900 / 770 ways asked per room:
"no way through on the map" 276 (40%) / 30 (3%) / 0. No other reason occurred.

Only the 698 ways that start where the wearer truly stands on free floor with 0.2 m to spare (the
Kinect was over free floor in 259 of the 472 fixes, over something in 74, over cells never
measured in 139):

| Setting | A route was found | Clear: all rooms; per room | Goes through something: all rooms; per room | Truly blocked share, mean |
| --- | --- | --- | --- | --- |
| today: the straight arrow | - | 18% (125 of 698); 13 / 22 / 17 | 76% (530); 86 / 68 / 78 | 55% |
| **`route.py` as committed** | 91% | 33% (230); 12 / 43 / 33 | 51% (358); 79 / 13 / 66 | 20% |
| floor plan cut by the model's own floor, every point | 93% | 78% (534); 48 / 51 / 100 | 7% (47); 46 / 0 / 0 | 7% |
| upright map, today's floor plan | 96% | 63% (437); 36 / 60 / 71 | 23% (161); 57 / 13 / 21 | 11% |
| upright map, plan cut by the model's own floor, every point | 94% | 76% (527); 47 / 54 / 97 | 11% (75); 49 / 13 / 0 | 10% |

Longer walks, starting where each of the baseline cameras stood instead of at the wearer (every
eighth fix, 3446 ways judged, `route.py` as committed): a route for 85%, clear 32% (23 / 40 / 32),
through something 47% (63 / 17 / 68), 1.25 times the straight line (median).

How deep, `route.py` as committed: the deepest point of what is shown is 0.15 m inside something
(median), 0.40 m at the 90th percentile, 0.96 m at worst. For the straight arrow: 0.41, 0.90, 0.96 m.

**The other truth, as a cross-check.** With the truth taken from every recording of a room (five
for fr1_office, two for fr2_desk) instead of the baseline recording alone, the Kinect's own path
counts as clear for only 48% and 23% of its stretches in those two rooms, and as going through
something for 20% and 45%. The recordings do not line up to the centimetre, and things were moved
between them. That truth is too fat to judge by. For the record, on it: straight arrow 11% clear,
`route.py` as committed 28%, plan cut by the model's own floor (every point) 54%.

## Reading the numbers

**fr3_office, two desks with a partition, a quarter of the room blocked.** Today's floor plan: 32%
clear, 66% through something. Cut for walking: 99% and 1%. Nothing else differs between those two
rows, so here the height band is what stands between the route and a clear way. The 40th to 90th
percentile is not a height: where the camera mostly looks down at desks, the 40th percentile lies
above the desk tops, and the desks drop out of the plan together with the floor.

**fr1_office, a cluttered office, 72% of what the Kinect measured is blocked.** Two separate
troubles. First the tilt: the map's "up" is 19 degrees off, so a thing at desk height lands 0.2 to
0.3 m beside where it truly is, which is the whole body margin. Upright, with a plan cut for
walking, a route that exists is clear 91% of the time and goes through something 1% of the time.
Second, a route often does not exist: 26% to 45% of the ways here have no route, depending on the
plan, nearly all for "no way through on the map", and the straight arrow shown instead goes through
things 8 times in 10.
Twelve baseline frames do not see every side of this room, and the places are on desks deep in the
clutter. Whether those places can be reached at all within the 1.2 m reach was not looked at.

**fr2_desk, two desks in a large hall.** Routes go through something for 11% of the ways (the
arrow: 71%), brush an edge for 49%, and their deepest point is 0.05 m inside (median). That is
better than the Kinect's own path scores there (32% through something), so this room cannot tell a
good route from a perfect one. The upright map scores worse here (25%); the cause was not found,
and with this room's yardstick it cannot be told from noise.

**The settings.** None moves the result by much. Lone points count because the plan has few points
and it did slightly better (29% against 27% clear). "Unseen floor" costs 2: 0.5 was worse in the
office (5% against 11% clear), 5 was a little better there (18%) and equal elsewhere, but in an
empty made-up room it bends the route further off the straight line (18% longer with three
corners, against 15% and two). 2 is a middle, chosen on these three rooms. Keeping strictly to
within 1 m of the walked line loses a quarter of the routes and gains nothing. A body of 0.4 m is
worse; 0.8 m is better in two rooms and worse in the third, and finds fewer routes.

**How sure.** The fixes of one room share one baseline and their floor plans are near copies of
each other. So these are three rooms, not 472 samples: read the percentages as "about". The
settings were chosen on the same three rooms they are reported on.

## What a fix would need to carry

Asked of `locate.py` and the worker, which this work did not touch:

- **`map.obstacles`**: the same kind of list as `map.points`, but every point from 0.12 m to 1.9 m
  above the floor. The floor can be found in the model's own points: the lowest large flat surface
  (`eval/locate_walk_tables.py`, `floor_plane`), with the baseline cameras taken to be 1.6 m above
  it, gave the same result as the true floor (56% and 56% clear with 1500 points, 60% and 60% with
  every point) and found no floor in 2 of 472 fixes. `route.py` routes on `map.obstacles` whenever
  a fix has it, and on `map.points` otherwise.
- **More than 1500 points, or cells instead of points.** Every point did better than 1500 (60%
  against 56% clear; going through something 16% against 19%). A list of the solid 0.1 m cells
  says the same in fewer bytes; `/where` must stay under 64 kB for the event API.
- **The floor itself**, so that "open" can mean "the floor was seen there". Not tried here.

## What was not tested

- Position fix v2 itself. The fixes are from before it; its better "up" was imitated, its real
  metres (a known distance) were not. With real metres the body would be its true 0.6 m.
- A home, a doorway, a corridor, a second room. All three rooms are one open space; a map is one room.
- The glasses, and the live relay with the cluster. The relay's blocks were tested offline against
  a stand-in gateway, the display in a browser on made-up fixes.
- A person walking a route. Routes were laid on depth measurements; nobody followed the arrow.
- A wearer who moves between fixes. A route is as old as its fix; between fixes the display only
  turns with the compass.
- The steady position of the tracker on real data. The relay starts the route there when it
  exists; the measurement starts every route at the raw fix.
- 24 baseline frames, and 3 query frames.
- Things moved since the baseline, people, pets, doors open or shut.
- Steps, stairs, rugs, thresholds, and anything lower than 0.12 m.
- Unsure fixes on their own: 19 of the 472.
- Why the cluttered office so often has no way through, and why the upright map is worse in fr2_desk.

## Run it again

On the cluster, in `<work folder>/route/` with `code/` holding `route_eval.py`, `route_eval.slurm`,
`locate_walk_eval.py`, `locate_walk_tables.py` and `route.py`. It reads the walking test's folder
(`../walk`: `out/plan.json`, `out/results.jsonl`, `out/fix/`, `data/tum/`) and writes only its own:

    sbatch code/route_eval.slurm --out out_final --pictures 3
    sbatch code/route_eval.slurm --out out_final_all --every-recording --every 3   # the other truth

About eight minutes on four cores. `out_final/route_eval.md` holds every table above and more;
`--pictures` draws routes on the true room (not kept in the repository).
