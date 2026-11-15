# Venue splat (anonymised)

`venue-kitchen-glasses-anon.splat` is the Junction 2026 hacking hall as a 3D Gaussian splat. It was made from the
Ray-Ban Meta glasses recording `glasses-20261115-001625` (zone "kitchen", 14 Nov 2026), after every person in it had
been blurred. It is the common 32-bytes-per-Gaussian `.splat` format. Open it with
`ml/recon_change/live/viewer.html?src=...` or any `.splat` viewer.

Published with Seb's approval on 15 Nov 2026. Only the splat is published. No photos, video, depth maps, keyframes or camera path are included.

| | |
|---|---|
| Frames | 50 (1376 × 1840, one every 0.5 s), all used as keyframes, middle square at 518 px |
| Anonymised before training | 619 person boxes found by OWLv2 ("a person", whole frame and 2 × 2 tiles, score ≥ 0.15), each pixelated to about 8 blocks along its longer side. 321 head and face boxes (OWLv2 "a human head" / "a human face", plus YuNet and Haar), each grown by 35% and filled with one flat colour |
| Faces left afterwards | 0 by OWLv2 "a human face" (score ≥ 0.3). YuNet/Haar flagged 3 patches in 3 frames; looked at by eye, they are flat-filled or pixelated areas, not faces |
| Splat | 1,022,521 Gaussians, 32.7 MB, sha256 `13f637d4…5ff1d` |
| Training | VGGT-1B cameras and depth (18 s), gsplat 7000 steps (105 s), one NVIDIA L4 on Iridis |
| Fit to its (anonymised) keyframes | 25.6 dB PSNR, median |

Limits. People are still there as coarse blocks of colour, and some far-away people (a few pixels in a 518 px frame)
may only be pixelated where OWLv2 saw them. Clothes colours and screens are not anonymised. Made with
`ml/recon_change/eval/venue_anon.py` and `video_map.py`; see `ml/recon_change/PRIVACY.md`.
