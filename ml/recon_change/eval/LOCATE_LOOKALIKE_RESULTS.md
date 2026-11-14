# Does an appearance check stop "where is the wearer?" accepting the wrong room?

Status: **in progress. No numbers yet.** The code is written; the reconstructions of all 885
benchmark passes are being saved on the GPU (Iridis job 2219128) so that the new score can be
measured, and its one limit chosen, on CPU cores.

## What changed in `locate.py`

Before: a pixel of the wearer's photo counted as matched if at least two baseline photos saw
the same depth where it lands. Appearance was never looked at, and one wrong-room photo was
"found" with match 0.97 (`LOCATE_RESULTS.md`).

Now: the baseline photo must also show something that looks alike there. "Looks alike" is the
cosine similarity between the pixel's own image patch and the best of the 3 x 3 patches around
the place it lands, using the patch features the model pass already produces (`Recon.feat`).
The limit is taken per room: a low percentile of the similarity between baseline photos of the
same surface, times a slack for other lighting. The yardstick ("how well do baseline photos
match each other") is computed with the same rule.

- `Recon.feat` missing (older saved reconstructions): scored on depth alone, exactly as before.
- New keys in the result: `looks_alike` (of the pixels that match by depth, the share that also
  look alike; `None` without features), `match_by_depth` (the old score) and `look_limit`.
- Position, heading, waypoints and the map are computed by the same lines as before.

## Plan for the numbers

1. Score the saved reconstructions with the old `locate.py` and check that the old benchmark
   numbers come back.
2. Choose the percentile, the slack and the patch reach on half of the scenes; report on the
   other half and on all scenes.
3. Tables for 24, 12 and 8 baseline photos: wrong room, same room, same room under other
   lighting, position and heading, extra time per query.
