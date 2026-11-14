# 3D change test

Can we rebuild a room in 3D from plain video (no depth sensor, no tracking) and spot what
changed? `recon_change.py` takes a "before" and an "after" clip of the same room, runs all
frames through [VGGT](https://github.com/facebookresearch/vggt) in one pass, and reports what
appeared and what is gone.

## Record two clips

- The same room twice, 20 to 40 seconds each, from a phone or the glasses.
- Pan slowly and keep things in view from several angles. Fast head turns blur.
- Between the clips, change one thing: move, add or remove an object at least fist-sized.
- Nobody walking through the shot.

## Run it on Iridis

One-off setup (login node; builds the environment and downloads the 5 GB model to scratch):

```bash
bash iridis/setup_env.sh
```

Then, from the folder that holds `recon_change.py`:

```bash
sbatch iridis/recon_change.slurm --before before.mp4 --after after.mp4 --out out
```

A job takes about 40 seconds on an L4 GPU. The verdict is at the end of `slurm-jun-recon-<id>.out`.

## Read the result

- `out/changes.jpg`: before frames with what is gone in red, after frames with what appeared in green.
- `out/scene.ply`: the 3D point cloud, with the changes coloured.
- `out/report.json`: the numbers.

The before clip is split in two. One half is the reference and the other half is a control:
nothing changed between them, so anything found there is a false alarm.

`report.json` also has `lookalike_check`. When the two clips line up worse with each other than
frames of one clip do, a tall object (a tree, a lamp) is rebuilt in a slightly different place
and looks like "gone here, appeared there". The check drops a flagged surface when the other
clip has a surface that looks the same within `reach` of it (a fraction of the viewing
distance). "Looks the same" uses the DINOv2 patch features VGGT computes anyway, so it costs no
extra model and no extra GPU time. `reach` grows with the measured misalignment and is zero
for well-aligned clips, where the check is off and results are unchanged. `--no-lookalike`
switches it off.

| Verdict | Meaning |
| --- | --- |
| PASS | Changes found, and the control is clean |
| NO CHANGE FOUND | Nothing differs between the clips |
| UNCLEAR | Changes found, but the control has false alarms of similar size |
| UNSTABLE | The reconstruction disagrees with itself: move slower, add light, overlap more |

## Live mode: Iridis through a relay

Iridis GPU nodes cannot be reached from outside, so live checks go through three small parts:

```
phone or server --HTTP--> relay.py (laptop with the SSH key) --one SSH connection-->
gateway.py (login node) --files on scratch--> worker.py (GPU job, model kept loaded)
```

Start the worker on Iridis. It holds one L4 until it stops, and is capped at 2 hours:

```bash
sbatch iridis/worker.slurm
```

Start the relay on the laptop. `--host 0.0.0.0` lets a phone on the same network reach it:

```bash
python live/relay.py --host 0.0.0.0
```

It prints the link to open. The link carries an access key, and every request needs it,
because the relay spends GPU time on your Iridis account.

**On a phone:** open the link. The page (`live/phone.html`) has two steps: record how a place
should look, then record it again and check. It shows a headline ("1 thing appeared"), a
"now" picture with what appeared in green, a baseline picture with what is gone in red, and
the timings.

**From a script:** set a zone's baseline once, then check new clips against it (`KEY` is the
access key from the printed link):

```bash
curl -X POST --data-binary @before.mp4 "http://127.0.0.1:8090/baseline?zone=kitchen&k=KEY"
```

```bash
curl -X POST --data-binary @after.mp4 "http://127.0.0.1:8090/check?zone=kitchen&k=KEY"
```

`/check` answers in about 15 seconds with a headline, a list of vision events
(`object_appeared` / `object_gone`, with `zone`, `confidence`, rough `size_cm` and an
`evidence_url`), picture links and timings. `GET /status` says whether the worker is running.
Set `EVENT_API_URL` before starting the relay to have events POSTed to the event API.

**Dashboard:** `http://127.0.0.1:8090/dashboard?k=KEY` lists every check with its before/now
pictures, a 3D view, where the time went, which worker ran it, and the repo's pull requests.

**3D view without training:** each check also writes `scene.splat`, Gaussians built straight
from the reconstruction on a CPU in about 0.05 s (`splat.py`), with what appeared in green and
what is gone as red ghosts. `live/viewer.html` shows it in the browser.

**CPU worker:** `sbatch iridis/worker_cpu.slurm` starts the same worker on CPU cores only, with
its own queue. Send a check to it with `&device=cpu`. It uses fewer frames at full size (8+4
frames, long side 518). The CPU benchmark measured that setting at 34 s per check on 32 AMD EPYC
cores, with the same answer as the GPU. Smaller frames are faster, but the depth gets too noisy
for the detector, so cut frames, not size (`RECON_FRAMES`, `RECON_LONG_SIDE`). The numbers are in
`cpu/RESULTS.md` on branch `jun-22-cpu-speed`. Live, the CPU worker took 45 to 47 s per
check and 35 to 37 s per position fix on 32 cores, with the same answers as the GPU.

**Where is the wearer, and directions:** name places once on `/places?k=KEY` by clicking a spot
in a baseline picture. Then `POST /locate?zone=kitchen` with a photo or a short clip of what the
wearer sees returns where they stand, which way they face, and the distance and bearing to each
place, in about 5 s on the GPU. `GET /where?zone=kitchen` returns the latest fix. `/hud?k=KEY`
is the glasses display that draws it: an arrow, a distance and a mini map.
`/hud?k=KEY&demo=1` is a sample room that needs no relay. A view that does not match the room
comes back as `lost`. Distances are rough: they assume things are about 2 m away.

**What the carer sets once:** three things the vision stage cannot work out by itself, set on
`/places?k=KEY` and kept by the gateway. This part only stores them: the position fix, the item
finder and the change check read the files.

- *The room's real scale.* Pick two named places and type the measured distance between them.
  Kept as `baselines/<zone>/scale.json`: `{"a": "pill box", "b": "front door", "metres": 2.4}`
  (0.1 to 50 m). `POST /scale?zone=&a=&b=&metres=`, `POST /scale?zone=&delete=1`; `GET
  /waypoints` shows it as `scale`, or `null`. Removing one of its two places removes it.
- *Show an item once.* One to five photos of the exact item (these keys, not any keys), for the
  whole home. Kept as `enrolled/<item>/00.jpg`, `01.jpg`, ... in the project folder: JPEGs with
  the long side at most 1280, the item name in lowercase letters and digits with `_` for a
  space (`remote_control`), at most 12 items. Removing a photo renumbers the rest, so there is
  never a gap. `POST /enroll?name=` with the photo as the body (JPEG, PNG or WebP, up to 20 MB),
  `GET /enrolled`, `GET /enrolled/<item>/<n>.jpg`, `POST /enroll?name=&delete=1` (with `&n=`:
  that photo only).
- *Spots to watch.* Press "Watch a spot", drag a box on a baseline picture and name it. Kept as
  `baselines/<zone>/watch.json`: `[{"name": "pill box shelf", "frame": 3, "box": [u0, v0, u1,
  v1]}]`, the box in fractions of the picture with `u0 < u1` and `v0 < v1`, at most 12 in a
  room. `POST /watch?zone=&name=&frame=&u0=&v0=&u1=&v1=`, `POST /watch?zone=&name=&delete=1`,
  `GET /watch?zone=`.
- A new baseline for a room removes its places, its scale and its watched spots: they were
  picked in the old pictures. Enrolled items stay.
- The page also lists the items the relay remembers in the room (`seen` in `/where`), each with
  a Forget button.
- The relay checks all of it and the gateway checks it again before it writes anything: names
  against a pattern, numbers finite and in range, photos by their first bytes and their size.
- `python live/test_setup_flow.py` tests this offline against the real gateway code in a
  temporary folder. `python live/dev_setup.py` serves the page with made-up rooms and no
  cluster, and prints the link (`BEACON_ROOT` or `gateway.set_root()` moves the gateway's folder).

**Questions, reminders and lost items (the demo flow):** the glasses display also asks and
reminds. Start the relay with the team's event API (the backend that holds the sensor events)
next to it:

    EVENT_API_URL=http://127.0.0.1:8000/events EVENT_API_TOKEN=<its token> python live/relay.py

- *Questions.* `POST /ask` with `{"question": "Did I take my pills?"}` is passed to the event
  API and its answer is shown and spoken on `/hud`. When no pill box opening is recorded, the
  arrow then points to the pill box. "Where is the pill box?" is answered by the event API
  from `/where`; if it cannot read the position within its 0.4 s limit, the relay answers from
  its own fix in the same words. The event API still decides what kind of question it is, so
  its refusal of medical questions stands.
- *Reminders.* Add them on `/places` (text, time of day, place). When one is due, `/hud` shows
  and speaks it and points the arrow at its place. A reminder for the pill box ends when a
  sensor reports the box was opened, which the relay reads from the event API's event list.
  That is an observation of the box, never a claim that the pills were taken. Other reminders
  end with the Done button. "Show now" on `/places` makes one due at once, for a demo. Times
  are in the home's time zone (`BEACON_TZ`, default Europe/Helsinki), not the laptop's.
- *Lost items.* "Where are my keys?" is answered by the relay: where and when a position fix
  last saw them ("last seen at 10:42 near the pill box, about 2 m to your left"), and the arrow
  points there. This needs the item finder in the worker (`items.py`: an open-vocabulary
  detector, OWLv2, looking for `keys, glasses, wallet, phone, remote control`, or the list in
  `RECON_ITEMS`). It is not in the base environment: run `bash iridis/setup_items.sh` once on
  a login node (it fills `items-env/` next to `.venv-recon` and leaves `.venv-recon` alone),
  then start the worker as usual; for another folder, `RECON_ITEMS_ENV=<folder> sbatch
  iridis/worker.slurm`.
  A worker without it still gives fixes, and the answer is "I cannot look for your keys yet."
  The finder says nothing rather than guess: no false detection on 324 photos without the
  items, and it misses most things that are small in the picture, so expect it to see keys
  from about a metre, not from across the room (`eval/ITEMS_RESULTS.md`). It adds 0.8 s to a
  fix on the GPU and 5 s on the CPU worker.
  The relay sends the wearer's newest photo at up to 1280 px (`big.jpg`) for it.
  A sighting can go stale (someone picks the keys up), so every later fix is asked to look at
  each remembered spot again. After two clear, close looks from different standpoints with no
  item there, the answer becomes "Your keys were near the pill box at 10:42, but I did not see
  them there at 11:05" and the arrow no longer points there.
  Remembered positions are comparable between fixes: across ten fixes with different query
  frames on the example scene, the same named place moved by at most 1.4 cm and a baseline
  camera by at most 1.8 cm (cameras span 4.8 m), because every fix builds its map from the same
  baseline photos.
- `/hud?k=KEY&demo=1` rehearses all of it with stand-ins and no backend: "Remind me" and "Open
  the pill box" play the reminder and the sensor.
- `python live/test_care_flow.py` tests the relay side offline, with a stand-in gateway and a
  stand-in event API.
- `/speak` is passed on too. Without a voice set up in the event API the page uses the
  browser's own voice.

- The video stays on the laptop: the relay picks the sharp frames with OpenCV and sends only
  those (about 1 MB) to Iridis.
- Baselines are kept on scratch, so they survive a worker restart.
- The worker handles one request at a time.
- Stop the worker with `touch STOP` in the folder on Iridis, or `scancel`.
- Iridis is only reachable through the university VPN. If the VPN drops, the page shows
  "Iridis unreachable" and recovers by itself when it is back.

## What has been tested

- `python test_synthetic.py` (no GPU): a moved box in a synthetic room is found as one "gone"
  and one "appeared"; an unchanged room reports nothing.
- Three unchanged real scenes (`kitchen`, `llff_fern`, `llff_flower` from the VGGT examples):
  no changes and no false alarms.
- Virtual boxes written into a real reconstruction's depth maps: found down to about 4% of the
  viewing distance.
- Live chain, end to end: a clip with a cube painted into every frame came back as one
  `object_appeared` event in about 15 s; the unchanged clip came back as no change.
- Real video (aerial clips from the VGGT examples, with the second clip looking from new
  viewpoints): the first version reported false changes on four of five unchanged clips, as
  large as a real object, while the control stayed clean. Cause: the two clips line up less well
  than frames of one clip, so a tall object lands in a slightly different place and looks moved.
  With the cross-clip tolerance and the 15-pixel search window, three of those four are clean.
  A portrait crop of the Colosseum clip still gave two or three small false clusters (a tree
  rebuilt about 10% of the viewing distance off). The look-alike check removes them: all five
  video cases and the kitchen set report nothing. Numbers, and what did not help (more frames),
  are in [MAXQ_RESULTS.md](MAXQ_RESULTS.md).
- **Not yet tested:** a real object moved between two real clips, indoor video, and footage
  from the glasses.

## Limits

- **A clean control is not proof.** The control only compares the before clip with itself, so
  it cannot see errors between the two clips. Treat a reported change as evidence to look at,
  not as fact, and record the second clip from roughly the same spots as the first.
- When the two clips line up poorly, the thresholds widen and only large changes are reported.
  `depth_noise_between_clips` in the report shows how well they lined up.
- When the look-alike check is active (poorly aligned clips), an object that moved less than
  `reach` is treated as the same object and not reported, and so is a new object standing
  within `reach` of one that looks just like it.
- It sees depth steps of about 5% of the viewing distance or more (5 cm at 1 m). Flat things
  (paper, a pill strip lying on a table) are invisible to it.
- A point only counts if at least two other frames of its own clip confirm it, so each part of
  the room must appear in three or more frames.
- Lighting changes do not matter (it uses geometry only), but moving people do.
- Sizes in cm are rough: the model has no metric scale, so they assume `--typical-distance-m`.
- `--from-recon out/recon.npz` redoes the change detection without a GPU, after a run with
  `--save-recon`.
