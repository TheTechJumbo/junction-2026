# Caretaker view (/care) and the Daylight Live 3D tab: hand-off (15 Nov, 03:10)

## What /care shows (`live/care.html`, read per request)
- `GET /care?zone=kitchen` (key as `?k=` or `X-Key`). The room as the Gaussian splat trained on Iridis, with the
  wearer in it (head, view cone, line to the floor, today's path with times), named places (yellow tags) and
  recognised things (labels.json, OWLv2 + CLIP). W A S D / arrows move, Q E down/up, Shift faster, drag to look,
  right-drag or two fingers to slide, wheel or pinch to walk, F follow, V their view, R reset, click a tag to fly.
- Panel: Now (where, "At the X, N min so far", since when), Going to, doing (walking / looking around / standing
  still, last event), the change check of the newest photo (same view / something changed / camera moved) and
  /changes with before and now crops, what the glasses saw (/frame), Where they went today, reminders, today's
  timeline (/day), places, things in the room. Polls /where 1 s, /changes 2 s, /care/space 8 s, /day 15 s.
- Where they went: birdseye.jpg of the trained splat (or the path fitted in, north up) with the path, times,
  places and wearer. Visits: within 1.0 m of a place; a visit ends past 1.5 m or after 3 min unseen.
- Going to: reads `/where` `navigate` (also `stable.navigate`): `"pill box"` or `{"name", "active", "distance_m"}`.
  Shape agreed with the demo session's /navigate; not tested end to end.

## /care/space and care_space.py (relay `care_view`, `care_named`)
- Puts the trained splat into the room map's axes (fixes and places are in those). Room-map cameras and trained
  keyframes are paired by recording frame numbers when the room and its trained splat share a session
  (keyframe_numbers / cameras.npz keyframes), else by heading in order (gap cost 12 deg, turn searched every 10).
  Turn about up from paired headings, scale and shift from positions; refused if rms > 0.5 m (page shows room map).
  Glasses venue: 48/48 pairs, rms 0.105 m. Cached per (room job, trained splat, labels).
- `named`: each /waypoints place (frame, u, v) from the trained splat's trusted depth in the paired keyframe
  (views.json, depths.bin), so places show without a fix or map rebuild. Fresh on every call.
- Needs next to trained.splat in the relay's evidence dir: cameras.npz, views.json + depths.bin (named places),
  labels.json (things), birdseye.json/.jpg (map from above). Missing files only drop that feature.
- Test: `python live/test_care_view.py` (synthetic cameras; relay handler with a stub gateway; no cluster).

## Daylight family app (android/family, Ed's module)
- Tab bar Today | Live 3D. Live 3D is a second WebView on `https://appassets.androidplatform.net/relay/care?zone=`;
  `RelayProxy` forwards only the page's GET routes under /relay/ and adds the key as `X-Key` (never in a URL).
- Settings: first use asks for laptop address, key and room; change them by touch-and-hold on Live 3D or the icon's
  "Live 3D settings". Debug builds allow http to loopback (adb reverse tcp:8090 tcp:8090) or private LAN.
- After a key rotation the tab shows the relay's 403 JSON: enter the new key by touch-and-hold.
- Pixel: installed from CI run 43658259525, set to http://127.0.0.1:8090, room kitchen, key current at 02:55.

## Where the code is
- Branch `claude/caretaker-app` (pushed): base `claude/glasses-dc-fix-038490` + merge of `jun-26-recording-splat`;
  commits 3b47763, 38e3ae1, 9a34a35 (family app), c5255b7, 6e812f9, 5c54032 (last code change), then this note.
  CI green: android-ci 43658259525 and 43658945450, bartek-ci 43658291624.
- Main checkout (`jun-26-recording-splat`, PR #47): committed 611e211 has care.html from 3b47763 only. Uncommitted
  there at 03:10: care.html and care_space.py at 5c54032, and relay.py's care hunks (care_view, labels.json
  fallback, care_named, same-frame pairing). test_care_view.py and the family app are on the branch only.

## Open items
- Relay restart to load care_named and same-frame pairing; then check /care on the new kitchen splat
  (aon-20261115-024529) and re-name places on /places (the new baseline cleared the old 5).
- Commit the main checkout's care files and test into #47, or merge `claude/caretaker-app` (it also carries the
  family app). Merges to main need Seb's OK.
- Live change check needs photos arriving (phone or glasses camera held still, then move one thing).
- Daylight: prompt for the key on a 403 instead of showing the relay's JSON (small change in createLiveView).
