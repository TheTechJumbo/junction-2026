# End-to-end validation with the glasses (Sun 15 Nov 2026, 02:31–03:45 EET)

Times are EET on the laptop's clock. Every number below was measured, except where a line says "from code".
Seb ended the live tests at 03:18 ("no more validation use the runs we have of the room"). So checks 2–4 were done
by replaying his own 90 s glasses video (02:59, recorded in the venue) through the running relay, in the test zone
`validate-room`, exactly as the app's Live camera sends photos. Kitchen was not used for any test.

## Results

| # | Check | Result | Numbers |
|---|-------|--------|---------|
| 1a | Glasses app: wearer screen, setup, backend, glasses STARTED, a question answered | **PARTIAL** | Wearer screen, setup cards and "Backend connected" shown. Glasses RB Meta 0076 reached STARTED at 02:45:19, 0.1 s after STARTING. The Pills? answer was not seen: the tap went in at 03:14:21 and the phone locked at 03:15 |
| 1b | Family app shows the same timeline | **NOT RUN** | The CI APK (run 43650443344) was refused (signature mismatch with the build another session installed at 02:36). The phone then locked, and Seb stopped the tests |
| 2 | Change detection in a test zone, with before/now pictures | **PASS** (on recorded frames) | 3 of 3 detected, with correct before/now crops. 4.9 s, 7.7 s and 10.3 s from the change to `/changes`. An unchanged view: 103 ms (168 answers, model not asked) |
| 3 | Day log and event API timeline with correct timestamps | **FAIL** | Changes reach neither the day log nor the event API (0 events). Fixes are logged 4.86–7.36 s after capture (median 5.16 s). Only 7 of 117 answers were logged |
| 4 | Position and path on the bird's-eye view, with time per point | **FAIL** | Tracking follows the walk (Kendall tau +0.85), with an instant fix every 0.33 s (median) and 0.36 s from capture to answer. But the wearer is not drawn on validate-room's bird's-eye map (fit 0.52 m > 0.5 m), every full fix moves him 2–4 m, and the /dayview path is squeezed into about 1 m |
| 5a | Box opening seen by vision in under ~2 s | **FAIL** (by measurement of the same path; not done physically) | The fastest possible notice is one full fix: 4.9–10.3 s measured, plus up to 4 s waiting from code |
| 5b | Box opening from the sensor (simulated pillbox_open) | **NOT RUN** | A pillbox_open makes "Did I take my pills?" answer yes for the rest of today's demo, and Seb had stopped the tests. Event API reads on loopback: 4–6 ms median |

## What Seb did physically

- 02:45: put the glasses on; the app connected them (STARTED 02:45:19).
- 02:59: recorded a 90 s video with the glasses in the venue (Meta AI imported it as `20261115_025911_0a9dfa62.mp4`,
  1376 x 1840, 30 fps; its frames show a drinks table and stage lighting, and its views change through the room).
- About 03:04 ("new video sent now"): the same video reached zone **kitchen** as session `20261115_025911_0a9dfa62`
  (named after the file, as the app's video send does; the app's zone field said kitchen), while I sent the same file from the
  laptop to `validate-room` (session `glasses-20261115-025911`). Both finished at 03:12:52 / 03:12:56. The two
  uploads ran at the same time and took 7.4 min together (1347 frames each).
- 03:18: ended the live tests. The family app, the Pills? answer and the physical box opening were not done.

## Setup as found, and clocks

- Event API on 127.0.0.1:8000 (up since 01:34). The relay on :8090 was restarted by other sessions at 02:51, 03:14:26 and
  03:32:39. Workers on Iridis: GPU on ecsai04 (L4), CPU on ruby047.
- `adb reverse` was **empty** at 02:31, which cut the phone off from the event API (it listens on 127.0.0.1 only).
  I re-added `tcp:8000` and `tcp:8090` at 02:34, as `phone_setup.py` does.
- **Phone vs laptop clock:** the phone is +24 to +183 ms ahead (bounds from 15 adb round trips). Iridis logs are in UTC
  (01:19 there = 03:19 here); all relay and day-log times are laptop time. Clock offsets are not a source of error
  at the second level. The real offset is the processing delay below.

## Check 2: change detection (replay)

How: the frame at 55 s of the video (the drinks table) was sent back to back for 10 s, as a still head would. Then
the same frame with two patches swapped (18 % x 22 % of the picture: an object moved) was sent for 14 s, and back.
3 changes in all, at 03:27:17, 03:27:41 and 03:28:11.

- The cheap gate called each one `local`, with 14.6 % of the view changed and box [0.125, 0.5, 0.875, 0.833] (both
  moved patches). The model was asked, and `/changes?zone=validate-room` listed each one with its before/now crops
  (`/evidence/012717-bb8fb0/moved_before.jpg` and `moved_now.jpg`; the crops were checked by eye).
- **Change to `/changes`: 4.92 s, 7.67 s and 10.34 s.** All of it is the model's turn on the GPU, which got slower with
  load. The cheap gate itself answered an unchanged view in a median 103 ms.
- In all three trials, the first changed photo came more than 4 s after the last full fix. A change inside that window
  waits up to 4 s more (from code, `FULL_EVERY_S`).
- Not tested: a real person's head is never perfectly still. The gate calls a change `local` only when at most 35 % of
  the view's blocks differ, so a turned head reads as "moved" and is not reported as a change.

## Check 3: logging and timestamps

- **Changes are not logged.** No event reached the event API during the test (the watcher polled
  `/events` every 0.5 s: 0 vision events). Nothing appeared in `/day` either. A cheap-gate change lives only in the
  relay's in-memory `/changes` list: the last 20 per zone, gone after a restart (the 03:14 and 03:32 restarts lost them).
- **Day log for the 90 s walk:** "The wearer was in the validate room from 03:24 to 03:26" (92 % confidence, 7 fixes),
  which is correct to the minute. Only full fixes that said "found" are logged: 7 of 10 full fixes, and none of the
  92 instant fixes. Each is stamped with the time the model answered: **capture + 5.16 s median (4.86–7.36 s)**.
- The same walk also logged "glasses", "phone" and "wallet" as seen in the validate room at 03:25 (the item finder
  on venue footage; whether those items were really there is not verified).
- The event API timeline for today has 0 events: no sensor or vision event arrived during the test.

## Check 4: position, path and bird's-eye view (replay)

How: the whole 90 s walk was sent like the app's Live camera (one `/locate` in flight, newest frame, long side
896 px). That's 117 requests between 03:24:43 and 03:26:18.

| Kind of answer | n | Round trip median (p90, max) | Capture to answer, median |
|---|---|---|---|
| Instant fix (relay `--quick`) | 92 | 0.29 s (0.40, 0.53) | 0.36 s |
| Full fix (model on Iridis) | 10 | 4.94 s (5.45, 12.71 cold) | 5.05 s |
| Skipped (blurred or dark) | 15 | 0.04 s | - |

- **The tracking follows the walk.** The room picture each instant fix matched rises with the video's time (Kendall tau
  **+0.85**, 48 matches), and 115 of 117 answers carried a position. Caveat: the map was built from the same video,
  so this is optimistic about matching; the latencies are real.
- **Position update gaps:** median 0.33 s, p90 0.69 s, but **9 gaps over 2 s, up to 5.74 s**. With one request in
  flight, each full fix (every 9.0 s median) blocks the next photo for its ~5 s.
- **Full fixes and instant fixes disagree.** Full fixes span 1.15 x 1.05 m. Instant fixes, at the room map's
  pictures, span 7.07 x 6.30 m. A full fix lands **2.1–4.0 m** (median 3.0 m) from the instant fix just before
  it. So every ~9 s the marker jumps toward the start point and back.
- **`/viewer?live=validate-room` bird's-eye:** the splat trained on Iridis (job 2226699, attached at 03:42 after a
  relay restart; 22.14 dB, 96 keyframes, 1.75 M Gaussians) has a bird's-eye picture. But `/care/space` refuses to line
  the room map up with it: "cameras 0.52 m apart on average", over the 0.5 m limit, 45 camera pairs, scale 4.54. So the
  wearer is not drawn on it. The map shows only the current position anyway; the 3D view keeps 40 points of trail,
  without times.
- **Time per point:** `/dayview`'s map from above has a time slider, with one point per logged full fix
  (`/day` → `path`: [time, east, north, zone]). Times are the model's answer times (+5.2 s, see check 3). Positions
  are the squeezed full-fix ones, so the 7 m walk shows as about 1 m.

## Check 5: box opening

- **5a vision (not done physically):** each full fix checks the watched spots it can see (`result["watch"]`), but the
  answer goes only to `/locate` and `/where`, not to the day log or the event API (from code). The earliest a box
  opening could be noticed is one full fix after the photo: **4.9–10.3 s measured**, plus up to 4 s waiting (from code).
  Under 2 s is not possible with the relay as it runs; it would need the cheap gate's verdict pushed as an event.
- **5b sensor:** not run (see table). The path itself is plain HTTP into the event API (loopback reads 4–6 ms median,
  20 calls). The app's Pills? tile asks `/ask`, which answers from those events.

## Bugs and risks (for the parent to forward)

1. **One tap records over the demo venue.** "Start recording" (Always-on) and the glasses-video auto-send write to
   the zone in the app's zone field, which says kitchen, with no confirmation. At 02:45 a stray adb swipe of mine
   started a kitchen recording. At about 03:04 the auto-send put the 02:59 video into kitchen too. Each time, the
   relay's `recording_map` replaced kitchen's pictures and dropped its trained splat.
2. **A relay restart cuts a recording in the middle.** At 03:14:26 validate-room's map failed (`BadZipFile`), and the
   resumed training gave up on an SSH reset (`kex_exchange_identification ... Connection reset`). The Iridis job ran
   on regardless, and the relay only attached its splat after a second restart, with the state set back by hand.
3. **Vision changes and watched spots are not events.** Neither reaches the day log, the event API or the family app.
   They are lost on relay restart.
4. **Full fixes disagree with the room map's scale** by a factor of ~6 (1.1 m vs 7 m). Instant fixes and the map agree.
   The day log and the /dayview path use only full fixes.
5. **Updates stall up to 5.7 s** while a full fix is in flight (app: one request at a time).
6. **Logged times are 5 s late** (the model's answer time, not capture). The app sends no capture time with a photo.
7. `/care/space`'s 0.5 m fit limit rejected validate-room by 0.02 m, so the bird's-eye map can't show the wearer there.
8. `adb reverse` is lost on a USB reconnect, which silently cuts the phone off from the event API.

## What this validation changed (state left behind)

- validate-room: its room map, trained splat (`train-validate-room-glasses-20261115-025911-1`), 3 entries in
  `/changes`, `where`/`track` state, and 7 fixes plus 4 item sightings in **today's day log** (they show on /dayview
  as "validate room" lines).
- Kitchen: the accidental recording `aon-20261115-024529` (02:45, my error). Seb chose to keep it ("Keep the 02:45
  one") rather than restore the 00:16 venue. At 03:12:52 the 02:59 video, sent to kitchen as described above, replaced it
  (kitchen's room is now `20261115_025911_0a9dfa62`; its training was running at 03:33).
- Phone: `adb reverse tcp:8000/8090` restored. The glasses app's settings were not changed.
- Nothing was posted to the event API.

## Demo script (what works live, in order)

1. Laptop: `bash ml/recon_change/run_demo.sh`. Phone on USB, then run
   `adb reverse tcp:8000 tcp:8000` and `adb reverse tcp:8090 tcp:8090`. Check with `adb reverse --list` after any
   replug.
2. Glasses app: unfold and wear the glasses. The setup card says "Glasses: connected" within seconds. **Check the
   zone field before tapping Start recording or sending a video:** it writes over that zone's room.
3. Wearer screen: tap **Pills?**. The answer comes from the event API's events, and says "no record" until a pill-box
   event exists.
4. Change in view: open `/dashboard?k=…` on the laptop. Look at a table and hold your head still for 5 s, then move
   one object and keep still. The change appears in the dashboard's change list with before/now pictures **5–10 s
   later**. Say it's evidence on the dashboard, not an alert.
5. Where the wearer is: open `/viewer?live=kitchen&k=…` and walk slowly. The marker follows at about 3 updates/s,
   with a jump every ~9 s (a full fix). The bird's-eye map shows the wearer only if `/care/space?zone=kitchen`
   reports `kind: trained`.
6. The day: open `/dayview?k=…`. It has the timeline and the path from above with a time slider (times about 5 s
   after the moment).
7. Pill box: open the real UNO Q box, or run `unoq/sensor-sim` (`pillbox`; needs a crate download to build). Then
   tap Pills? again; it answers that the box was opened. **That answer stays for the rest of the day.**

## Raw data

The scratch files (not in the repo) are in the session scratchpad:
- `walk.jsonl` and `change.jsonl`: every replay request with its capture, sent and answer times.
- `run2/watch.log`: the watcher's view of the relay and the event API.
- `day.json`: the `/day` reply after the replay.
