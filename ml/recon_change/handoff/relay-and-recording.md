# Relay, recording to splat, labels, places, /navigate (hand-off, Sun 15 Nov ~03:45 local)

## What was built (all in `ml/recon_change/`)
- **Recording to splat**: `live/relay.py` POST /recording (zips of frames, or a whole video up to 4 GB, split into frames
  on the laptop) -> `live/gateway.py` keeps frames on Iridis -> room map (`live/worker.py recording_map`) ->
  `live/remote_train.py` submits `iridis/video_map.slurm` -> `video_map.py`: VGGT, untrained splat, gsplat training
  (PSNR), fly-through, `views.json`+`depths.bin` (photo playback), labels, `birdseye.jpg/json`. Results are fetched to
  `%TEMP%\beacon-evidence\train-<zone>-<session>-<run>\` and linked as the zone's `trained` (GET /room).
- **Labels** (`video_map.py label_objects`): OWLv2 proposes, CLIP ViT-L/14 confirms, sizes give the map a scale,
  sightings grouped in 3D per class. `--labels-only OUT` (re-label, ~1 min on Iridis), `--labels-place OUT` (CPU).
- **3D page** (`live/viewer.html`): trained splat, label tags, bird's-eye map, playback; with `/care/space`'s matrix
  also the wearer, the places, the way to the chosen place, and "+ Set a place" on the bird's-eye map.
- **Places without new pictures**: dashboard photo tap (POST /waypoint?live=1, photo in the body), map position
  (&east&north&up), auto places (BEACON_AUTO_PLACES, 2 agreeing fixes), POST /places (worker op "spots": places
  from the pictures as they are, no rebuild). `locate.py` takes places with a stored map position.
- **GET /navigate?zone=Z[&to=NAME]**: map (no `to`) or guidance (route.py route, turn words, arrived, stale). With
  no fix since the newest room map, the map is that room map's. While polled, /where carries `"navigate"`.
- **Resilience**: a map rebuild keeps the zone's trained splat; a zone with a trained splat does not grow from fixes
  unless `?grow=1`; on start the relay picks up trainings it was waiting on (`resume_trainings`, retries SSH errors).
- Tests (`python live/test_<name>.py`, CPU, no cluster): navigate, places, places_worker, room_keeps_training,
  resume_training. All 13 `live/test_*.py` passed at 03:30.

## Current state (03:45)
- Laptop relay pid 34948, started 03:32 with everything on disk (no pending restart). Iridis workers restarted 02:53
  with the current worker.py, locate.py, gateway.py.
- **kitchen** = the 02:59 recording `20261115_025911_0a9dfa62` (1347 frames): room map job `011254-dd435f`, trained
  `train-kitchen-20261115_025911_0a9dfa62-1` (21.97 dB, 42 labels), /care/space fit 0.38 m, 0 named places (a
  recording that replaces a zone's pictures deletes its places).
- **validate-room** = `glasses-20261115-025911`: trained 22.14 dB with labels; its room map (job `012722-94cb05`) is
  from other pictures, so /care/space shows kind=map ("cameras 0.5 m apart").
- Also on disk, not linked: `train-kitchen-aon-20261115-024529-1` (02:45, 20.27 dB) and
  `train-kitchen-glasses-20261115-001625-1` (8 s glasses venue, 24.59 dB, labels and views added by a VGGT-only pass).
- **Kitchen decision**: Seb told me "Keep 02:45 recording"; the validation session relayed "Restore kitchen now" and
  later "Keep the 02:45 one" (02:55). Then the ~1 min 02:59 recording arrived and replaced kitchen, and Seb asked for
  "the most recent map", so kitchen shows 02:59. Ask Seb before switching.

## How to switch kitchen (02:45, or the glasses venue)
1. On Iridis, back up `baselines/kitchen` to `/scratch/ase1g23/backup/`, then rebuild its pictures from the session's
   frames as `worker.recording_map` does (`pick_keyframes(frames, REC_PICTURES)`, `square_up`, <=518 px, `NN.jpg`).
2. `POST /map?zone=kitchen` (rebuilds the room map, keeps `trained`).
3. Stop the relay; in `%TEMP%\beacon-evidence\rooms.json` set kitchen's `trained` to the chosen folder (splat, session,
   psnr, keyframes, gaussians, median_depth, fov_deg, views, labels from its summary.json); in recordings.json set
   kitchen's session; back both files up first. Start the relay.

## Commands
- Relay (from `ml/recon_change`, keys from `.env.demo`): `EVENT_API_URL=http://127.0.0.1:8000/events
  EVENT_API_TOKEN=$API_TOKEN python -u live/relay.py --host 0.0.0.0 --port 8090 --token $RELAY_KEY --quick
  --train-host iridis` (or `bash run_demo.sh`); logs `%TEMP%\beacon-relay.log`, `.err.log`. Restart only when GET
  /recording shows no zone "receiving". A training that is cut off: set its recordings.json state to "running" while
  the relay is stopped, and it is fetched at start.
- Workers (VPN): `cd ml/recon_change && bash iridis/deploy_from_laptop.sh` (copies, compiles, restarts both workers).
- Re-label a finished map: `sbatch iridis/video_map.slurm FRAMES --labels-only trained/NAME/out` (from the root).
- Fetch a finished training: `python live/remote_train.py - NAME OUT --fetch`.

## Open bugs and next steps (priority order)
1. A new recording in a zone replaces its pictures and deletes its named places (kitchen lost 5). Guard: a zone
   with a trained splat should need `?replace=1`, or each recording gets its own zone.
2. validate-room: room map and training come from different pictures, so /care/space cannot line them up.
3. Kitchen has no named places: name them (dashboard tap, "Set a place", /places).
4. The change check 02:45 vs 02:59 is "UNSTABLE" (64% self-consistency): pair keyframes by view, square-crop them.
5. rooms.json/recordings.json are written in place (a reader can see half a file): write to a temp file and rename.
6. Auto places ("pill box", "front door") and live taps are tested offline only, not on real objects.
7. Labels: per-class counts are not checked against the real room (ask Seb for counts).
