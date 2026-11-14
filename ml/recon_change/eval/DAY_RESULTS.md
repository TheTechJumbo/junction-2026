# Day log: how right are the visits? (replay on TUM RGB-D)

Measured on 14 November 2026 by replaying the 537 saved same-room position fixes of the walking
test (`LOCATE_WALK_RESULTS.md`) through `day.build_visits`. CPU only, 5 seconds, no new fixes were
run: the cluster's two GPUs were taken. Script: `eval/day_eval.py`.

## Short answer

1. **No visit was invented.** In every replay with the shipped limits, every visit the log wrote
   down overlapped a true visit to that place: 0 invented, out of 48 to 153 logged visits per replay.
2. **With one known distance in the room, 84% of the true visits are found at one photo a
   second and 78% at one photo every 6 seconds.** Of the true visits long enough to hold two
   photos, 91% to 96% are found. The rest are missed because the fixes there were "lost" or because
   the visit was shorter than the gap between photos, not because of distance errors: reading the
   distances perfectly gives the same 84% and 76%.
3. **With today's metres (no known distance), only about half are found (54% to 48%) and the time
   logged is about half the true time.** The map's metres are 25% to 54% too long, so "within
   1.5 m" really means within 1.0 to 1.2 m. Nothing is invented either way. Starting the relay with
   `BEACON_NEAR_M=2.0` brings it back to 84% and 80% found, still with nothing invented.
4. **No visit was split in two** with the shipped limits and a known distance. Without the
   0.5 m of slack between entering and leaving, 5% to 12% were.
5. **The time spent is short by about one photo interval per visit**: median error 3.2 s at one
   photo a second, 7.7 s at one every 6 s. A visit is logged from its first to its last found fix.
6. **The confidence percentages are too low, and this data cannot check them further.** Every
   logged visit was real, at every stated confidence from 60% to 99%. See "Confidence" below.

**A whole day in a home was not tested.** These are eight recordings of 19 to 121 seconds in three
offices. Everything about minutes and hours (the 60 s gap, walks, repeated questions, reminders,
day files) is covered only by the offline tests on a made-up day.

## The measured table

One known distance per room (each fix's `distance_m` divided by that room's median metres ratio),
shipped limits: enter at 1.5 m, leave beyond 2.0 m, 2 fixes in a row beyond that to end a visit.

| Room, one fix every | True visits | Found | Found, of those lasting 2 photos or more | Split in two | Visits logged | Invented | Time logged / true time | Error of time spent per found visit, median |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fr1_office, 1 s | 24 | 11 (46%) | 61% of 18 | 0 | 10 | 0 | 63% | 2.1 s (11%) |
| fr2_desk, 1 s | 40 | 40 (100%) | 100% of 40 | 0 | 32 | 0 | 103% | 2.9 s (22%) |
| fr3_office, 1 s | 18 | 18 (100%) | 100% of 18 | 0 | 16 | 0 | 84% | 4.8 s (75%) |
| **All rooms, 1 s** | 82 | 69 (84%) | 91% of 76 | 0 | 58 | 0 | 94% | 3.2 s (26%) |
| fr1_office, 3 s | 36 | 15 (42%) | 83% of 18 | 0 | 15 | 0 | 44% | 4.5 s (25%) |
| fr2_desk, 3 s | 60 | 57 (95%) | 98% of 54 | 0 | 44 | 0 | 103% | 6.3 s (37%) |
| fr3_office, 3 s | 27 | 27 (100%) | 100% of 18 | 0 | 22 | 0 | 96% | 5.8 s (93%) |
| **All rooms, 3 s** | 123 | 99 (80%) | 96% of 90 | 0 | 81 | 0 | 92% | 5.2 s (37%) |
| fr1_office, 6 s | 72 | 28 (39%) | 73% of 30 | 0 | 28 | 0 | 34% | 7.5 s (39%) |
| fr2_desk, 6 s | 120 | 109 (91%) | 99% of 84 | 0 | 82 | 0 | 99% | 9.3 s (45%) |
| fr3_office, 6 s | 54 | 54 (100%) | 100% of 12 | 0 | 43 | 0 | 100% | 5.8 s (100%) |
| **All rooms, 6 s** | 246 | 191 (78%) | 93% of 126 | 0 | 153 | 0 | 88% | 7.7 s (46%) |

How to read it:
- There are **41 distinct true visits** in the data. To use every saved fix, each recording was
  replayed with shifted starts (2 at 1 s, 3 at 3 s, 6 at 6 s), and the counts are added up. So the
  rows are not independent samples: read the percentages as "about".
- "Logged" can be lower than "found" because one logged visit can cover two true visits that lie
  close together in time (the leave slack and the 2-fix rule bridge them).
- The percentage in the last column is of the true length of the visit. The true visits here last
  a few seconds to half a minute, so an error of one photo interval is a large share. In seconds it
  would be the same for a ten-minute visit, but that was not measured.
- fr1_office is low because its fixes are: one recording (`freiburg1_360`) has 22% found, another
  56%. Where the fix is lost the log says "no information", which is the intended behaviour.

The same with other ways of reading the distance (all rooms):

| Distances read as | One fix every | True visits | Found | Split in two | Visits logged | Invented | Time logged / true time | Error of time spent, median |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Today: the map's own metres | 1 s | 82 | 44 (54%) | 6 | 48 | 0 | 52% | 7.9 s (48%) |
| | 3 s | 123 | 60 (49%) | 5 | 62 | 0 | 47% | 8.0 s (55%) |
| | 6 s | 246 | 117 (48%) | 0 | 109 | 0 | 45% | 8.2 s (62%) |
| Today's metres, limits raised to 2.0 m and 2.5 m (`BEACON_NEAR_M=2.0`), truth still at 1.5 m | 1 s | 82 | 69 (84%) | 2 | 61 | 0 | 95% | 4.5 s (36%) |
| | 3 s | 123 | 100 (81%) | 1 | 81 | 0 | 94% | 6.9 s (48%) |
| | 6 s | 246 | 197 (80%) | 0 | 153 | 0 | 91% | 8.2 s (70%) |
| Best case: the fix's camera fitted to the truth, true place positions | 1 s | 82 | 69 (84%) | 0 | 59 | 0 | 95% | 3.2 s (26%) |
| | 3 s | 123 | 98 (80%) | 0 | 78 | 0 | 90% | 4.5 s (39%) |
| | 6 s | 246 | 186 (76%) | 0 | 143 | 0 | 87% | 7.8 s (41%) |

## How it was measured

- **Truth.** The true path is the motion capture of each recording (`groundtruth.txt`), sampled at
  10 Hz. The true places are the five per room of the walking test (Kinect depth at one pixel, moved
  into the room with the true pose). The wearer is truly near a place when the horizontal distance
  is at most 1.5 m; a true visit ends when it exceeds 1.75 m, so the truth does not flicker at the
  limit.
- **The stream.** The saved fix of every same-room query (12 baseline frames + 1 frame), with its
  real state: 453 found, 19 unsure, 65 lost. Per recording they are put in time order and thinned
  to one fix per 1, 3 or 6 seconds. Unsure and lost fixes stay in the stream and count for nothing.
- **Counting.** A logged visit and a true visit of the same place are the same visit when they
  overlap in time, give or take 0.5 s. Found: a true visit with at least one logged visit. Split: a
  true visit with two or more. Invented: a logged visit with no true visit.

| Room | Recording | Seconds covered | Fixes | Median seconds between fixes | Found | Longest stretch without a found fix |
| --- | --- | --- | --- | --- | --- | --- |
| fr1_office | freiburg1_360 | 24 | 36 | 0.5 | 22% | 11.8 s |
| fr1_office | freiburg1_desk | 19 | 36 | 0.4 | 97% | 0.8 s |
| fr1_office | freiburg1_desk2 | 20 | 36 | 0.4 | 94% | 1.3 s |
| fr1_office | freiburg1_room | 37 | 36 | 0.1 | 56% | 23.2 s |
| fr1_office | freiburg1_xyz | 26 | 36 | 0.6 | 100% | 1.1 s |
| fr2_desk | freiburg2_desk | 92 | 90 | 0.1 | 97% | 15.6 s |
| fr2_desk | freiburg2_xyz | 121 | 90 | 1.0 | 98% | 4.1 s |
| fr3_office | freiburg3_long_office_household | 79 | 177 | 0.1 | 82% | 12.3 s |

The saved fixes were dense enough (about one per second per recording), so no more were run.
Metres ratio per room (shown / true): 1.54, 1.25, 1.31. From fix to fix it barely moves (5th to
95th percentile: 1.52 to 1.61, 1.24 to 1.26, 1.31 to 1.32), so one known distance per room fixes it.

## The limits, and why

| Limit | Value | Why |
| --- | --- | --- |
| Enter (`ENTER_M`) | 1.5 m | A choice, not a measurement: "near" as arm's reach plus a step. The truth moves with it, so the replay cannot say which radius is right. |
| Leave (`LEAVE_M`) | 2.0 m | Measured. With no slack 10% to 12% of found visits were split at 1 s; with 0.25 m, 6% to 9%; with 0.5 m, none. With 1.0 m the logged time ran 15% to 21% over the true time. |
| Fixes beyond the leave limit that end a visit (`OUT_FIXES`) | 2 | Measured. With today's metres at one fix every 6 s, ending on the first fix split 7 visits (6%); needing two split none. With a known distance it made no difference. |
| No found fix for this long closes a visit (`GAP_S`) | 60 s | Not measurable here: the longest silent stretch in the data is 23 s. It has to be longer than that, so a visit is not closed by the lost stretches that do happen (12 to 23 s), and 60 s is ten photos in a row at the live rate. |
| An event this close to a visit counts as during it (`JOIN_S`) | 20 s | Not measured. Three photo intervals, to cover the photo before the first found fix. |
| A walk: near one place, then near another (`WALK_S`) | within 180 s | Not measured. |
| The same question again is one entry with a count (`REPEAT_S`) | within 30 min | Not measured. |

**Metres.** The limits are applied to the fix's own `distance_m`. With a known distance in the
room those are true metres and the table at the top applies. Without one, 1.5 means 1.0 to 1.2 true
metres: the log stays honest (nothing invented) but finds about a third fewer visits and half the
time. The slack still holds: 0.5 map metres is at least 0.32 true metres, and the worst error of a
found fix is 0.19 m. Until the room has a known distance, `BEACON_NEAR_M=2.0` is the measured way
to get the same result as with one.

## Confidence

Every room visit, place visit, walk and joined statement carries a `confidence_pct`. For a visit
it is the mean sureness of its fixes (the stabiliser's `confidence_pct` when the relay passes
`stable`, else 95% for a raw found fix: 453 of 453 were within 0.2 m), times three discounts: few
fixes (one fix: 15% off, halving with each further fix), a long stretch without a fix inside the
visit (up to 40% off at 60 s, nothing off under 15 s), and a closest fix right at the enter limit
(up to 15% off). A joined statement ("the pill box was opened while next to it") takes the
visit's confidence, times 0.8 if the event fell just outside the visit, times the event's own
confidence if the camera reported it. Entries under 60% are left out of what is said to the wearer
and marked "uncertain" for the carer.

Stated confidence against what was real, all six replays (known distance and today's metres, at
1, 3 and 6 s) together:

| Stated confidence | Visits | Mean stated | Were real | Share of the logged time truly near |
| --- | --- | --- | --- | --- |
| 60 to 69% | 12 | 69% | 100% | 100% |
| 70 to 79% | 80 | 75% | 100% | 99% |
| 80 to 89% | 161 | 85% | 100% | 97% |
| 90 to 99% | 258 | 93% | 100% | 95% |

| Fixes in the visit | Visits | Were real | Mean stated |
| --- | --- | --- | --- |
| 1 | 70 | 100% | 73% |
| 2 | 92 | 100% | 83% |
| 3 to 4 | 135 | 100% | 90% |
| 5 to 9 | 108 | 100% | 92% |
| 10 or more | 106 | 100% | 93% |

| Closest fix, as a share of the enter limit | Visits | Were real | Mean stated |
| --- | --- | --- | --- |
| under 0.70 | 272 | 100% | 93% |
| 0.70 to 0.85 | 70 | 100% | 87% |
| 0.85 to 1.00 | 169 | 100% | 79% |

What this says, plainly:
- **The percentages understate.** All 511 logged visits were real, including the 70 resting on a
  single fix and the 169 whose closest fix was at the edge.
- **The replay cannot tell whether a 70% visit is less reliable than a 95% one**, because there was
  no invented visit to separate them. The discounts are judgement. After the first run I halved
  the one-fix discount (30% to 15%) and cut the edge discount (25% to 15%) because the data showed
  no cost for either; that is tuning on the same data, and only three rooms.
- The only thing that does fall with the confidence is the last column: long, high-confidence
  visits include a little time just beyond the limit (95% truly near), because of the leave slack.
- **Not tested: confidence from the stabiliser.** `track.py` is not on this branch. The replay used
  raw fixes, so every fix counted as 95% sure. With the stabiliser's own percentage the numbers
  will be lower and will vary more; that needs its own check once both are merged.
- The discount for a hole inside a visit was hardly exercised: the longest hole in the data is 23 s.

## What was not tested

- A whole day, a home, or anything longer than two minutes. Gaps of a minute or more, walks,
  repeated questions, reminders, the day files, "yesterday" and the 14-day limit are tested only
  offline on made-up streams (`eval/test_day.py`, `live/test_day_flow.py`).
- More than one room. Each recording is one room, so room visits and walks between rooms were not
  measured. What stands behind them is the walking test's 0 wrong rooms accepted out of 178.
- Sensor events joined with real positions. The joins are tested on made-up events; the reading
  from the event API is tested against a stand-in.
- The live relay with the cluster, end to end. The relay hooks were tested with a stand-in gateway.
- The stabiliser's answer (`stable`) on real data: only its shape, in the offline test.
- Glasses taken off and left on a table (the log follows the glasses), and two places closer
  together than the limits (the log then says "near" both, which is true but reads oddly).
- The trail on the day page. It draws the positions of the found fixes on the map and joins two
  fixes with a line only when they are at most 20 s apart. How much the map shifts between fixes
  over a day was not measured.

## What this means for the demo

- **What it will say is safe to show.** In this data the log never claimed a visit that did not
  happen. It misses some: about one in five at the live rate of one photo every 6 s, mostly short
  ones and ones where the fix was lost.
- **Set a known distance for the demo room, or start the relay with `BEACON_NEAR_M=2.0`.**
  Otherwise the wearer has to come within about a metre of the pill box for it to count, and half
  the visits will be missing.
- **Stand at a place for at least two photos (12 s).** A visit shorter than the gap between photos
  can fall between them. Times are right to about one photo interval at each end.
- **Looking away from what the baseline saw gives "no information"**, as on the glasses display.
  On the day page that is the striped stretch and a break in the trail.
- Wording stays with what was seen: "The pill box was opened at 08:15 while you were next to it",
  never "you took your pills".
