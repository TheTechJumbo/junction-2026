# Vision stage: orchestrator state and handoff

Last updated: Sat 14 Nov 2026, about 00:50 (Espoo time). Kept current by Seb's orchestrating
agent. If that agent runs out of credits, whoever picks this up starts here.

## The goal Seb set

Work until there is a working demo. The reconstruction model (a vision transformer, VGGT) and the
Gaussian splatting should both run reasonably fast on a CPU. There should be a UI where the team
can see it working. Keep splitting the work across agents and land it as pull requests.

The project brief is Linear JUN-19. The vision stage is JUN-22. It only adds evidence events; the
fast sensor path must work without it.

## What is live right now

- **Phone page and relay** on Seb's laptop (`python live/relay.py --host 0.0.0.0`). The link
  carries an access key that only Seb has. The phone must be on the same Wi-Fi as the laptop.
- **GPU worker** on Iridis (`sbatch iridis/worker.slurm`, capped at 2 hours per start).
- A check takes about 15 s: baseline once per place, then a new clip, then a headline, events,
  and before/now pictures.
- Only Seb's laptop can reach Iridis: it needs his SSH key and the university VPN, and the VPN
  has dropped twice tonight. Another person's agent cannot run this part.

## Pull requests

| PR | Branch | What | State |
|---|---|---|---|
| #2 | `jun-22-vision-stage` | Detector, live relay, phone page | open |
| to come | `jun-22-eval-benchmark` | Benchmark with ground-truth changes (agent A) | running |
| to come | `jun-22-max-quality` | Best and most expensive detector (agent B) | running |
| to come | `jun-22-cpu-speed` | How fast VGGT runs on a CPU (agent C) | running |
| to come | `jun-22-cpu-splat-ui` | CPU splat export, 3D viewer, dashboard, CPU worker | in progress |

All are stacked on `jun-22-vision-stage`. Agent B edits `recon_change.py`; expect to resolve
conflicts with the CPU worker changes in the same file.

## What is measured

- GPU (NVIDIA L4): 36 frames reconstruct in 5 to 7 s using 9.9 GB; change detection takes about
  7 s on 4 CPU cores; model load 13 to 17 s.
- Photo sets with interleaved before/after views: no false changes on three scenes; painted-in
  and virtual objects are found down to about 5% of the viewing distance.
- Video where the second clip looks from new viewpoints: false changes on four of five unchanged
  clips with a clean control. Cause: the two clips line up worse than frames inside one clip.
  With cross-clip tolerances and a 15-pixel search window, three of the four are fixed. A portrait
  crop of the Colosseum clip still fails.
- Not tested: a real object moved between two real clips, indoor video, glasses footage, a real
  phone recording through the phone page.
- Seb's laptop (4 cores, 16 GB, 2.4 GB free) cannot hold the 5 GB model, so CPU runs happen on
  Iridis CPU nodes (`amd_student` partition, 64-core nodes).

## Next steps, in order

1. CPU splat export: turn a reconstruction into Gaussians with no training (one flat disc per
   pixel, changes coloured), write a `.splat` file, view it in the browser.
2. Dashboard on the relay: history of checks, before/now pictures, the 3D view, timings per
   step, which device ran it, and the open pull requests.
3. CPU worker: make `load_model` and `reconstruct` device-aware, add a second queue so a check can
   be sent to a CPU worker or the GPU worker, and show both timings.
4. Review and merge the agents' pull requests; take agent C's fastest passing CPU setting and
   agent B's detector as defaults if their numbers hold.
5. Reduce size against agent A's benchmark: fewer frames, lower resolution, cheaper detection.
6. Get one real before/after clip pair from a phone or the glasses and run it.

## If you take over as orchestrator

- You can: review and merge the pull requests, coordinate on Linear, build the dashboard and
  viewer (they only need the relay's HTTP API and a `.splat` file), run CPU experiments on your
  own machine, and write the demo script.
- You cannot: start the relay or the Iridis worker. Ask Seb.
- Ground rules that held up: every agent gets its own clone and its own folder on Iridis; only
  one agent edits `recon_change.py` at a time; no data, weights or keys in git; check an agent's
  numbers yourself before repeating them.

## Update, Sat 14 Nov about 01:25 UTC

Focus from Seb: dementia only. New work: locate the wearer in the room, and directions on the glasses display.

Branch `jun-22-hud-waypoints` (on top of `jun-22-cpu-splat-ui`):
- `locate.py`: wearer position, heading, and distance and bearing to named places, from the same model pass as the change check.
- `live/worker.py`, `gateway.py`, `relay.py`: `POST /locate`, `GET /where`, `GET /waypoints`, `POST /waypoint`, `GET /baseline/<zone>/<n>.jpg`, pages `/hud` and `/places`.
- `live/hud.html`: the glasses display (arrow, distance, mini map). `/hud?k=KEY&demo=1` is a sample room that needs no relay.
- `live/places.html`: name places by clicking a baseline picture, and test a position fix.

Tested live on the L4: a photo from the same room is found (match 0.96) in 5 to 6 s; a photo of another place comes back as lost (match 0.0); bad uploads no longer stop the worker. The display page, places page, dashboard and 3D viewer were checked in a browser.

Not tested: a real room (the only baseline is the VGGT kitchen example, a table top), the glasses, the phone compass between fixes, and locate on the CPU worker (it still runs the old code; restart it with `sbatch iridis/worker_cpu.slurm`).

Rough edges: distances assume a typical viewing distance of 2 m; the viewer legend overlaps in a narrow frame; the dashboard has no link to `/places` yet.

Agent results, reported but not yet checked by the orchestrator:
- PR 4 (`jun-22-eval-benchmark`): PASLCD benchmark with real changes. Precision 0.92, recall 0.24; large objects found 67 percent of the time, small ones 5 percent; 10 of 10 unchanged pairs clean.
- PR 5 (`jun-22-max-quality`): a look-alike check that removes the remaining false alarms on video. More frames did not help.
- CPU speed agent: still running at the time of writing (branch `jun-22-cpu-speed`, Iridis folder `cpu/`).

Next: review PR 4 and 5 and merge them into the stack, take a CPU setting from the CPU agent, record a real room with the glasses or a phone, name three places, rehearse the demo. GPU worker job 2217918 ends about 03:15 UTC; restart it with `sbatch iridis/worker.slurm`.

## Update, Sat 14 Nov about 06:00 UTC

Seb's Claude is back (credits reset at 03:50 UTC) and is orchestrating the vision stage again.

Branch `jun-22-hud-waypoints` now holds everything: PR 2, 3, 4 (benchmark), 5 (look-alike check),
8 (locate and the glasses display) and 10 (CPU benchmark). It is the branch to deploy and try.

Live on the cluster with that branch, tested end to end through the relay:

| | GPU worker (L4, 24+12 frames) | CPU worker (32 EPYC cores, 8+4 frames) |
| --- | --- | --- |
| Unchanged clip | No change found, 19 s | No change found, 47 s |
| Clip with a cube painted in | 1 thing appeared, 14.5 s | 1 thing appeared, 45 s |
| Photo from the same room | found (0.94), 5 s | found (1.0), 37 s |
| Photo of another place | lost (0.0), 5 s | lost (0.0), 35 s |

Both workers have a 2 hour cap. Started 05:39 UTC as jobs 2218938 (GPU) and 2218939 (CPU).
Restart with `sbatch iridis/worker.slurm` and `sbatch iridis/worker_cpu.slurm`.

Agents running now, each with a draft PR to open within 20 minutes:
- `jun-25-locate-eval`: how reliable the position fix is on real captures (found rate, wrong-room
  rejection, position error, fewer baseline frames).
- `jun-22-recall`: make the change check find more small and flat changes, measured on PASLCD.

Still not done: a real room recorded with a phone or the glasses, places named in it, and a
rehearsal of the demo. Nothing has run on the glasses.

## Update, Sat 14 Nov about 07:45 UTC

Merged into `jun-22-hud-waypoints` and deployed: the position-fix benchmark (PR 12) and the
appearance check for the position fix (PR 14). A matched spot must now also look alike, not only
sit at the same depth.

Benchmark (PASLCD, 10 real scenes, scored from saved reconstructions):
- Wrong room: 540 of 540 lost at 24, 12 and 8 baseline photos. Before, one was found at 0.97.
- Same room: 267 of 270 found, 1 lost (at 8 photos), position within 2 percent, heading within 1.6 degrees.
- 12 baseline photos are now as safe as 24 on this data, so the GPU worker uses 12 for a fix.

Live, through the relay, with that code:

| | GPU worker (L4) | CPU worker (32 EPYC cores) |
| --- | --- | --- |
| Unchanged clip | No change found, 16.5 s | No change found, 56 s |
| Clip with a cube painted in | 1 thing appeared, 15 s | 1 thing appeared, 46 s |
| Photo from the same room | found (1.0), 3.1 s | found (1.0), 40 s |
| Photo of another place | lost (0.0), 3.3 s | lost (0.0), 39 s |

Workers restarted 07:30 UTC as jobs 2219254 (GPU) and 2219255 (CPU); 2 hour cap.

Draft PR 11 (`jun-22-recall`, a looks-different list for the change check) is not merged: the
gain is modest (regions found 37 to 47 percent), its limit was tuned on the benchmark it is
scored on, and it adds about 2 s per check.

Still not done: a real room recorded with a phone or the glasses, places named in it, a rehearsal.
Not tested anywhere: two rooms in one home that look alike, blur, night lighting, people in view.

## Update, Sat 14 Nov about 09:50 UTC

Seb chose the demo moment: **directions and pills**, then added **reminders** and **lost keys**.

Built and tested on branch `jun-26-care-flow` (relay and pages only, nothing new on the cluster):

- The glasses display (`/hud`) can ask a question. The relay passes it to Bartek's event API
  (`POST /ask`), shows and speaks the answer, and points the arrow at the pill box when no
  opening is recorded.
- Reminders: set on `/places`, shown and spoken on `/hud` when due, with the way to the place.
  A pill box reminder ends when the event API has a sensor `pillbox_open` event after it started.
- Lost items: the relay remembers where each position fix saw an item and answers "Where are my
  keys?". The item finder itself (an open-vocabulary detector in the worker) is being built by an
  agent on branch `jun-26-lost-items`; until it is deployed the answer is "I cannot look for
  your keys yet."

Tested end to end on this laptop against Bartek's branch `codex/bartek-foundation` (PR 13) run
locally, with the live GPU worker: pills answer, medical refusal, place answers, a reminder ended
by a real `POST /events` sensor event ("The pill box was opened at 11:47."). Also in a browser.

Two things found on the way, both reported to Bartek on Linear:
- `/where` was 109 kB and his adapter reads at most 64 kB, so every place answer fell back.
  Fixed on this side: it is 19 kB now.
- His adapter makes a new HTTP client per question, which takes about 280 ms of its 400 ms
  limit on this Windows laptop, so place answers time out inside his server. Until that
  changes, the relay answers place questions from its own fix when his answer is the fallback.

Running now on the laptop: the relay on 8090 (connected to a local copy of Bartek's API on
8000 with an empty store and a throwaway token), GPU job 2219443 and CPU job 2219444 until
about 11:28 UTC.

Not done: the item finder is not deployed, no ElevenLabs voice is set up (the browser's voice
speaks), no real sensor has sent an event, and nothing has run on the glasses or in a real room.

## Update, Sat 14 Nov about 10:55 UTC: the state to integrate against

Branch `jun-22-hud-waypoints` holds the whole vision stage in a working state and is what runs
live. It is 39 commits ahead of `main`, touches only `ml/recon_change/`, and merges into `main`
without conflicts. One pull request to `main` carries it; the earlier stacked pull requests
(2, 3, 4, 5, 8, 10) are all contained in it.

What it does now, all tested live through the relay on the cluster GPU at 10:53 UTC:

| Step | Result | Time |
| --- | --- | --- |
| Change check, unchanged clip | "No change found" | 15.3 s |
| Change check, clip with a cube painted in | "1 thing appeared" | 15.0 s |
| Position fix, photo from the room | found, 1.0 | 3.9 s (of which the item finder 0.8 s) |
| Position fix, photo of another place | lost, 0.0 | 3.8 s |
| "Did I take my pills?" (local copy of Bartek's API) | his answer, shown and spoken | under 0.1 s |
| "Where is the pill box?" | "about 1.9 metres ahead" | 0.4 s |
| A phone painted into a photo, then "Where is my phone?" | "last seen at 12:48 near the pill box, about 1.3 metres ahead" | answer at once |
| Two later looks at that spot without the phone | "... but I did not see it there at 12:49", arrow withdrawn | |

Offline tests, all passing: `test_synthetic.py`, `eval/test_items.py`,
`eval/test_locate_lookalike.py`, `eval/test_masks.py`, `live/test_care_flow.py`.

How to run it:

    # cluster, in /scratch/$USER/junction-recon (the item finder's packages and weights are in items/)
    RECON_ITEMS_ENV=items sbatch iridis/worker.slurm        # GPU lane
    RECON_ITEMS_ENV=items sbatch iridis/worker_cpu.slurm    # CPU lane
    # laptop, in ml/recon_change (EVENT_API_* are optional: without them only pills answers are missing)
    EVENT_API_URL=http://127.0.0.1:8000/events EVENT_API_TOKEN=<token> python live/relay.py --host 0.0.0.0

The item finder (OWLv2, `eval/ITEMS_RESULTS.md`) is deployed but weak: at its limit of 0.5 it
finds 97 of 303 labelled items (keys 7 of 56) with 1 wrong answer in 78 and no false answer on
324 item-free photos. It is precise and misses most keys. An agent is working on that on branch
`jun-26-items-recall`.

A real, complex room is loaded as zone `lounge` (a PASLCD scene with labelled changes). The
change check there finds the moved bin and a removed disc and misses thin and flat changes.

Running now: GPU job 2220544 and CPU job 2220545 until about 12:47 UTC, with replacements queued
behind them. Agents in flight: item finder recall, the walking test of the position fix
(`jun-25-locate-walk`, draft PR 18), and watched spots for small changes (`jun-22-watch-spots`).

Still not done: a real room of ours, real items, the glasses, a real sensor event, a voice.
