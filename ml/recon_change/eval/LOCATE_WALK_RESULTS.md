# Position fix on a person walking through a real room (TUM RGB-D)

Status: **plan only, no numbers yet.** This file is filled in as the runs finish.

## Why

`locate.py` was measured on PASLCD only (`LOCATE_RESULTS.md`, `LOCATE_LOOKALIKE_RESULTS.md`):
tripod-like photos, no motion blur, and no real metres. The glasses will be on a person who walks.
This test uses a handheld camera with motion blur and with true metres.

## Data

TUM RGB-D benchmark (Sturm et al., IROS 2012, CC BY 4.0, plain downloads, `fetch_tum.py`).
A person carries a Kinect through a room: 640x480 colour at 30 pictures a second, a depth picture
in real metres for each, and the camera's true pose from a motion-capture system.

## Plan

1. Rooms: the freiburg1 office (`fr1/room` as the baseline recording, `fr1/desk`, `fr1/desk2`,
   `fr1/360`, `fr1/xyz` as other recordings of the same office), the freiburg2 desk scene
   (`fr2/desk`, `fr2/xyz`), and `fr3/long_office_household`.
2. Baseline per room: 24 frames picked like `recon_change.sample_frames` does for a video
   (the sharpest of each stretch); a fix uses 12 of them, as `live/worker.py` `where()` does.
3. Same-room queries: frames of another recording of the room where the two recordings share one
   ground-truth frame (to be checked first), else frames of the same recording kept away from the
   baseline frames in time.
4. Wrong-room queries: frames of the other rooms.
5. Places: about 5 pixels per room in baseline frames, with valid Kinect depth, so their true
   position is known.
6. Measured: found / unsure / lost, wrong rooms accepted, position error in metres and heading
   error in degrees after fitting the baseline cameras to the truth, the error of `distance_m`
   and `bearing_deg` as the display shows them, the ratio between the code's metres and true
   metres, all of it split by blur and by distance from the nearest baseline camera, 12 against
   24 baseline frames, 1 against 3 query frames, seconds per fix.
