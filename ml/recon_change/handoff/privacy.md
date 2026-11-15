# Privacy with Iridis untrusted: hand-off, 15 Nov 2026

Full write-up: `ml/recon_change/PRIVACY.md` (threat model, numbers) and `PHONE_SPEC.md` (the phone version). No secrets
here. Seb's ask: no plain images leave the trusted side; garble them with a key, blur faces, and keep the splat encrypted.

## Threat model in short
- Today Iridis holds everything in plain files under `/scratch/ase1g23/junction-recon`: recording frames (full size),
  /locate photos, baselines, keyframes, `trained.splat`, depths, the camera path, labels and fly-throughs. The folder
  is mode 0700, so other users cannot read it. Admins, root on any node, backups and a later compromise can.
- Root on the GPU node reads the job's memory: the key, the frames, the depths and the splat geometry. Only
  confidential computing would stop that (not on L4s), or not using Iridis.
- The colour garbling is per pixel: shapes and edges stay visible. It hides appearance; it is not encryption.

## Built (all in `ml/recon_change`, pushed in c2e8dee; later edits are listed under "next steps")
- `live/anonymise.py` + `test_anonymise.py` (other session): blurs faces (YuNet, Haar) and people (HOG) on the CPU.
- `live/garble.py` + `test_garble.py` + `garble_reference.txt`: a keyed invertible colour code. Tone curves, then 4
  coupling layers with a keyed MLP, all applied as a 3D LUT through cv2.remap (12–20 ms a 960×720 frame).
  `decode_colours` decodes a splat's colours. `screened_key` rejects keys that leave the brightness pattern.
- `live/vault.py` + `test_vault.py`: AES-256-GCM in 1 MiB chunks. Uses `cryptography` on the laptop, and OpenSSL via
  ctypes on Iridis (nothing installed). The key reaches a job's memory only: `push_key` runs
  `ssh iridis srun --jobid=J --overlap` with the key on stdin, into `KeyInbox`, a 0600 FIFO in `/dev/shm`.
- `live/private_train.py` (laptop) + `live/private_job.py` + `iridis/private_train.slurm`: private splat training,
  with the same call as `remote_train.train`. The laptop blurs, garbles and seals; the job unseals into RAM, runs
  `video_map.py` and seals its results; the laptop decodes. The job never holds the garbling key.
  `--resume JOB` reuses OUT/private.key.
- `eval/privacy_eval.py` (+ slurm): VGGT, fixes and splats, plain against garbled. `eval/split_eval.py`: DINOv2
  tokens. `eval/venue_anon.py` (+ slurm): OWLv2-based blur for the venue splat.

## Measured
- Hall (120 frames): garbled = plain. Cameras within 0.3°, depth within 4%, fixes 6/6, decoded splat 18.58 dB against
  18.63 plain (held-out).
- Venue glasses session (50 frames): median pairwise rotation 6.3° off plain, depth 7%, fixes found 5 against 7
  (of 12), splat −1.6 dB held-out and −4.4 dB on trained views (2.7 dB of that is decoding). "strong" mixing breaks
  VGGT (45°).
- The 2-minute videos. aon: 0.9° and depth 7.4%, fixes 11 = 11, splat −0.7 dB. 2k: 5.2° and depth 6.2%, fixes
  0 found in plain or garbled, splat −0.8 dB held-out. "tone only" breaks the 2k video (24°).
- VGGT's focal estimate is fragile under garbling: 1.6–2× off on aon, 40–50% on the hall, 3% on the venue.
- End to end (`privtest-hall`, job 2226733): works. The key reached RAM through srun in 3 s, scratch holds only `.jv`,
  and the laptop decoded in 34 s. The decoded render is 18–22 dB, with false-colour speckle in flat areas.
- Keys vary: most leave the brightness pattern (median |corr| 0.95), so keys are screened (< 0.8, about 2 s).
- Split computing: after 4 DINOv2 blocks the tokens invert to a near-photo, faces visible (23 dB). The full backbone
  is about 1 TFLOP a frame (41–225 s on the busy laptop CPU), and tokens are 2.8 MB a frame. Rejected.
- Laptop trusted step (venue frames → 768 px): 377 ms on one thread (anonymise 176, garble 16, JPEG 62), 156 ms with 4.
- Phone transport: send WebP lossless (47.7 dB round trip). Android's JPEG (4:2:0) gives 27.8 dB.

## Wired into the relay: nothing yet
- Not wired: `relay.py` / `worker.py` / `gateway.py` are unchanged. The proposal (`BEACON_PRIVATE=1`): keep recordings
  on the laptop and call `private_train.train` instead of `remote_train.train`. For live fixes, the relay garbles and
  seals each zip after the anonymise hook points; the worker opens a KeyInbox and unseals in memory. Also: ungarble
  `/frame` for the dashboard.
- If the relay moves to Iridis, garbling must happen before it: on the phone, or in a thin laptop proxy.

## Seb's open decisions
1. Trusted side: the phone app (PHONE_SPEC.md; needs a face/person detector download Seb must approve) or the thin
   laptop step (built; the laptop then sees plain frames).
2. Publishing the anonymised venue splat (1,022,521 Gaussians, 32.7 MB, 619 person and 321 head boxes blurred, no
   faces left by OWLv2). It is staged outside the repo in the session notes folder `venue-publish/` (splat, README,
   LFS `.gitattributes`). Do not commit it without Seb's explicit OK.

## Iridis
- Code is deployed in `junction-recon` (`live/garble.py`, `vault.py`, `private_job.py`, `eval/*`, `iridis/*.slurm`).
  The YuNet model is in `privacy-models/`.
- Outputs: `privacy-out/{hall,kitchen-*}/summary.json` (evals), `trained/kitchen-glasses-20261115-001625-anon/`
  (venue splat and anonymised frames), `private/privtest-hall/` (sealed test). The jobs are all finished.

## Next steps
1. Run `python live/test_garble.py`, `test_vault.py` and `test_private_train.py` (also on Iridis: `test_vault.py`).
2. Fix `remote_train.to_webm` on the laptop (OpenCV 4.9 cannot write VP8 WebM: empty file).
3. After Seb chooses, wire `BEACON_PRIVATE=1` into `relay.py` (through the integrator), test on a test zone, never
   kitchen.
4. Live fixes: KeyInbox in `worker.py`, `push_key` from the relay, and sealed queue files.
