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
