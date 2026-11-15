# A steady position from the stream of fixes, with a confidence percentage (track.py)

Measured on 14 November 2026 by replaying the saved fixes of the walking test
(`LOCATE_WALK_RESULTS.md`: TUM RGB-D, three rooms, a handheld camera, true poses from motion
capture) through `track.py` in time order. No GPU was used: `eval/track_eval.py` reads the saved
fixes and the recordings' true poses and prints every table below.

## Short answer

1. **The fixes do not jump. They go stale.** At the moment of its photo a found fix is right:
   2 cm and 0.6 degrees (median), under these stricter rules too. But the cameras in this test
   never stop moving, so a few seconds later the wearer is somewhere else. With one photo every
   6 seconds, today's display shows an arrow 94% of the time and that arrow is right 46% of that
   time ("right" = within 0.3 m and 15 degrees of the truth at that moment).
2. **The tracker makes the arrow honest.** It draws an arrow only when the answer is expected to
   still be good when the next photo is due. Then the arrow is right 81% of the time it is shown
   at one photo every 6 s, and 90% at every 3 s or every 1 s. The price: on these never-resting
   cameras the arrow is shown much less (16%, 36% and 48% of the time).
3. **The percentage means what it says.** Fitted on two rooms and tested on the third, answers
   labelled about 80% were right 87% of the time, about 60% were right 60%, about 20% were right
   23%. Overall gap: 4.0 points. Inside one unseen room it is off by 6 to 12 points.
4. **Flicker did not go down with the chosen limit.** The arrow changes to "Look around slowly"
   and back about as often as today in the room where fixes get lost, and more often in the rooms
   where today's display never changes because it always shows an arrow, right or wrong. With the
   limit switched off (arrow whenever the tracker is steady) flicker is half of today's at one
   photo every 1 s or 3 s, and the arrow is as right as today's. Both settings are in the table.
5. **A real move is followed on the very next photo.** No found fix was ever held back in the
   replay (0 of 301 after a real move). Smoothing adds no lag you could measure.
6. **A photo of another room never moved the answer** (0 of 406 mixed in). The reason is the fix
   itself: every one came back "lost". If a fix ever called such a photo "found", the tracker
   could stop it only when photos come about every second. Details below.

## Before and after

"Today" is what `hud.html` did before: draw the newest fix until the next one arrives, arrow unless
that fix is lost. "Steady" is the tracker with the limits that ship (arrow on at 40%, off below
20%, see "The limits"). Every half second of every replay is one sample, so the time between
photos counts, not only the moment of a photo.

| One photo every | Display | Arrow shown, share of time | Of that time, right (0.3 m, 15 deg) | Right at 0.2 m, 10 deg | Changes between arrow and no arrow, per minute |
| --- | --- | --- | --- | --- | --- |
| 1 s | today | 88% | 67% | 59% | 2.2 |
| 1 s | steady | 48% | 90% | 84% | 4.9 |
| 1 s | steady, limit off | 87% | 65% | | 1.1 |
| 3 s | today | 90% | 53% | 43% | 0.9 |
| 3 s | steady | 36% | 90% | 76% | 1.8 |
| 3 s | steady, limit off | 90% | 52% | | 0.4 |
| 6 s (the live rate) | today | 94% | 46% | 34% | 0.5 |
| 6 s (the live rate) | steady | 16% | 81% | 63% | 1.0 |
| 6 s (the live rate) | steady, limit off | 88% | 48% | | 0.6 |

Replayed: 6, 16 and 25 minutes of display time. They are the same 537 fixes cut into streams with
different starting moments, so the minutes are not independent. Read the numbers as "about".

Error while an arrow is shown, between photos included:

| One photo every | Display | Position error m: median / 90th percentile / worst | Heading error degrees: median / 90th / worst |
| --- | --- | --- | --- |
| 1 s | today | 0.12 / 0.86 / 1.53 | 2.8 / 25.4 / 47.9 |
| 1 s | steady | 0.07 / 0.28 / 1.08 | 1.2 / 8.6 / 27.3 |
| 3 s | today | 0.27 / 1.26 / 3.36 | 5.1 / 41.3 / 177.6 |
| 3 s | steady | 0.11 / 0.30 / 0.61 | 1.2 / 4.1 / 23.4 |
| 6 s | today | 0.36 / 2.02 / 4.54 | 4.8 / 70.3 / 164.9 |
| 6 s | steady | 0.15 / 0.40 / 1.74 | 1.6 / 7.3 / 60.7 |

Error at the moment of a photo (no ageing):

| What | How many | Position error m: median / 90th / worst | Heading error degrees: median / 90th / worst |
| --- | --- | --- | --- |
| Newest fix, found or unsure | 224 | 0.02 / 0.09 / 0.31 | 0.6 / 2.7 / 5.8 |
| Newest fix, every one, lost too | 253 | 0.03 / 0.14 / 1.15 | 0.7 / 3.4 / 172.5 |
| Steady answer (1 s replay) | 219 | 0.03 / 0.10 / 1.46 | 1.0 / 3.3 / 67.3 |
| Steady answer that was held through a lost photo (1 s / 3 s / 6 s replay) | 9 / 11 / 7 | median 0.38 / 1.37 / 1.46 | median 30 / 45 / 37 |

The last row is the cost of "one lost photo does not drop the answer": the pose that is kept is
the old one, and the camera has moved on. The percentage falls with age, but the pose itself is
stale. Smoothing barely matters here: fixes
differ from the truth by 2 cm, so there is little jitter to remove.

Per room (arrow shown / right when shown / changes per minute):

| Room | One photo every | Today | Steady |
| --- | --- | --- | --- |
| fr1_office (fast camera, fixes often lost) | 1 s | 70% / 65% / 5.0 | 18% / 89% / 5.0 |
| | 3 s | 73% / 42% / 1.5 | 17% / 69% / 1.3 |
| | 6 s | 81% / 39% / 1.1 | 19% / 67% / 0.4 |
| fr2_desk (slow camera, never lost) | 1 s | 100% / 78% / 0.0 | 75% / 95% / 2.8 |
| | 3 s | 100% / 69% / 0.0 | 60% / 94% / 2.7 |
| | 6 s | 100% / 60% / 0.0 | 21% / 86% / 1.6 |
| fr3_office (camera circles the desks without a pause) | 1 s | 84% / 39% / 3.0 | 25% / 51% / 9.9 |
| | 3 s | 87% / 17% / 2.4 | 0% / - / 0.0 |
| | 6 s | 91% / 9% / 1.1 | 0% / - / 0.0 |

In fr3 the camera moves 0.27 m every second without ever resting. At one photo every 3 s or
more, the tracker never shows an arrow there, and today's arrow there is right 9% to 17% of the
time. That is the plainest picture of what the photo rate can and cannot do.

## The confidence percentage

**What it means.** `confidence_pct` is the chance that the steady answer is right now: position
within 0.3 m and heading within 15 degrees of the truth at this moment, not at the moment of the
photo. It is made only from things known at run time: the match score of the fix the pose came
from, how many of the last five fixes agreed, whether the answer is still settling, the age in
seconds, and the age times how fast the wearer was moving and turning at the last fixes. The
model is a logistic regression with seven weights (`track.CAL`).

**The test.** Fitted on two rooms, tested on the third, for each of the three choices of test
room. The table pools the three test rooms: 6,160 moments (every half second of the 1 s, 3 s and
6 s replays at which the tracker had a pose).

| Stated confidence | Moments | Mean stated | Share right (0.3 m, 15 deg) | Share right at 0.2 m, 10 deg |
| --- | --- | --- | --- | --- |
| 0 to 10% | 2049 | 2% | 7% | 4% |
| 10 to 30% | 678 | 19% | 23% | 14% |
| 30 to 50% | 633 | 40% | 39% | 23% |
| 50 to 70% | 701 | 60% | 60% | 36% |
| 70 to 85% | 670 | 78% | 87% | 63% |
| 85 to 95% | 1089 | 91% | 95% | 89% |
| 95 to 100% | 340 | 96% | 99% | 97% |

**How far off overall: 4.0 points** (the average gap between stated and measured, weighted by how
many moments fall in each row). Mean squared error 0.114; always saying the average (47%) would
score 0.249. The percentage is fitted to the 0.3 m / 15 degree rule; against the stricter rule it
is too high in the middle rows, as the last column shows.

Per test room it is less tidy, and that is the number to trust for a room nobody has seen:

| Test room (fitted on the other two) | Moments | Mean stated | Share right | Gap, points |
| --- | --- | --- | --- | --- |
| fr1_office | 1456 | 46% | 40% | 10.4 |
| fr2_desk | 3389 | 52% | 64% | 11.5 |
| fr3_office | 1315 | 19% | 13% | 5.9 |

Too high by 6 points in the two rooms with fast cameras, too low by 12 in the room with the slow
one. The moments are neighbours in time, so these are not thousands of independent tests.

**One fix on its own** (`track.fix_confidence_pct`, also `confidence_pct` at the top of every
`/locate` and `/where` reply): the chance that the fix was right at the moment of its photo, from
its match score alone. Photos of another room are in the test and count as never right, because a
caller with one fix in hand cannot know which room the photo came from.

| Stated confidence | Fixes | Mean stated | Share right (0.3 m, 15 deg) |
| --- | --- | --- | --- |
| 0 to 10% | 97 | 7% | 18% |
| 10 to 30% | 120 | 15% | 2% |
| 30 to 50% | 5 | 37% | 80% |
| 50 to 70% | 17 | 57% | 100% |
| 70 to 85% | 7 | 74% | 100% |
| 85 to 95% | 5 | 93% | 100% |
| 95 to 100% | 464 | 100% | 100% |

Gap: 5.3 points over 715 fixes (178 of them photos of another room). Nearly every fix is at one
end or the other; the middle rows hold 34 fixes and say little.

## The limits, and why

| Limit (in `track.py`) | Value | Why |
| --- | --- | --- |
| `WALK_M_S`, `TURN_DEG_S` | 1.2 m/s, 120 deg/s | A fix farther from the answer than a person walks or turns in the time since is not followed alone. Generous on purpose: no found fix in the test was ever wrong enough to need it. |
| `NOISE_M`, `NOISE_DEG` | 0.35 m, 20 deg | "The same pose". The map itself shifts up to 0.38 m between fixes in the worst room (0.18 m median there, under 0.05 m in the other two). |
| `JITTER_M`, `JITTER_DEG` | 0.15 m, 8 deg | Differences this small are averaged, larger ones followed in full. |
| `LOST_AFTER` | 3 | One or two lost photos among good ones keep the answer; three in a row drop it. |
| `SILENCE_S` | 30 s | No usable fix for this long drops the answer too. |
| `STEADY_AFTER` | 2 | After being lost, one good fix gives a pose but no arrow yet; the second one that agrees does. |
| `SHOW_PCT`, `HIDE_PCT` | 40%, 20% | The arrow, see below. |

**The arrow.** The first rule tried was the obvious one: arrow while the percentage is at least
50. It blinks. The percentage is high right after a photo and falls within a second or two when
the wearer moves, so the arrow came and went with every photo: 9.8 changes per minute at one photo
every 6 s, 16 at every 1 s. So the arrow is decided once per photo, on `next_pct`: the confidence
expected at the moment the next photo is due. It goes on at `SHOW_PCT` and off again below
`HIDE_PCT`, so it does not flip while the number hovers.

| One photo every | On at | Arrow shown | Of that time, right | Right arrow, share of all time | Changes per minute |
| --- | --- | --- | --- | --- | --- |
| 1 s | off (arrow whenever steady) | 87% | 65% | 57% | 1.1 |
| 1 s | 20% | 51% | 87% | 44% | 6.5 |
| 1 s | **40%** | 48% | 90% | 43% | 4.9 |
| 1 s | 60% | 44% | 92% | 40% | 3.7 |
| 3 s | off | 90% | 52% | 47% | 0.4 |
| 3 s | 20% | 39% | 89% | 35% | 1.3 |
| 3 s | **40%** | 36% | 90% | 32% | 1.8 |
| 3 s | 60% | 32% | 92% | 30% | 1.7 |
| 6 s | off | 88% | 48% | 42% | 0.6 |
| 6 s | 20% | 27% | 77% | 21% | 1.5 |
| 6 s | **40%** | 16% | 81% | 13% | 1.0 |
| 6 s | 60% | 1% | 40% | 0% | 0.1 |

("Off below" is always half of "on at".) 40% was chosen because it is the highest limit that
still shows an arrow at the live rate: at 60% an arrow almost never appears at one photo every
6 s. Lower limits show more arrows that are less often right. There is no setting on this data
that shows an arrow most of the time AND has it right most of the time at one photo every 6 s:
that needs a faster photo rate or a wearer who stands still, not a better filter.

**Following real motion.** After the wearer really moved (more than 0.3 m or 15 degrees since
the photo before), the answer was on the new fix at once in 67 of 67, 109 of 109 and 125 of 125
found fixes (1 s, 3 s, 6 s). No found fix was held back anywhere. So the delay to follow motion is
the time to the next photo plus the time the fix takes (about 2.5 s on the GPU, not in this
replay), and one more photo after the answer was lost (`STEADY_AFTER`).

**Photos of another room.** Saved wrong-room fixes of the walking test were put into the stream
in place of the right photo.

| One photo every | What is mixed in | Wrong-room photos | Moved the answer | Answer dropped right after one |
| --- | --- | --- | --- | --- |
| 1 s | every 4th photo | 61 | 0 | 0 |
| 1 s | 3 in a row, every 9 photos | 84 | 0 | 26 |
| 3 s | every 4th photo | 54 | 0 | 0 |
| 3 s | 3 in a row, every 9 photos | 86 | 0 | 24 |
| 6 s | every 4th photo | 48 | 0 | 1 |
| 6 s | 3 in a row, every 9 photos | 73 | 0 | 17 |

None moved the answer, and three in a row drop it, which is right: by then nobody knows where
the wearer is. The protection is the fix's own label: all 178 wrong-room fixes came back "lost"
with a match of at most 0.06, against 0.5 needed for "found". A made-up harder case, the same
photos relabelled "found" with a match of 0.6, shows what the tracker adds on its own: at one
photo every second the answer was moved by 9 of 61; at every 3 s by 34 of 54; at every 6 s by 46
of 48. In 6 seconds a person can reach any spot in a room, so the tracker cannot tell such a fix
from a real walk. If `locate.py` ever accepts a wrong room, the tracker will not save it at the
live rate.

## What was not tested

- **Not a home, not the glasses, not a person with dementia.** Three office rooms, recordings of
  20 to 100 seconds, a Kinect carried by hand that never rests. A wearer who stands and looks will
  keep an arrow far longer than these cameras did; how much longer is not measured. The weights
  that say how fast confidence falls with age come from cameras that were always moving, so for
  someone standing still the percentage is probably too low.
- **No compass.** On a phone, `hud.html` turns the arrow with the compass between photos. The
  replay has no compass, so every heading here goes stale between photos. Live on a phone the
  heading should hold up better than these numbers; position does not.
- **The time a fix takes is not in the replay.** A fix is treated as arriving the moment its
  photo was taken. Live it arrives about 2.5 s later, so the live arrow is that much older. The
  relay ages the steady answer from the moment the photo reached it, so the percentage does count
  that time.
- **The live rate is replayed, not run.** The 1 s, 3 s and 6 s streams are picked from saved
  fixes that are 0.1 to 1 s apart (median, per room). Nothing went through the relay, the worker
  or the cluster. The relay path is covered only by the offline test.
- **`locate.py` as it is on `jun-22-hud-waypoints` today** (rough metres: 1 map metre is 0.64 to
  0.80 true metres in these rooms). When the better fix lands (real metres, a new found/unsure
  rule), the distance limits fit better as they are, but the weights were fitted on today's
  scores and should be fitted again: `python eval/track_eval.py --walk DIR --truth DIR --fit`
  prints them, in seconds, without a GPU.
- **A found fix that is wrong** never happened in this data, so the rule for it (wait for a
  second fix) is tested only on made-up fixes (`test_track.py`, and the relabelled photos above).
- **Stricter than the walking test's own numbers.** Here one fixed transform per room takes the
  map to the truth, so the map's shift between fixes counts as error. `LOCATE_WALK_RESULTS.md`
  fits the transform anew for each fix. Single-fix confidence uses the walking test's way, the
  steady answer this stricter one.

## What this means for the demo

- **Switching it on changes what the audience sees.** With the limits as shipped, the display
  says "Look around slowly" whenever the wearer has been moving, and shows the arrow after two
  photos in a row from about the same spot. Someone walking around the room with photos every 6 s
  will mostly see "Look around slowly". Someone who stops and looks gets an arrow that is right.
  That is honest, and it matches what the system can really do at this photo rate.
- **If that hides the arrow too much in rehearsal**, open the display with `&limit=0`. The arrow
  then shows whenever the tracker is steady: as often and as right as today (about half the time
  at one photo every 6 s), with half the flicker at faster rates, and the small percentage still
  tells the truth. `&stable=0` gives back the old display unchanged.
- **The photo rate matters more than any of this.** At one photo every 3 s the honest arrow is
  shown twice as often as at every 6 s (36% against 16%) and is right 90% of that time.
- **Say the percentage out loud only as "about".** It was within 4 points on average over three
  rooms, and 6 to 12 points off inside a single room it had not seen.
- **In the sample room** (`/hud?k=KEY&demo=1`, then "Noisy photos") the steadying can be seen
  without a backend: made-up photos every 2 s, some lost, some far off. The red ring on the mini
  map is what the newest photo alone says, the white dot is where the wearer really is, and the
  blue arrow is the steady answer.

## How to run it again

    python eval/test_track.py        # offline, seconds; also checks hud.html's copy of the tracker (needs Node.js)
    python live/test_care_flow.py    # the relay carries "stable"
    python eval/track_eval.py --walk DIR --truth DIR --fit

`--walk` is the walking test's output folder on the cluster
(`/scratch/ase1g23/junction-recon/walk/out`: `plan.json`, `results.jsonl`) and `--truth` the
folder with `<recording>/groundtruth.txt` (`walk/data/tum`). Both are small text files; the
tables above were made on a laptop from a copy.
