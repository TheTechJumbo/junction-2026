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
