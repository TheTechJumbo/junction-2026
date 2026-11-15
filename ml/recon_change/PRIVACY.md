# Privacy when Iridis is not trusted (JUN-26)

Seb's requirement: no plain images leave the trusted side. The data must be garbled so it is not easily undone,
faces must be blurred, and the learnt model (the splat) must come back encrypted too, as if Iridis were a server in a
country without strict laws. This file covers what Iridis sees, what each measure protects, what was measured, and
what is still exposed.

## 1. What Iridis sees today

All of it travels over SSH (encrypted in transit). At rest it sits in plain files under
`/scratch/ase1g23/junction-recon`. That folder is mode 0700, so other cluster users cannot read it. The cluster's
admins, anyone with root on a login or GPU node, the storage and backup systems, and anyone who later gets the account
can read all of it.

| What | Where on Iridis | What it shows |
|---|---|---|
| Recording frames (`POST /recording`) | `recordings/<zone>/<session>/frames/*.jpg`, full size (glasses: 1376×1840) | Every person in view. The venue session has 9–22 people a frame (OWLv2). Plus screens, papers, the room |
| Video uploads (baseline, check, recordings as video) | `queue/<job>/`, `trained/<name>/video.*` | The same, at video frame rate |
| Position-fix photos (`/locate`) | `queue/<job>/` (518 px, plus `big.jpg` at 1280 px), `baselines/<zone>/*.jpg` | What the wearer looks at, and when |
| Training results | `trained/<name>/out/`: `keyframes/*.jpg`, `trained.splat`, `views.json`, `depths.bin`, `cameras.npz`, `labels.json`, `flythrough.mp4`, `birdseye.jpg` | A photo-real 3D copy of the room, where the wearer walked, which objects are where |
| Enrolled item photos | `enrolled/<item>/*.jpg` | Close-ups of the wearer's things |
| File names and logs | everywhere | Times (`NNNNNN_<ms>.jpg`), zone and item names, the worker's log |

The live position fix also gets its answer (position, heading, distances) computed on Iridis.

## 2. The measures, and what each protects

| Measure | Runs on | Protects against | Does not protect against |
|---|---|---|---|
| **Anonymise**: blur faces and people (`live/anonymise.py`: YuNet, Haar, HOG) | trusted side, before upload | Everyone downstream recognising bystanders and the wearer, including root and us | Missed people. HOG finds people standing upright and fully in view; at the venue it found 4 person boxes in 50 frames where OWLv2 found about 600. Seated, partly hidden or turned-away people are missed, and so are faces under about 20 px |
| **Garble**: keyed invertible colour code (`live/garble.py`) | trusted side, after anonymise | Looking at a frame or the splat and seeing the room as it looks; true colours, skin tones, colour-coded labels | **Shapes and edges stay**: layout, objects and large text can still be seen in false colours. One known picture (known plaintext) gives enough colour pairs to fit an inverse. Root can see the geometry (depths, camera path, splat positions), which is not garbled |
| **Seal**: AES-256-GCM files at rest (`live/vault.py`) | trusted side seals inputs; the job seals results | Scratch files read by other users, admins browsing, backups and snapshots, a later account compromise, the storage operator | Root on the GPU node **while a job runs**: the key and every opened frame are in that job's memory |
| **Key handling** | trusted side → job memory | The key ever landing on scratch, in a Slurm script, in the environment (Slurm stores both) or in argv (`ps` shows argv to everyone) | Root on the login node (in Slurm's I/O forwarding) or on the GPU node (process memory) |
| **Results encrypted** | the job seals; the trusted side decodes | The trained splat and renders lying around in a usable form | As above, while the job runs |

How the key is handled, concretely. The trusted side makes a session master key K and keeps it (a hex file next to
the results on the laptop). Iridis only ever gets `vault key = HMAC(K, "derive|vault")`. The garbling key
`HMAC(K, "derive|garble")` never leaves the trusted side, so even root, holding the vault key, sees garbled frames.
The vault key is pushed by `ssh iridis srun --jobid=J --overlap ... 'cat > fifo'` with the key on stdin, into a FIFO
in `/dev/shm` (RAM) that the job opened with mode 0600. Tested: srun forwards stdin into a running job, and the FIFO
holds nothing on disk. Sealed files carry a key id, never the key. Every file has its own derived key, so nonces never
repeat. A changed, reordered, cut or swapped chunk is refused (`test_vault.py`, which passes on the laptop and on
Iridis).

### Honest limits

* **Root on the GPU node reads the job's memory**: the vault key, the garbled frames, VGGT's depths and cameras, and
  the splat's geometry. Encryption at rest does nothing against the machine that does the computing. Only
  confidential computing would: an H100 in CC mode inside an AMD SEV-SNP or Intel TDX VM. L4s do not have it, and
  Iridis does not offer it. The other way out is not to send the data: the laptop's RTX 2060 already runs position
  fixes locally (local mode: 6.3 s a fix, 3.8 GB).
* **A per-pixel transform keeps shapes and edges.** It is obfuscation of appearance, not encryption. A garbled frame
  is far from the photo (median 8 dB PSNR over random keys), and no 3×3 colour matrix brings it back (25–30 dB even
  when fitted with the true photo known). But a person can still see what is where; for many keys even the
  brightness pattern survives (section 4, which is why keys are screened). With one known picture under the same
  key, a cubic fit gets about 36 dB. Use a fresh key per session; never reuse one across rooms.
* **Anonymise misses people**, above all seated ones (section 4). A missed face is garbled but not blurred.
* What is not covered: file names and times, the worker's logs, item names and the questions asked, and
  `labels.json` (object names and 3D positions, in plain text inside the sealed file, readable in job memory).

## 3. Split computing: send tokens, not pixels?

The stronger-sounding option is to run VGGT's first stage on the trusted side and send its patch tokens. That stage
is DINOv2 ViT-L/14: 24 blocks, 0.3 B parameters. Measured with `eval/split_eval.py`:

| | |
|---|---|
| Laptop CPU (i5-10300H, 4 threads, float32, while other sessions were using it), one 518×518 frame | patch embedding 44 ms; +4 blocks 5.7 s; +12 blocks 15 s; all 24 blocks 41 s. About 1 TFLOP a frame: 5–10 s on an idle CPU, too slow for a 2 fps stream. A phone is no better without a converted NPU model (about 300 MB) |
| Size sent | 1369 × 1024 float16 = 2.8 MB a frame, against 62 kB for the JPEG (45× more over SSH) |
| Inverting the tokens (attacker with the public VGGT weights) | after 0 blocks: exact (59 dB), since the patch embedding is linear. After 4 blocks: 23 dB from 400 Adam steps (85 s on the RTX 2060). After 12 and 24: see section 5 |

Verdict: tokens from a few blocks are about as revealing as pixels. Tokens from the full backbone cost 5–40 s a frame
on the laptop CPU. A keyed rotation of the tokens does not help either: Iridis would need weights rotated the same
way, and with the public weights the rotation can be solved for (orthogonal Procrustes). gsplat needs photometric
images anyway. So split computing is not worth it here. For the splat the garbled-colour route is the realistic one:
the splat's colours live in the keyed space, and only the trusted side decodes them (`decode_colours`). That is how
"the output is encrypted".

## 4. Measurements (on Seb's existing recordings; nothing new recorded)

`eval/privacy_eval.py` on Iridis (`iridis/privacy_eval.slurm`, one L4). The same keyframes are fed to VGGT plain and
garbled (sent as JPEG q95 4:4:4), and the plain pass is the reference. `tone` is the tone curves, channel order and
reversals only. `garbled` is the default (4 keyed coupling layers). `strong` mixes more.

### The colour code itself (laptop, `test_garble.py`, 8 real keyframes)

| | |
|---|---|
| Garble a 960×720 frame (cv2.remap of a 65³ LUT) | 12 ms on one thread |
| LUT against the exact transform | 51 dB |
| Garble then ungarble through 8 bits | 47–50 dB |
| Through JPEG q95 4:4:4 / WebP lossless / JPEG q95 4:2:0 | 35 / 48 / 28 dB |
| Garbled frame against the photo | 6–8 dB (not viewable as the photo) |
| Best 3×3 colour matrix fitted with the photo known | 25–30 dB; a cubic polynomial fit 33–36 dB |

**Keys differ a lot.** Over 40 random keys on real keyframes, the grey garbled picture's |correlation| with the grey
photo had a median of 0.95: for most keys, a garbled frame shown in grey looks much like the photo or its negative.
The best key was 0.3, and 8 fixed keys gave 0.18 to 0.86 on test rooms. So the trusted side screens keys:
`garble.screened_key` draws session keys until one leaves less than 0.8 of the brightness pattern on 4 of the
session's own frames. That takes a few keys and about 2 s; a 0.7 limit took 25 keys and 18 s. The key used for the
VGGT measurements below (sha256 of "privacy-eval") is a middling one: 0.86 for `garbled`. Hiding brightness further
is what breaks VGGT: `strong` gets 0.27 and VGGT fails (45° median error). For a per-pixel code, privacy and VGGT
accuracy trade against each other.

How much each variant hides (medians over 8 keyframes, key 00..1f, a lucky key):

| Variant | Against the photo | Its grey picture against the photo's (\|correlation\|) | Best colour matrix / cubic, photo known | Through JPEG 4:4:4 |
|---|---|---|---|---|
| tone (curves, reversals, channel order) | 6.1 dB | 0.99: a negative-like picture, brightness order kept | 33.2 / 42.7 dB | 41.1 dB |
| **garbled** (default) | 7.0 dB | **0.64**: brightness re-mixed across channels | 29.6 / 36.3 dB | 35.1 dB |
| mild (no reversals, channels in order) | 21.3 dB | 0.99: the grey picture is nearly the photo's | 31.4 / 39.1 dB | 37.3 dB |

### VGGT, fixes and splats, plain against garbled

| Recording | Variant | Rotation error, pairs (median / p90) | Pairs within 5° | Depth AbsRel | Fixes found / unsure / lost (plain) | Splat PSNR held-out (trained views), decoded |
|---|---|---|---|---|---|---|
| hall-rec/test1 (120 frames, 18 keyframes) | tone | 0.26° / 0.53° | 100% | 6.2% | 6/0/0 (6/0/0) | |
| | **garbled** | 0.31° / 0.60° | 100% | 4.0% | 6/0/0, 0.0 m and 0° from plain | **18.58 dB** (18.36) against plain 18.63 (18.16) |
| | strong | 0.24° / 0.51° | 100% | 5.1% | 6/0/0 | |
| kitchen glasses-20261115-001625 (venue, 50 frames) | tone | 5.8° / 14.6° | 46% | 5.9% | 5/3/4 (7/1/4), 0.79 m, 2° | |
| | **garbled** | 6.3° / 18.6° | 45% | 7.2% | 5/2/5 (7/1/4), 0.45 m, 1° | **13.26 dB** (20.55) against plain 14.86 (24.99) |
| | strong | 45° / 126° | 20% | 11.9% | 10/0/2, 0.65 m, 9° | |
| kitchen 20261115_004109_2k (2 min video) | | *pending* | | | | |
| kitchen aon-20261115-010701 (2 min video) | | *pending* | | | | |

What this says so far:
* On the hall, garbling changes nothing measurable: cameras agree to 0.3° and the decoded splat is within 0.05 dB.
* On the crowded venue session, garbling costs some agreement: half the camera pairs are more than 5° off the plain
  pass, depth stays within 7%, and fixes drop from 7 to 5 found out of 12. The decoded splat loses 1.6 dB on held-out
  views and 4.4 dB on the views it trained on. Of that 4.4 dB, 2.7 dB is decoding (garbled colours blend
  non-linearly, so decoding each Gaussian's colour is not the same as decoding the picture) and 1.7 dB is training on
  garbled frames.
* `tone` (no coupling layers) costs VGGT about as much as `garbled`: the channel reversals and the reorder do most of
  the damage, and the couplings add little. `strong` breaks VGGT (45° median). Do not use it.
* VGGT's focal length came out 40–50% different on the hall (garbled against plain) but only 2–3% on the venue.

## 5. Where the trusted side runs (Seb to choose)

| | Phone app (Kotlin, before upload) | Thin laptop step (relay, implemented) |
|---|---|---|
| What leaves the phone | Garbled, blurred frames only | Plain frames, over USB (adb reverse) or the LAN to the laptop |
| Per frame | Estimated 60–150 ms on a Pixel: no JPEG decode, garble by LUT on CPU or GPU, face detector on NPU (PHONE_SPEC.md) | Measured 377 ms on one thread, 156 ms with 4 threads (venue frames at 768 px: read 109, anonymise 176, garble 16, JPEG 62). Fits 2 fps |
| New dependencies | A face and person detector in the app (ML Kit about 2–3 MB, or MediaPipe about 0.2 + 4.4 MB): a download Seb approves | None |
| Work | The app session implements PHONE_SPEC.md; the relay gets a key handout endpoint | Done for training (`private_train.py`). Live fixes still need the worker changes in section 6 |
| Seb's "nothing on the laptop" | Yes, apart from key handout and decoding results for the carer's page | No: the laptop sees plain frames, and runs the blur and the garble |

## 6. What is built, and what is not wired in yet

Built and tested (new files only; `relay.py`, `gateway.py`, `worker.py`, `video_map.py` and `remote_train.py` are
unchanged):
* `live/garble.py` + `live/test_garble.py`: the colour code; `live/garble_reference.txt` holds values for other
  implementations.
* `live/vault.py` + `live/test_vault.py`: sealing (`cryptography` on the laptop, OpenSSL via ctypes on Iridis, which
  needs nothing installed), the key inbox and `push_key`.
* `live/private_train.py` (laptop) + `live/private_job.py` + `iridis/private_train.slurm` (Iridis): the whole private
  training path, with the same call as `remote_train.train`. Result: see section 7.
* `eval/privacy_eval.py`, `eval/split_eval.py`, `eval/venue_anon.py` (+ slurm files): the measurements above, and the
  venue splat.

Not wired in (needs the parent session, since others edit these files):
* `relay.py`: in private mode, keep recordings on the laptop instead of `recordings/` on Iridis, and call
  `private_train.train` instead of `remote_train.train`. A one-line switch, e.g. `BEACON_PRIVATE=1`. Then the
  `/recording` chunks must stop going to the gateway in plain.
* Live fixes (`/locate`, `/map`): the relay garbles and seals each zip. The worker opens a `KeyInbox` at start, and
  the relay pushes the session's vault key with `push_key(job id of jun-worker)`. The worker opens sealed queue files
  in memory and seals what it writes back (`changes.jpg`, room splats). The gateway passes sealed blobs through
  unchanged. The worker's DINOv2 "instant fix" features and the item finder (OWLv2 on garbled frames, not measured)
  are the parts most likely to lose accuracy.
* `GET /frame`, the dashboard's camera view: ungarble on the relay before showing it.

## 7. End-to-end test (test name `privtest-hall`, never a real zone)

*pending*

## 8. The venue splat for the public repo (waiting for Seb's OK)

The hacking hall from the glasses session `glasses-20261115-001625` (50 frames), retrained from anonymised frames
with `eval/venue_anon.py` + `video_map.py` (`iridis/venue_anon.slurm`). This ran on Iridis, where those frames
already were.

* `anonymise.py` alone is not enough for a crowded hall. HOG found 4 person boxes in 50 frames, one of them a wall,
  and seated people were missed. So OWLv2, already in the workers' model cache (nothing downloaded), looked for "a
  person", "a human head" and "a human face" over the whole frame and 2×2 tiles, at score 0.15. It found 619 person
  boxes and 321 head and face boxes. Each person box was pixelated on its own to about 8 blocks. An earlier run merged
  overlapping boxes, which turned the crowd into one big blurred rectangle. Each head and face box was grown by 35% and
  filled with one flat colour.
* Faces left in the anonymised frames: 0 by OWLv2 "a human face" (score 0.3 or more). YuNet and Haar flagged 3 patches;
  by eye they are flat or pixelated areas.
* Splat: 1,022,521 Gaussians, 32.7 MB, 25.6 dB on its (anonymised) keyframes, trained in 105 s. The renders show
  people as coarse blocks.
* Staged outside the repo, so nothing commits it by accident: the shared notes folder `venue-publish/` (the splat, a
  short README with these numbers, `.gitattributes` for Git LFS). No photos, video, depths or keyframes.

