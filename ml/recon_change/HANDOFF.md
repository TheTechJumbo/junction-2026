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

## Update, Sat 14 Nov about 12:15 UTC

Deployed and tested live on the GPU lane at 12:09 UTC: change check 15 to 16 s, position fix 3.9 s,
a painted-in phone found and placed, and the lounge scene with 20 marked spots: 19 right, 1 wrong.

- **Start or restart the workers with** `bash iridis/restart_workers.sh` from the project folder on the
  cluster. It sources `iridis/worker_env.sh` (the item finder's settings), holds other queued GPU jobs
  for the minute it takes, and queues replacements behind the new workers. A plain `sbatch` without
  those settings still works but runs the item finder at its old, weaker settings.
- **Files the cluster needs** from `ml/recon_change/`: `recon_change.py`, `recon_rust.py` (the change
  check imports it; without the Rust build it falls back to NumPy), `locate.py`, `items.py`, `watch.py`,
  `splat.py`, `live/worker.py`, `live/gateway.py`, `iridis/*`. Forgetting `recon_rust.py` took the
  workers down for three minutes at 12:08 UTC.
- **Item finder**: a limit per item plus a second opinion from CLIP on weak boxes finds 201 of 303
  labelled items (was 97), 3 wrong answers in 140, none on 162 held-out item-free photos. Keys over
  64 px: 14 of 25. "Show it once" did not help in the one synthetic test that could be built.
- **Watched spots** (`watch.py`): 74% of small changes caught at about 9% false alarms on held-out
  scenes, against 7% for the room-wide check. Wired into every check and position fix.
- **Walking test** of the position fix: found fixes are right (3 cm, 1 degree median), no wrong room
  accepted, 16% of same-room views not found, metres a third too long.

Waiting on Seb: four session pull requests are measured and unmerged (28 position fix v2, 29 carer
setup page, 30 day log, 31 stable confidence). Still running: the object map (32).

## Update, Sat 14 Nov about 16:35 UTC: merged, NOT yet deployed

Merged into `jun-22-hud-waypoints` and pushed (all twelve offline tests pass on the combined code):

- PR 29, carer setup on `/places`: one real distance (`baselines/<zone>/scale.json`), photos of an item
  (`enrolled/<item>/NN.jpg`), boxes round spots to watch (`baselines/<zone>/watch.json`).
- PR 28, position fix v2: metres from one known distance or an assumed 1.5 m, up from the largest level
  surface, "unsure" instead of "lost" when one baseline photo confirms the view, a hint which way to turn.
- PR 31, steady position with a calibrated confidence percentage (`track.py`): the relay adds `stable`
  and `confidence_pct` to `/locate` and `/where`, and `/hud` draws from it.
- PR 30, day log (`day.py`): `/day` and `/dayview`, and day questions in `/ask`. It is fed the fix
  itself, not the steady answer: the two were measured apart, and at one photo every 6 s the steady
  answer is rarely "steady", so visits would go missing.
- PR 32, object map (`objects.py`): merged as code with its results, not wired into the worker or relay.

**The cluster still runs the previous version.** The university VPN dropped during the afternoon, and
the workers' two-hour jobs have ended. When the VPN is back, from `ml/recon_change` on the laptop:

    bash iridis/deploy_from_laptop.sh

It copies the files (including `recon_rust.py`), checks that they import, and restarts both workers
with the item finder's settings. Then restart the relay. Nothing from PRs 28 to 32 has run live.

Finding to design around (PR 31): a position fix does not jump, it goes stale. At one photo every 6 s
an arrow drawn from the last fix is right 46% of the time between photos; at one photo a second, with
the steadying, 90%. Guiding a walking person needs about one photo a second or motion sensing in
between.

## HAND-OFF, Sat 14 Nov 17:55 UTC: start here

Seb is moving to a new chat. Everything is pushed; nothing lives only on a laptop folder. Clone the repo
fresh and read `ml/recon_change/DEMO.md` (how to run the demo) and this section.

**Where the code is**
- `main` has the whole vision stage up to the cheap gate (PR 42).
- `jun-22-hud-waypoints` is one commit ahead of `main` (105fe7d): local mode (`relay.py --local`,
  `run_local.sh`), `live/simulate.py`, `run_demo.sh`, `DEMO.md`, half-size VGGT weights on a small
  graphics card. Not yet in a pull request to `main`, because the local models were never run.

**What Seb asked for last, not done**
1. Install the Android app on his phone (connected by USB) and run the demo with the glasses.
   - The laptop has no `adb` and no Android SDK. `adb` (Android platform-tools, from Google) has to be
     installed first; ask Seb before downloading it. Java is JDK 25; the app wants Java 17 to build, so
     take the debug APK from the repo's Android workflow run on GitHub instead of building.
   - Then `adb install -r app-debug.apk`, `adb reverse tcp:8000 tcp:8000`, `adb reverse tcp:8090 tcp:8090`,
     and the steps in `DEMO.md`. The API token to type into the app is `API_TOKEN` in `.env.demo`,
     which `run_demo.sh` makes. Never print it in a chat or commit it.
   - The app talks to the event API only: voice questions work through the glasses; the arrow,
     reminders, lost items and the day page are on the relay's pages (`/hud`, `/places`, `/dayview`).
2. Run the models on the laptop (RTX 2060, 6 GB; 17 GB RAM). A separate environment was being
   installed (PyTorch 2.3.1 cu121, then VGGT, then 5 GB of weights into the Hugging Face cache). The
   commands are in `DEMO.md`, "Models on the laptop". Never started: whether VGGT fits in 6 GB with
   half-size weights and 5 baseline photos is unknown. First test: `bash run_local.sh <python>`, record
   a baseline, send a photo, watch `worker.log` for the peak GPU memory line.

**What is running, and will stop**
- Cluster workers with the item finder and the spot check: started 16:31 UTC, replacements queued, so
  they end about 20:30 UTC. Restart with `bash iridis/restart_workers.sh` on the cluster (VPN needed);
  deploy new code first with `bash iridis/deploy_from_laptop.sh` from `ml/recon_change`.
- The relay and a local copy of the event API were running on Seb's laptop from the old chat's
  folder. A new chat should start its own with `bash ml/recon_change/run_demo.sh` from a fresh clone
  (stop the old ones first: they hold ports 8000, 8090 and 8091).

**Unfinished pieces, each a draft pull request**
- PR 43 `jun-26-people-in-frame`: know when a person or a hand is in the frame so they are not treated
  as part of the room. Stopped part-way; check what is in the branch before continuing.
- PR 40 `jun-25-phone-side-vit`: position from a small model on the phone against a map made once.
  Stopped part-way; it had a plan and code but no numbers at 17:30 UTC.
- A replay of a real handheld walk through the whole live system, scored against ground truth, was
  proposed and never started. It would be the first test of the gates on real video.
- PR 11 `jun-22-recall`: left unmerged on purpose (tuned on the benchmark it is scored on).

**Numbers to carry over** (details in `eval/*_RESULTS.md`)
- Position fix: a "found" fix is right to 3 cm and 1 degree (median); 84% of same-room views found; no
  wrong room accepted; 4 s on the cluster GPU, 33 to 39 s on 32 CPU cores, 2 to 5 minutes on 4 cores.
- An arrow from a photo a few seconds old is right about half the time while the wearer moves.
- Item finder: 201 of 303 labelled items found, keys only reliable within about 0.9 m.
- Spot check: 74% of small changes at about 9% false alarms; live on the lounge scene 19 of 20 right.
- Cheap gate: the same view is answered in under 0.1 s without the model; never tested on real video.

**Not tested anywhere**: a real room of ours, real items, the glasses' camera, a real sensor event.

**Correction, 18:00 UTC.** The laptop set-up stopped early: only PyTorch 2.3.1 cu121 got installed in the
separate environment (with numpy 2.x, which must be replaced by `numpy<2`). OpenCV, VGGT and the 5 GB of
weights are not there. Run the remaining commands from `DEMO.md`, "Models on the laptop". The relay and the
event API on Seb's laptop have also stopped: nothing is listening on ports 8000, 8090 or 8091.

**Update, 20:30 UTC: local mode works on the laptop.** The environment is complete (`~/venv-local`: PyTorch
2.3.1 cu121, `numpy<2`, VGGT, weights in the Hugging Face cache) and `adb` is in `~/platform-tools`.
- On the RTX 2060 the first runs failed or spilled into shared RAM. Three fixes, all in `recon_change.load_model`
  and only below 10 GB: the trunk at half size but the camera and depth heads in float32, DINOv2's blocks in
  float32 (float16 overflowed to NaN on real pictures), and the depth head 2 frames at a time. A fix is now
  6 frames at 518x294, 1.5 s of inference and about 2 s in all, at 3.5 GB peak. Numbers in `DEMO.md`.
- `bash ml/recon_change/verify_demo.sh` checks every stage on a synthetic room in its own zone: 16 passed,
  0 failed, 1 skipped (the item finder, off on the 6 GB card; `RECON_ITEMS_ENV` turns it on, on the CPU).
- Real room: Seb's hall is the baseline of zone `kitchen`. The phone sends frames from the Android app's new
  live camera (branch `android-autostart`, not merged: `android/` is Ed's to review). The relay rebuilds the
  room map (`/viewer?live=kitchen`) and shows frames, fixes and the worker log on `/dashboard`.
- The glasses were returned. Found before that: the app disconnects them whenever it leaves the screen, and
  the glasses end an idle session after about 3.5 minutes without a camera or microphone stream ("Session
  ended by device"). `android-autostart` reconnects for both, untested on hardware.

## HAND-OFF, Sun 15 Nov ~01:40 UTC (03:40 local): start here

Written so someone else (or a new chat) can finish without us. Per-area details are in
`ml/recon_change/handoff/*.md` (one file per workstream). Never print or commit `.env.demo`.

**Paste this into a new chat:** "Continue the Junction 2026 vision + glasses work for Seb. Repo
github.com/TheTechJumbo/junction-2026. Check out `jun-26-recording-splat` and read the last section of
`ml/recon_change/HANDOFF.md` plus `ml/recon_change/handoff/*.md`. Rules: ask before downloads; never print or
commit keys/tokens (`.env.demo`), data, images, recordings or weights; merge with merge commits; `android/` is
Ed's: changes stay on a branch for his review; Iridis is treated as untrusted (see handoff/privacy.md)."

**Branches and PRs** (all pushed)
- PR #46 `jun-22-hud-waypoints` -> `main`: local mode on a 6 GB laptop, live room map, verify demo. Mergeable,
  verified (verify_demo 15/0/2). Merge first, with a merge commit.
- PR #47 `jun-26-recording-splat` -> `jun-22-hud-waypoints` (stacked): record a room -> Gaussian splat trained
  on Iridis, whole-video uploads, fly-throughs, labels (deduped), playback, anonymiser, caretaker page (/care),
  places (live/auto), /navigate, privacy work in progress. Retarget to `main` after #46 merges.
- `claude/glasses-dc-fix-038490` (no PR; Ed to review): the Android app. Ed's wearer screen first, our setup
  behind the gear, auto setup, glasses reconnect fixes, live camera / Record room / Record video, opt-in
  always-on recording, wearer map + "take me to", debug panel (459ed3a). See handoff/android-app.md.
- `claude/caretaker-app` (5c54032): the caretaker page's history; its files are folded into PR #47.
- `android-autostart`: superseded by `claude/glasses-dc-fix-038490`.
- `main` also gained PR #55 (wearer-guidance) from a teammate at 01:30 local; not yet merged into our branches.

**What runs where (now)**
- Laptop (Seb's, Windows): event API `scripts/run_local.py --port 8000` (from run_demo.sh) and the relay
  `python -u live/relay.py --host 0.0.0.0 --port 8090 --token <RELAY_KEY> --quick --train-host iridis`
  started from `ml/recon_change` with env `EVENT_API_URL=http://127.0.0.1:8000/events`,
  `EVENT_API_TOKEN=<API_TOKEN>`; logs `%TEMP%\beacon-relay.log` / `.err.log`. Both keys are in `.env.demo`
  (RELAY_KEY rotated 02:51 local; old copy `.env.demo.bak-before-rotate`, git-ignored).
- Iridis (`ssh iridis`, VPN): GPU worker `jun-worker` (L4) and `jun-worker-cpu`, each with a replacement queued;
  gsplat training jobs (`video-map`, `private-train`) launched by the relay; a test servers job
  `jun-servers-test` (the API + relay on a compute node, synthetic zones only). Code under
  `/scratch/ase1g23/junction-recon`, deployed with `iridis/deploy_from_laptop.sh`.
- Phone (Pixel, USB): `adb reverse tcp:8000 tcp:8000` and `tcp:8090 tcp:8090` (adb in
  `C:\Users\User\platform-tools`). Apps: glasses app `com.junction.glasses` (build f15eadd installed; 459ed3a with
  debug panel + venue_zone not installed yet), Ed's family app "Daylight" (Live 3D tab shows /care; set the
  key by touch-and-hold).
- Relay pages (add `?k=<RELAY_KEY>`): /dashboard, /care?zone=Z, /viewer?live=Z, /places, /dayview.

**Zones**
- `kitchen`: the demo venue, changed several times tonight. 02:45: a stray always-on recording
  (aon-20261115-024529) replaced its map (Seb first kept it, later asked to "restore kitchen now"). 03:12: the
  app uploaded Seb's 02:59 glasses video (session 20261115_025911_0a9dfa62, 1347 frames) to kitchen, and the
  map was rebuilt from it at 03:13; its training was still running at 03:40. Check `GET /room?zone=kitchen` and
  handoff/relay-and-recording.md for what it points at, and ask Seb which venue he wants before switching. Seb's original glasses venue is intact:
  `%TEMP%\beacon-evidence\train-kitchen-glasses-20261115-001625-1\` (24.59 dB, 1.73M Gaussians), Iridis
  backups in `/scratch/ase1g23/backup/`. Places must be named again on whichever pictures kitchen ends up with.
- `validate-room`: the validation zone (Seb's latest glasses video; training job may need fetching).
- `always-on-test`: where the app now records by default, so stray taps can't touch kitchen.

**Open items, in priority order**
1. One bundled relay restart (when no upload is running): link kitchen's chosen splat, fetch validate-room's
   training (set its recordings.json state to "running" first), load the most-recent-map fallback for
   /navigate and the caretaker's same-frames pairing (already in relay.py). Restarts cut uploads in flight.
2. Name kitchen's places again (the glasses session has the procedure); then test "take me to" end to end.
3. Install the app build with the debug panel + venue_zone (one Meta AI approval).
4. Privacy: Seb wants Iridis treated as untrusted (blur + keyed garble + encryption at rest, outputs encrypted).
   Modules exist; the trusted side (phone vs laptop) is Seb's decision. The move of the API and relay onto
   Iridis is built and tested but PAUSED until that is decided (handoff/iridis-servers.md).
5. Publish the anonymised venue splat (Git LFS, .splat only) once Seb confirms.
6. Merge #46, then #47; get Ed's review of the app branch.

**Lessons that cost time**: relay restarts kill uploads and pending training fetches; UI automation over adb
can tap buttons by accident (the 02:45 recording); every CI build has a new debug key, so installs need an
uninstall and a new Meta AI approval; glasses need Developer Mode per pair and drop idle sessions after
~3.5 min; the laptop GPU throttles at 80+ °C, so the models run on Iridis.
