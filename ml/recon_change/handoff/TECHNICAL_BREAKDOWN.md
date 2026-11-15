# Junction 2026 vision + glasses demo: full technical breakdown

Written for another LLM (or engineer) taking over. State as of **Sun 15 Nov 2026, ~09:25 local (UTC+2)**.
Branch `all-in-one` (draft PR #68 to `main`), HEAD `f8182d7` at the time of writing. Everything below is
about the vision and glasses side of the project. The event API / answer engine (`backend/`) belongs to
another team member and is only described where the vision side talks to it.

Secrets are never written here. Keys live in `.env.demo` at the repo root (`RELAY_KEY`, `API_TOKEN`). Read them
from that file in scripts; never print them, never commit them, never put them in URLs you log.

---

## 0. One-paragraph summary

A care aid for people with memory problems. The wearer has Ray-Ban Meta glasses paired to an Android phone.
The phone app (`com.junction.glasses`) takes frames from the glasses camera (or the phone camera as a fallback)
and posts them to a **relay** running on a Windows laptop (`ml/recon_change/live/relay.py`, port 8090). The relay
keeps one SSH link to a **GPU cluster**, where Slurm jobs run the **workers** (`live/worker.py`, behind
`live/gateway.py`): VGGT for position fixes and room maps, OWLv2 for finding items, gsplat for training a
Gaussian splat of the room. The relay works out where the wearer is in a pre-recorded room map ("zone"), routes
them to named places, remembers where watched things (e.g. "orange box") were seen ("find me X"), writes a day
log, and posts events to the team's **event API** (port 8000). A **caretaker web page** (`/care`) and a
**wearer 3D view** (`/care?view=wearer`, shown inside the phone app) draw the trained splat of the room with the
wearer, their route and found things in it. A second Android app ("Daylight", `com.junction.family`) shows the
caretaker page to family members through a key-adding proxy.

---

## 1. System diagram

```
 Ray-Ban Meta glasses ──Bluetooth (Meta Wearables Device Access Toolkit, "DAT")──┐
   (camera stream 504x896 MEDIUM @15 fps, often degrades to 360x640)            │
                                                                                 ▼
 Android phone (Pixel)  app com.junction.glasses                                  
   - wearer screen (Ed's WearerController) + Map / 3D toggle                     
   - automatic sampler: newest frame -> POST /locate?zone=kitchen (≈3/s)          
   - find me X (voice/typed) -> GET /find?zone=&what=   take me there -> /navigate
   - 3D view = WebView of relay /care?zone=kitchen&view=wearer via RelayPageProxy 
        │  USB: adb reverse tcp:8090 / tcp:8000   (fallback: Wi-Fi http://<laptop LAN IP>:8090)
        ▼
 Windows laptop
   ├─ relay  ml/recon_change/live/relay.py  :8090  (HTTP, X-Key auth)  ── logs %TEMP%\beacon-relay(.err).log
   │     finder.py (find me X), route.py (paths), track.py (steady pose), care_space.py (splat <-> map),
   │     day log, reminders, places, recording upload, live outline (colour + detect op)
   │     └── one SSH connection ("cluster" alias, needs university VPN) ──────────────┐
   └─ event API  scripts/run_local.py  127.0.0.1:8000  (backend/, answers, events)     │
                                                                                         ▼
 GPU cluster (Slurm)                                                     
   gateway.py (over SSH stdin/stdout) ──► worker.py jobs                  
     jun-worker      (GPU, NVIDIA L4)  ops: locate, map, baseline, check, spots, recording_map, detect
     jun-worker-cpu  (CPU)             background ops (room maps etc.)
     gsplat training jobs (from recordings) -> trained.splat fetched back to the laptop's evidence folder
```

---

## 2. Components in detail

### 2.1 Glasses + phone app (`android/app`, package `com.junction.glasses`)

Ownership: `android/` is Ed's directory. Our app work lives on branch `claude/glasses-dc-fix-038490` and was
merged into `all-in-one`. The installed build on the Pixel was taken from `all-in-one` `12ae0dd`
(CI run 43685465205, "Android apps" workflow), installed at 08:33.

Key source files (`android/app/src/main/java/com/junction/glasses/`):

| File | Role |
|---|---|
| `MainActivity.kt` | Everything wired together: wearer screen, setup cards behind the gear, live camera loop, automatic sampler, Map/3D toggle, find me X, debug panel. |
| `meta/MetaGlassesAdapter.kt` | Meta DAT SDK: registration, connect, camera stream. **Line ~618:** `StreamConfiguration(videoQuality = VideoQuality.MEDIUM, frameRate = 15)`. |
| `meta/GlassesCards.kt` | Setup cards for the glasses (enable access, register with Meta AI, connect). |
| `live/LiveRelay.kt` | HTTP client for the relay: `/locate`, `/find`, `/navigate`, `/where` etc. Adds `X-Key`. Records each call (never key/body) for the debug panel. |
| `live/RelayPageProxy.kt` | Native proxy for the 3D WebView: page served from `https://appassets.androidplatform.net/relay/...`, forwarded to the relay with `X-Key` (USB first, then Wi-Fi). Read-only allowlist: `/care` (`zone`, optional `view`), `/care/space` (`zone`, optional `lite=1`), `/where`, `/find` (`zone&what=`), `/navigate` (zone only, never `to=`), `/day`, `/reminders`, `/evidence/<job>/<file>.(splat|json|jpg)`. 60 s read timeout for splats. |
| `live/GlassesCamera.kt`, `live/PhoneCamera.kt`, `live/LiveSources.kt` | Frame sources. Phone camera via CameraX at up to 1280x960. |
| `live/AlwaysOnRecorder.kt`, `RecordingService.kt` | Opt-in always-on recording service (uploads `/recording` chunks). **Not used now**: the user does not want more recordings. |
| `live/RoomRecording.kt`, `live/VideoUpload.kt` | "Record room" / "Record video" → `/recording`. |
| `core/Sampling.kt` | The automatic sampler rules (when to run, which camera). |
| `core/FindMe.kt` | Parses "find me X / where is X / where did I put X / take me there"; `FindReply` parser for `/find`; the polling loop (every 3 s, up to 60 s while `looking`). |
| `core/Navigation.kt`, `core/Wearer.kt` | `/navigate` and `/where` parsing; places vs seen things (`kind: "object"`). |
| `wearer/WearerController.kt`, `WearerPanel.kt`, `WearerMap.kt`, `MicPulse.kt` | Ed's wearer UI (map, mic button that pulses while listening). |
| `AutomaticSetup.kt` | Debug-only intent extras that configure the app with no typing. |

**Automatic sampling (commit 8a4cc2b):** while the app is in front with a relay key set, a 1 s sampler keeps the
live loop running. It uses the glasses camera while a glasses session streams, else the phone camera (asks for
the permission once). It sends to `/locate?zone=<venue_zone>` (kitchen), keeps the screen on, and shows one status
line above the map ("Found you · glasses · last fix 2 s ago", or the problem: no relay key, relay not reachable,
glasses not streaming, using phone camera). It does not use the always-on service and never uploads to
`/recording`. Leaving the screen stops it, by design.

**Map / 3D toggle (b36fbc8, 9562e13, f2cb074):** a toggle above the wearer map. 3D loads
`/relay/care?zone=<venue_zone>&view=wearer` in a WebView through `RelayPageProxy`. `f2cb074` sets
`configChanges` so rotating the phone no longer recreates the activity (that used to reset to Map and reconnect
the glasses), remembers the 3D choice, and reloads the page after a renderer crash (falls back to Map only after
3 crashes in a minute).

**Find me X (61e1dcc, ff596d5):** phrases → `GET /find?zone=&what=X`; speaks the reply's `answer`; polls every
3 s while `looking` is true and `found` false, up to 60 s; "take me there" / "take me to the orange box" →
`/navigate?zone=&to=`. Seen things (`kind: "object"` in `/waypoints` and `/navigate` places) are kept apart from
named places so "where is the orange box" stays a spoken answer, not guidance.

**Debug intent extras** (debug builds only; values remembered, keys encrypted):
`backend_url`, `api_token`, `relay_url`, `relay_key`, `relay_zone`, `venue_zone`, `relay_lan_url`, `always_on`
(bool). Example (values read from `.env.demo` by a script, never typed into logs):
```
adb shell am start -n com.junction.glasses/.MainActivity \
  --es backend_url http://127.0.0.1:8000 --es api_token <API_TOKEN> \
  --es relay_url http://127.0.0.1:8090 --es relay_key <RELAY_KEY> \
  --es relay_zone kitchen --es venue_zone kitchen --es relay_lan_url http://<laptop LAN IP>:8090
```

**Glasses registration gotcha (important):** every CI build is signed with a fresh debug key. Installing over
the previous build fails with `INSTALL_FAILED_UPDATE_INCOMPATIBLE`, so it must be uninstalled first, which wipes
the app's Meta AI registration. Meta AI then still holds the old registration: logs show
`DAT:CORE:RegistrationManager: Application is already registered with Meta Wearables` and the app shows
registration `UNAVAILABLE`; the glasses never start streaming ("manual reconnect" lines, no "first video frame").
Recovery taps: gear → "3. Glasses setup and tests" → "Enable glasses access" → "Register with Meta AI" → approve
(camera + mic too) → "Connect / reconnect glasses" (Registration goes REGISTERED → STARTED). If still
UNAVAILABLE: Meta AI app → Devices → the glasses → Developer mode off, then on, tap the glasses row (reinstalls the
Developer Mode glasses app, ~10 s), then register again. **Proper fix:** sign all CI debug builds with one stable
keystore stored as a CI secret (not committed), so updates install in place and keep the registration.

Other glasses facts: the pair needs Developer Mode enabled per pairing; idle sessions drop after ~3.5 min (the
app reconnects with backoff); Meta AI app version seen: 298.0.0.28.160. Manifest metadata
`com.meta.wearable.mwdat.APPLICATION_ID/CLIENT_TOKEN` are absent (logged as errors, harmless in Developer Mode).

### 2.2 Family app "Daylight" (`android/family`, package `com.junction.family`)

Live 3D tab showing the relay's `/care` through `RelayProxy.kt` (adds the key, read-only allowlist including
`/find?zone&what=`). Also Ed's home 3D / night view work on `main`. `main` recently added a glasses picker and
watch-only sharing (`Glasses.kt`, `GlassesActivity.kt`).

### 2.3 Relay (`ml/recon_change/live/relay.py`, ~2,600 lines, Python 3.12 on Windows)

Single-process `ThreadingHTTPServer`. Auth: `X-Key` header or `?k=` (the relay key). Main also introduced a
watch-only `view_token` (`VIEW_PATHS = {/hud, /where, /reminders, /status, /waypoints}`), not yet merged into
`all-in-one` (see §11).

Start command (PowerShell, from `ml/recon_change`; keys read from `.env.demo` into `$kv`):
```
$env:EVENT_API_URL='http://127.0.0.1:8000/events'; $env:EVENT_API_TOKEN=$kv['API_TOKEN']
$env:BEACON_AUTO_PLACES='pill box, front door, orange box'; $env:BEACON_FIND_AGREE_VIEWS='1'
python -u live/relay.py --host 0.0.0.0 --port 8090 --token $kv['RELAY_KEY'] --quick --train-host cluster
```
Run it hidden with stdout → `%TEMP%\beacon-relay.log`, stderr → `%TEMP%\beacon-relay.err.log` (the access log
and `find live:` diagnostics go to stderr). Stop with `taskkill /PID <pid> /T /F` (find the PID with
`Get-NetTCPConnection -LocalPort 8090 -State Listen`). **Warning:** stdout prints a `/viewer?...&k=<key>` link at
start; do not grep/print that log carelessly (the key leaked into a transcript once this way).

Before restarting, check `GET /recording?zone=kitchen` has `final: true` (a restart cuts uploads and pending
training fetches). `care.html` and other pages are read from disk per request: page edits are live without a
restart; Python edits need one.

Flags: `--quick` (instant fixes between model fixes), `--train-host cluster` (gsplat training on the cluster),
`--local FOLDER` (no cluster: gateway + worker on this machine), `--splat-every-fix`, `--host`, `--port`, `--token`.

#### 2.3.1 HTTP API (most used endpoints)

| Method + path | What |
|---|---|
| `POST /locate?zone=Z` | body = JPEG (or short video / zip of frames). Returns a fix: `state` (found / unsure / lost), `position` [east, north, up] m in the zone's map axes, `heading_deg`, `stable` (smoothed pose with `confidence_pct`), `route` (paths around furniture to each place), `seen`, `items`, `items_candidates`, `items_looked_for`, `found` (finder events). With `--quick`: an instant fix in ms unless a model fix is due (every `FULL_EVERY_S` = 4 s); `&quick=0` / `&fresh=1` force the model. A cheap view gate reuses the last answer for an unchanged view. |
| `GET /where?zone=Z` | newest fix + `age_s`, `seen` list, `stable`, `navigate` (while guided), `route`. Polled by the pages (~1 s; 0.4 s on the pending caretaker branch). |
| `GET /frame?zone=Z` | the newest photo that reached `/locate` (live inset on the pages). |
| `GET /find?zone=Z&what=X` | **find me X** (see §4). Always 200. X joins the watch list. |
| `GET /find?zone=Z` | watch list + each thing's sighting, maybe, views, moves, last rejected box. |
| `POST /find?zone=Z&name=X` (`&forget=1`) | watch / stop watching without asking. |
| `POST /forget?zone=Z[&name=keys]` | forget remembered item spots (`seen`) in a zone (all if no name). Used at 09:1x to clear old "phone"/"glasses" points. |
| `POST /ask?zone=Z` | `{"question": ...}` → answer. "find me / where is / where did I put X" → finder; other questions → event API answer engine. |
| `GET /navigate?zone=Z[&to=NAME]` | wearer map, floor plan, places; with `to`: route around furniture, next turn in words, `distance_m`, `bearing_deg`, arrived, `stale`. Starts guidance (shown on `/where` as `navigate`) while polled (`NAV_GUIDE_S` 15 s); `&stop=1`. |
| `GET /waypoints?zone=Z` | named places (+ seen things with `kind: "object"`). |
| `POST /waypoint?...` | name a place (in a baseline picture, on a live photo, or at map coordinates). |
| `GET /care?zone=Z[&view=wearer]` | caretaker page / wearer 3D view (§2.5). |
| `GET /care/space?zone=Z[&lite=1]` | which splat to draw (trained or room map) + 4x4 matrix splat→map axes; `lite=1` adds `lite_splat` (400k-Gaussian copy for phones). |
| `POST /recording?zone=Z&session=S&seq=N&final=0|1` / `GET /recording?zone=Z` | continuous room recording upload; room map rebuilt every 60 frames; gsplat training on the cluster every 10 min and at the end. |
| `GET /room?zone=Z`, `POST /map?zone=Z`, `POST /places?zone=Z` | room map, build it, place named places from pictures. |
| `GET /status` | worker link status incl. `workers.gpu.info.ops` (must list `detect` for the live outline), device, host. |
| `GET /day`, `/dayview`, `/reminders`, `POST /reminder`, `POST /speak`, `GET /changes`, `GET /viewer?live=Z`, `/hud`, `/places`, `/dashboard` | day log, reminders, TTS, change checks, other pages. |

#### 2.3.2 Position fixes

- **Model fix ("full")**: VGGT on the worker (GPU L4), ~5–6 s per fix; OWLv2 item finding adds ~0.9 s. With
  `--quick`, one full fix every `FULL_EVERY_S` = 4 s; frames in between get instant fixes.
- **Instant fix**: cosine similarity of the frame to the room map's pictures / the last model-fixed photo
  (`quick_print`), milliseconds on the laptop, ~0.33 s cadence. Rough.
- **Steady pose** (`track.py`): smoothed `stable` pose with `confidence_pct` and `show`.
- Measured accuracy today (from the exact-position session, live at "our table"): current relay pose about
  **3.4 m off, wandering 1.4 m**. Full fixes jump 2–4 m (`VALIDATION.md`).
- **Pending improvement (branch `exact-position` @ 1b8e162, not merged):** `splat_locate.py` places each photo
  against the trained splat's own keyframes (SIFT + PnP, 0.3–0.6 s CPU on the laptop) plus a walking-speed
  filter with a 95% radius. Held-out frames: median 0.42 → **0.06 m**, p95 4.1 → 0.67 m, >1 m jumps 42 → 1.
  Live at "our table": **0.17–0.43 m, steady within 0.18 m**. Adds `source` ("full" | "instant" | "fused") and
  `uncertainty_m` to `/where` fix and stable. No worker change; merge + one relay restart; index builds ~30 s after
  start; `BEACON_SPLAT_POSE=0` turns it off. Glasses focal length settled at ×1.33 of the assumed value.
  Placement fails when the view is mostly the user's own desk (not in the recording): look at the hall.

### 2.4 Cluster side (`ml/recon_change/live/gateway.py`, `live/worker.py`, `ml/recon_change/cluster/`)

- SSH alias `cluster` (in `~/.ssh/config`); needs the university VPN (GlobalProtect). If DNS for the login node
  fails, the VPN dropped.
- `gateway.py` runs over the SSH link (stdin/stdout framing), dispatches ops to worker processes via files under
  the cluster scratch folder, and returns results + evidence files.
- Workers are Slurm jobs with a **2 h time limit**: `jun-worker` (GPU, L4) and `jun-worker-cpu`. Replacements are
  queued so a new worker takes over when one expires. Check: `ssh cluster 'bash -lc "squeue -u \$USER"'`
  (Slurm needs a login shell).
- **Deploy code + restart workers:** from `ml/recon_change`, `bash cluster/deploy_from_laptop.sh` (copies the py
  files, compiles and imports them on the cluster, then runs `cluster/restart_workers.sh`: cancels queued
  replacements, stops workers, starts new ones and queues replacements). Takes ~1–2 min; `/locate` may fail
  meanwhile. Last deploy 08:01; GPU worker 2227855 (L4) then, replacements queued — **it expires ~2 h after
  start (~10:00 local)**; verify the replacement takes over (`/status`).
- Worker ops: `locate` (VGGT fix + OWLv2 items for `look_for` names), `map` / `recording_map` (room map from
  keyframes), `baseline`, `check` (change check), `spots` (watched spots), `detect` (OWLv2 alone on one photo, for
  the live outline; candidates from a floor of 0.25; returns `items_live` with `colour` share). The gateway only
  sends `detect` to a worker whose `worker.json` lists it.
- Models: VGGT (pose/depth/map), OWLv2 (open-vocabulary detection, `items.py`), gsplat (training, `splat.py`,
  `remote_train.py`). Item finder has its own five tuned items: keys, glasses, wallet, phone, remote control
  (`WORKER_ITEMS`; only boxes the worker "accepted" count for those).
- The laptop GPU (RTX 2060 6 GB) can run everything locally (`run_local.sh`, peak 3.5 GB) but is not used: all
  model work is on the cluster by the user's choice.

### 2.5 Pages: caretaker `/care` and wearer view `/care?view=wearer` (`live/care.html`, three.js + splat renderer)

- Draws the zone's trained splat (`/care/space`, `lite=1` on phones) with the map→splat matrix, named places as
  3D labels, the wearer marker (puck, arrow, view cone, uncertainty ring, 30 s trail; "Here now · ±0.4 m" or "Last
  seen HH:MM"), today's path and visits.
- **Live inset:** newest `/frame` with outlines: watched things (FIND list, default `orange box`) in orange, other
  items blue. Sources: `/find` replies with `box2d` + `at` ≤ 4 s old, `/find` `live` detections (`liveFromFind`,
  shown even before the thing has a place), and `/where` `items`. A worker item overlapping (IoU ≥ 0.3) an orange
  box outline is skipped (it was labelled "phone").
- **Found things in 3D:** boxes from `/where` `seen` + `/find` answers with a position (orange wireframe), shown in
  the wearer view (`SHOW_FOUND_3D`), or on the caretaker page with `&found3d=1`.
- **Route:** green ribbon on the floor from `/where` `route.to[name].points` (pathfinding around furniture by
  `route.py`), else a dashed straight line. Active guidance (`/where.navigate`) wins; otherwise in the wearer view
  `foundGoal()` heads for the newest found thing: "Found: orange box, 2 metres away. Follow the green line."
- **Wearer mode** (`view=wearer`): first person at eye height by default (camera pitched ~11° down), "From above"
  / "My view" toggle, "Where am I?", "Centre on me", sticky "I'm still finding you… Look around the room slowly."
  when there is no fresh fix.
- Caretaker controls: Follow / Their view / Reset view.
- `/find` is polled per FIND name about once a second; `/where` about once a second.
- **Pending (branch `claude/caretaker-app` @ 38fdeb5, not merged, waits for exact-position):** ring from
  `uncertainty_m` (amber > 1 m), "within 30 cm" tag, view cone pitch, `stable.trail`, `/where` polled every 0.4 s,
  matched keyframe photo panel; `viewer.html` patch.

### 2.6 Event API / backend (`backend/`, `scripts/run_local.py`) — another owner

Runs on `127.0.0.1:8000` (loopback only: not reachable over Wi-Fi, only via `adb reverse`). The relay posts vision
events (`object_appeared`, `object_moved`, source `vision`, id `find-...`, confidence 0.5–0.6) with
`EVENT_API_TOKEN`, and forwards non-finder `/ask` questions. Project rule: vision adds evidence only and never
creates alerts; deterministic answers must not depend on GPU or evidence downloads.

---

## 3. Data: zones, recordings, splats

- Zone **`kitchen`** = the hackathon venue around our desk (name is historical). Recording session
  `20261115_025911_0a9dfa62` (1,347 frames). Trained splat `train-kitchen-20261115_025911_0a9dfa62-1`: PSNR
  21.97 dB, 96 keyframes, 1,743,273 Gaussians (`trained.splat`), `lite.splat` 400k Gaussians, 12.8 MB.
  Named places: **our table, snack table, IronShard booth, drinks fridge, exit door**.
- Other zones: `validate-room` (22.14 dB), `always-on-test`. Never record test runs into `kitchen`.
- Evidence (photos, splats, jobs) lives in the relay's evidence folder on the laptop and is served under
  `/evidence/<job>/<file>`. Never commit data, images or weights.
- Venue splats published via Git LFS in `ml/recon_change/venue/` (one anonymised, 11 unblurred at the user's
  request).

---

## 4. Find me X (the orange box) — algorithm and current tuning

Files: `live/finder.py` (pure Python, `Finder` class), relay functions `find_reply`, `find_seen`, `find_live`,
`with_live`, `find_search`, `find_answer`, `orange_patch`, `place_from_photo`, `box_iou`.

### 4.1 Watch list and detection paths
1. **Watch list:** `BEACON_FIND_WATCH` (default `orange box`) + anything asked via `/find?what=`, `/ask`, or
   `POST /find`. Each watched name (plus aliases in `FIND_ALIASES = {"orange box": ["orange object",
   "orange block"]}`) joins every full fix's `look_for` (capped at 8 names with `BEACON_AUTO_PLACES`).
   Aliases are mapped back to the canonical name. Reason: OWLv2 scored the real box 0.29–0.34 as "orange box"
   but 0.36–0.59 as "orange object".
2. **Full-fix sightings (have depth → a 3D position):** `find_seen` passes the fix's `items` (marked
   `accepted`) and `items_candidates` with positions to `Finder.observe`. Checks per box (`Finder.check`):
   score ≥ `LIMITS` (orange box 0.30, default 0.35), size within `SIZES_M` (orange box 0.1–0.6 m), colour share ≥
   `COLOUR_LEAST` 0.2 for names with a colour word, worker's own five only if accepted.
3. **Sightings:** `AGREE_VIEWS` views from different fixes within `AGREE_M` 1.0 m and `AGREE_S` 900 s make a
   sighting. **Demo setting `BEACON_FIND_AGREE_VIEWS=1`**: every view is a sighting at the newest view's position.
   Seen again within 1 m: the sighting's time, photo/outline AND (since f8182d7's parent) position and nearest place
   follow the newest view. A new sighting more than `MOVED_M` 1.5 m away is a **move** (event + day log line
   "The orange box moved from our table to the snack table at 09:09.").
4. **Live outline (no depth):** while someone polls `/find?what=` (window `FIND_LIVE_S` 20 s), each new photo
   gets, one at a time per zone:
   - `orange_patch(photo)`: HSV mask (H 3–18, S ≥ 140, V ≥ 90) on a 160 px copy, morphological open, largest
     component; accepted if 0.4–40% of the picture and fill ≥ 0.45; score `0.3 + 5·share` (≤ 0.6). Milliseconds.
     Shown immediately.
   - worker op `detect` (OWLv2 on the newest photo, ~1.1–1.5 s): boxes with score ≥ `BEACON_LIVE_SHOW` 0.25 and
     colour ≥ 0.2 are shown; if OWLv2 misses, the colour patch stands in. A hit is held `LIVE_HOLD_S` 4 s when the
     next photos miss (scores hover at the limit). Stored in `live_finds[zone]`; `with_live` adds `live`
     {at, box2d, score, colour, image, age_s, latency_s} to `/find` replies and, when newer than the sighting's
     photo, top-level `box2d`, `image`, `at`.
   - **Monocular placement (f8182d7):** if a shown box scores ≥ the name's limit and the newest fix is usable
     (not lost, has a position, within `LIVE_WITHIN_S` of the photo), `place_from_photo(box, fix)` estimates the
     spot: distance from the box's height in the picture assuming a `BEACON_LIVE_THING_M` 0.2 m tall thing and
     `BEACON_LIVE_VFOV` 85° vertical FOV (clamped 0.4–4 m), bearing from the box centre across a 9:16 upright
     picture, added to the fix's heading; up = fix up − 0.4 m. That view goes into `Finder.observe` (source
     `live`), so the box's spot updates every time it is seen. Rough (±50% distance) and only as good as the
     wearer's fix. Logged as `find live: placed orange box at [e, n, u] (colour|model)`.
   - Every live check logs `find live: colour <box|None> | N raw [(name, score, colour)...] kept K in T s`.
5. **Look-alike suppression (92e0dd8):** in each full fix, worker items (e.g. "phone") overlapping (IoU ≥ 0.3)
   a watched-name box or the orange colour patch are dropped from `items` before they are remembered or shown.
   The page applies the same rule to its live inset.
6. **Single-view confirmation (`confirm_live`):** with `AGREE_VIEWS` 2, a live detection from the same standpoint
   (≤ `LIVE_FROM_M`, ≤ `LIVE_TURN_DEG`, within `LIVE_WITHIN_S`) confirms a single full-fix view.

### 4.2 `/find?zone=Z&what=X` reply (always 200; this is the app's contract)
```json
{"ok": true, "zone": "kitchen", "what": "orange box", "name": "orange box",
 "found": true, "confirmed": true, "looking": false,
 "answer": "The orange box is on our table, about 1.1 metres ahead on your right. I last saw it at 07:41. Say take me there and I will guide you.",
 "place": "our table", "position": [0.34, 0.2, -0.22], "at": 1794723688.3,
 "moved_from": "snack table", "distance_m": 1.12, "bearing_deg": 24, "stale": false,
 "box2d": [u0, v0, u1, v1], "image": "/evidence/<job>/seen.jpg", "size_m": [w, h, d], "target": "orange box",
 "live": {"at": ..., "box2d": [...], "score": 0.42, "colour": 0.96, "image": ..., "age_s": 0.8, "latency_s": 1.1}}
```
Not seen yet: `found: false`, `looking: true` (the app keeps asking), `answer` "I have not seen the orange box yet.
I am looking for it now, also in the photos I took earlier…". `bearing_deg` is relative (0 ahead, + right).
`stale`: older than 60 s or a single unconfirmed view. `at` is epoch seconds (the caretaker page needs a number;
the app ignores non-string `at`).

### 4.3 Routes to found things
`routed()` adds the finder's targets (sightings + maybes) to the route targets, so `/where.route.to["orange box"]`
exists without "take me there"; `seen_list()` adds finder sightings to `/where.seen` (`finder: true`,
`kind: "object"`, `near`, `time`, `image`, `box2d`, `size_m`, distance/bearing). `/navigate?to=orange box`
routes to the sighting with a full-sentence instruction ("The orange box is on our table, about 3 metres on your
left") and "you're there" on arrival.

### 4.4 Measured
- Replay of last night's glasses photos (find-me-x session, `eval/FIND_RESULTS.md`): live outline 1.1–1.3 s from
  photo (≈1.5 s as seen by a `/find` poll; 6–7 s when queued behind a full fix); found 10.6 s, confirmed 17.5 s
  after the first photo with the carton; full-fix box score 0.32, size 0.12 m from depth, colour 0.28.
- Live today: as "orange box" 0.29–0.34 (mostly under the 0.30 limit → looked like "never detected"); with
  aliases 0.36–0.59 with colour share 0.5–0.96; the colour patch found the hand-held box every frame.
- False positives for "orange box" measured on 1,087 venue pictures (find-me-x commit 981d67a).

---

## 5. Navigation / routes
`route.py`: floor plan from the room map's splat points seen from above (40th–90th height percentile, without
floor/ceiling), occupancy grid, path around furniture, next turn in words. `/navigate` falls back to the zone's
newest room map when there is no fix since it was built. `NAV_STALE_S` 10 s, `NAV_GUIDE_S` 15 s. Auto places:
`BEACON_AUTO_PLACES` names (pill box, front door, orange box) found by the item finder become places after
`AUTO_VIEWS` 2 agreeing fixes with score ≥ `AUTO_SCORE` 0.4 (a watched thing never becomes an auto place).

---

## 6. Settings (environment variables)

| Variable | Default | Current demo value | Meaning |
|---|---|---|---|
| `EVENT_API_URL` | – | `http://127.0.0.1:8000/events` | event API for events and `/ask` |
| `EVENT_API_TOKEN` | – | from `.env.demo` | event API token |
| `BEACON_AUTO_PLACES` | `pill box, front door` | `pill box, front door, orange box` | item names that become places |
| `BEACON_FIND_WATCH` | `orange box` | default | watched from the start |
| `BEACON_FIND_AGREE_VIEWS` | `2` | **`1`** | views needed for a sighting (1 = follow every view) |
| `BEACON_LIVE_SHOW` | `0.25` | default | live outline score floor |
| `BEACON_LIVE_THING_M` | `0.2` | default | assumed thing height for live placement |
| `BEACON_LIVE_VFOV` | `85` | default | assumed vertical FOV of the upright glasses picture |
| `BEACON_TZ` | `Europe/Helsinki` | default | clock for answers |
| `BEACON_SPLAT_POSE` | (exact-position branch) | – | `0` turns splat PnP off |
| `BEACON_ROOT`, `BEACON_TRAIN_HOST` | – | – | local mode folder; training host |
| `RECON_*` | various | – | worker/model tuning (`items.py`, `worker.py`), e.g. `RECON_ITEMS_CANDIDATES` (detect floor), `RECON_ITEMS_LIMITS` |
| `RELAY_ANONYMISE`, `VAULT_*` | – | – | privacy pipeline (not in the live path) |

Constants worth knowing (`relay.py` / `finder.py`): `FULL_EVERY_S` 4.0, `FIND_LIVE_S` 20, `FIND_RECENT` 3,
`LIVE_HOLD_S` 4, `NEAR_PLACE_M` 1.5, `FRESH_FIX_S` 60, `AGREE_M` 1.0, `AGREE_S` 900, `MOVED_M` 1.5, `OLD_S` 2 h,
`STALE_S` 60, `LIMITS = {"orange box": 0.3}`, `DEFAULT_LIMIT` 0.35, `SIZES_M = {"orange box": (0.1, 0.6)}`,
`COLOUR_LEAST` 0.2, `REC_MAP_EVERY` 60 frames, `REC_TRAIN_EVERY_S` 600, `MAX_BODY` 200 MB.

---

## 7. Operating procedures (Windows laptop, Git Bash + PowerShell)

- **adb:** `C:\Users\User\platform-tools\adb.exe`. In Git Bash set `MSYS_NO_PATHCONV=1` and pass Windows-style
  paths (`C:/...`) to native exes. USB links: `adb reverse tcp:8000 tcp:8000` and `adb reverse tcp:8090 tcp:8090`.
- **"Relay not reachable" on the phone** = almost always the USB link: the phone dropped off adb (happened 08:44
  and 09:18). Check `adb devices`; replug; re-run both `adb reverse`. A small loop that waits for the device and
  re-applies the reverse ports works well. Wi-Fi fallback only works if the phone is on the same network as the
  laptop's LAN address (the event API is loopback-only, so the backend is not reachable over Wi-Fi at all).
- **Install an app build:** `gh run download <run> -n junction-glasses-debug -D <new empty dir>` (APK at
  `outputs/apk/debug/app-debug.apk`); `adb install -r`; on `INSTALL_FAILED_UPDATE_INCOMPATIBLE`: `adb uninstall
  com.junction.glasses` then install; `pm grant` CAMERA, RECORD_AUDIO, BLUETOOTH_CONNECT, BLUETOOTH_SCAN,
  POST_NOTIFICATIONS; re-send the intent extras; then re-register the glasses in Meta AI (§2.1). Only with the
  user's OK.
- **Read the screen:** `adb shell uiautomator dump /sdcard/ui.xml; adb shell cat /sdcard/ui.xml` (avoid UI
  swipes/taps: they hit buttons and once started a stray recording).
- **App logs:** `adb logcat -d --pid=$(adb shell pidof com.junction.glasses)`; useful tags: `GlassesSession`
  ("first video frame WxH", "manual reconnect"), `DAT:*` (registration).
- **Health checks:** `GET /status` (worker running, device, `ops` incl. `detect`), `GET /where?zone=kitchen`
  (`state`, `age_s`), count `POST /locate?zone=kitchen` lines in the err log, `GET /frame?zone=kitchen` to see what
  the camera sends (360×640/504×896 = glasses, 960×720 = phone camera).
- **Open pages with the key** from Python `webbrowser` (Windows `cmd start` breaks on `&`).

---

## 8. Current state (≈09:25)

Works:
- Relay up (pid changes on each restart), GPU worker on L4 with `detect`, event API up.
- App build from `12ae0dd` installed: automatic sampling, Map/3D, rotation-safe 3D, find me X, Ed's mic animation.
- Glasses streaming worked after re-registration (frames at 360×640 mostly); position placed at "our table".
- Orange box: found and outlined live (colour + OWLv2 with aliases); its spot now follows every sighting
  (monocular placement + `AGREE_VIEWS=1`); "phone" look-alike suppressed; old item points cleared.

Not working / fragile:
- **USB link drops** repeatedly → no frames → "relay not reachable".
- **Glasses video quality:** requested MEDIUM (504×896) @ 15 fps, but 359 of 366 frames in 10 minutes arrived
  at 360×640, heavily compressed and blurred (Bluetooth bandwidth in a crowded 2.4 GHz hall, heat). Small objects
  become a few blocks; room matching finds 5–7 agreeing points and fails.
- **Position accuracy** of the merged relay is poor (~3.4 m off, wanders 1.4 m) until exact-position is merged.
- Live placement of the box is rough (assumed size and FOV; depends on the wearer's fix).
- Every app reinstall wipes the glasses registration (new debug key per CI build).
- The relay key appeared in a local transcript at ~08:11 (from the relay's stdout log): rotate after the demo
  (new `RELAY_KEY` in `.env.demo`, restart relay, re-send `relay_key` to the app, update the family app).
- GPU worker job expires ~10:00 local; the queued replacement should take over.

---

## 8b. Every optimisation in the system (what, where, why, effect)

### Latency of position fixes
- **Instant fixes between model fixes (`--quick`, `relay.py` `quick_print`):** each photo is compared by a
  compact image signature (cosine similarity) to the room map's pictures and the last model-fixed photo; the
  answer comes in milliseconds on the laptop. The model (VGGT) runs at most every `FULL_EVERY_S` = 4 s.
  Effect: a pose ~3x a second instead of one per 5–6 s. Cost: instant fixes are rough.
- **Cheap view gate (`view_verdict`):** a photo with the same view as the last one the model saw reuses that
  answer (≤ 45 s old, `"reused": true`); a small changed region is reported under `view`; `&fresh=1` bypasses.
  Effect: no GPU time while the wearer stands still.
- **Few frames per fix:** `RECON_LOCATE_FRAMES` 3 frames of the wearer's view per fix; model input long side
  `RECON_LONG_SIDE` 518 px (smaller = faster, coarser).
- **Sharp-frame picking on the laptop (`pick_frames`, OpenCV):** uploaded videos never leave the laptop; only the
  sharp frames needed (~1 MB) go to the cluster.
- **One persistent SSH link** (opened on first use, reopened if it drops) instead of a connection per request;
  a separate background link (`bg_link`) for room maps/searches so live fixes never queue behind them.
- **GPU worker + CPU worker:** background ops go to `jun-worker-cpu`, keeping the L4 for fixes and detection.
- **Half precision / depth chunking (`recon_change.py`):** `RECON_HALF`, `RECON_DEPTH_CHUNK` (default 2) bound GPU
  memory (peak 3.5 GB measured on a 6 GB laptop GPU in local mode).
- **Rust hot loops (`recon_rust.py`, JUN-28):** optional Rust versions of the hottest numpy loops (unproject,
  patch median, nearest, look-alike counting), on by default when built (`RECON_FAST`, `RECON_FAST_OFF` to disable
  parts); benchmarked by `bench_rust.py`. Not built on the cluster workers today (`rust_enabled: false`).
- **Steady pose (`track.py`):** smoothing + confidence so the display does not jump with every rough fix.
- **Route cache:** `routed()` re-plans only when the fix, start or targets change.
- **Pending: splat PnP (`exact-position`):** SIFT + PnP against the trained splat's keyframes on the laptop CPU,
  0.3–0.6 s, with a walking-speed filter; median error 0.42 → 0.06 m. Focal length auto-settled (×1.33).

### Find me X / item finding
- **Detect op:** OWLv2 alone on the newest photo, no VGGT, ~1.1 s on the L4, only while someone is asking (20 s
  window) and one at a time per zone, so it fits between full fixes and costs nothing otherwise.
- **Colour patch on the laptop:** milliseconds, shows the outline before the GPU answers and stands in when OWLv2
  misses.
- **Prompt aliases:** "orange object" / "orange block" score 0.36–0.59 vs 0.29–0.34 for "orange box".
- **Hold 4 s + lower display floor (0.25):** stops flicker when scores hover at the limit; confirmation still
  needs the 0.30 limit.
- **Monocular placement:** a spot from every live sighting without waiting for a depth fix.
- **One-view sightings (`BEACON_FIND_AGREE_VIEWS=1`) and follow-the-newest-view:** the spot updates every time.
- **Look-alike suppression:** worker items overlapping the watched thing are dropped (no "phone" on the box).
- **Item finder internals (`items.py`):** candidate floor (`RECON_ITEMS_CANDIDATES`), per-name limits, colour
  share for colour words, size from depth, a second look / judge (`RECON_ITEMS_JUDGE`, zoomed crop) for the
  worker's own five, tiles (`RECON_ITEMS_TILES`), batching (`RECON_ITEMS_BATCH`), model cache
  (`RECON_ITEMS_CACHE`), enrolled photos per item (`/enroll`). False positives measured on 1,087 venue pictures.
- **Search of recent photos:** a newly asked thing is also looked for in the zone's latest 3 photos and its
  recorded pictures (background), so "find me X" can answer from the past.

### Recording, maps and splats
- **Chunked recording upload** (`/recording`, dedup of resent chunks, up to 4 GB videos), room map rebuilt every
  60 new frames from keyframes (`RECON_MAP_FRAMES` 16 frames per map pass), gsplat training on the cluster every
  10 min and at the end; trainings left running by a relay restart are re-registered and fetched later.
- **A rebuilt room map keeps the trained splat** (no re-training needed after a map refresh).
- **Lite splat for phones (`care_space.make_lite`):** 400k Gaussians, 12.8 MB instead of 1.74M Gaussians; made
  once next to the trained splat; the wearer page asks for `&lite=1` (and retries without it behind proxies that
  strip the parameter).
- **Places from depth** of the recorded pictures (no new picture needed); labels counted once.

### Pages and apps
- Pages are read from disk per request (no restart for UI fixes). `/find` polled ~1 s per name, `/where` ~1 s
  (0.4 s pending), live inset fetches `/frame` only when a fresh fix exists and the tab is visible.
- **Phone app:** newest-frame-only send loop (capture → post → wait for the reply → repeat; no backlog);
  1 s sampler; screen kept on only while sampling; glasses reconnect with backoff; rotation no longer recreates
  the activity (no 3D reset, no glasses reconnect); the 3D WebView reloads after a renderer crash.
- **Family app 3D (Ed, `main` #71):** lazy load, one draw per frame, no per-frame garbage.
- **Backend (Ed, `main` #72):** gzip `/mesh`, `/hazards`, `/night` (~4x smaller downloads); mesh quality levels
  (#67), block mesh at real heights (#70).

### Known optimisation gaps (not done yet)
- Glasses stream: MEDIUM @ 15 fps over Bluetooth degrades to 360×640; HIGH @ 7 fps recommended.
- Rust loops not built on the cluster workers.
- Live placement uses an assumed object size and FOV; depth from the trained splat along the ray would be better.
- The privacy pipeline (garbling costs ~0.7–0.8 dB PSNR) is not in the live path.

## 9. Recommended next steps (priority order)

1. **Stable signing key for debug builds** (CI secret) so app updates install in place and keep the Meta AI
   registration. Biggest time sink today.
2. **Glasses stream config:** `MetaGlassesAdapter.kt` → `VideoQuality.HIGH`, `frameRate = 7` (the relay only uses
   ~3 frames/s). Fewer frames leave Bluetooth room for sharper ones. Needs a rebuild + reinstall (do after step 1).
3. **Merge exact-position (`1b8e162`)** then **caretaker `38fdeb5`**, restart the relay once; run the three-spot
   test (≈20 s each at our table, snack table, drinks fridge, glasses streaming, looking at the hall).
4. Use the splat pose for live placement of found things (better fix → better box spot); optionally depth from
   the trained splat along the box's ray instead of the assumed-size distance.
5. **Merge latest `main` (edfec36: watch-only `view_token`) into `all-in-one`** — conflicts in `relay.py` are
   additive (combine `make_handler` keyword arguments and the `argparse` options; keep `VIEW_PATHS`, the
   `--view-token` print and `view_token=args.view_token`). Same resolution already done on PR #46 (fbb1c0d) and
   PR #47 (0ec9e9b).
6. Rotate the relay key. Switch `BEACON_FIND_AGREE_VIEWS` back to 2 outside demos.
7. Wire the privacy pipeline (anonymise → garble → vault) into the live path if the cluster is treated as
   untrusted (the user's stated requirement); outputs encrypted at rest.
8. Fix the test flake: `test_room_keeps_training.py` reads `rooms.json` while it is being written when tests run
   in parallel (passes alone) — write atomically (temp file + rename).

---

## 10. Testing

- Relay tests: from `ml/recon_change/live`, `python test_<name>.py` (16 files: anonymise, benchmark, care_flow,
  care_view, day_flow, finder, garble, navigate, places, places_worker, private_train, resume_training,
  room_keeps_training, setup_flow, vault, view_gate; plus `test_share.py` on `main`). They run their own relay on
  port 0 with a fake gateway; safe next to the live relay. All pass on `f8182d7` (setup_flow prints an expected
  libpng error).
- `ml/recon_change/test_*.py` (synthetic, watch, rollout, lowpoly, splat_mesh, hazards_night, capture_splat,
  splat_refine) and `eval/test_*.py`.
- Repo-wide: `make check` before hand-off (project rule). Android: CI workflow "Android apps" (compile, lint,
  unit and real-backend checks), ~6–8 min, artifacts `junction-glasses-debug`, `daylight-family-apk`.
- Product acceptance (project rule) additionally needs database failure/restart tests, browser checks and measured
  end-to-end latency; contract tests alone are not enough.

---

## 11. Branches and pull requests

| Branch | PR | State |
|---|---|---|
| `all-in-one` | #68 (draft → main) | everything combined; HEAD f8182d7; supersedes #46, #47, #8 (close only with the user's OK). Needs `main` edfec36 merged (§9.5). Merge to main with a **merge commit**, never squash. |
| `jun-22-hud-waypoints` | #46 | conflict with main fixed (fbb1c0d); Auto-fix is on. |
| `jun-26-recording-splat` | #47 (base #46) | conflict fixed (0ec9e9b); Auto-fix is on. |
| `claude/glasses-dc-fix-038490` | – | app work; f2cb074 merged into all-in-one. |
| `claude/caretaker-app` | – | 38fdeb5 pending (uncertainty ring etc.). |
| `exact-position` | – | 1b8e162 pending (splat PnP pose). |
| `find-me-x` | – | fully merged (up to 7c5e844 + sync f411eec). |
| `venue-home-3d` | – | scrapped by the user. |

Main's recent merges (Ed): #69–#73 splat vs mesh demo, family 3D faster on phones, gzip mesh/hazards/night,
three-panel demo (JUN-22); #75 wearer mic animation; edfec36 watch-only sharing (`view_token`, `share.py`,
glasses picker).

---

## 12. Rules and constraints (from the project and the user)

- Never print or commit keys/tokens; never commit data, images, recordings, database files or weights.
- Ask before downloads (except the user's own CI artifacts when they asked for an install).
- Merge to main with a merge commit, not a squash. Don't merge `main` into teammates' branches or deploy without
  the user's OK. Commenting on / changing Linear issues needs the user's authorisation.
- Ownership: `android/` Ed (app changes stay on branches for his review), `ml/` Seb (the user), `backend/`
  Bartek, `unoq/` and `demo/ayman/` Ayman.
- Don't name the cluster's institution or host in new code or PR text: call it "the cluster" / "GPU cluster";
  SSH alias `cluster`.
- Don't record into zone `kitchen`; test zones only. The user does not want new recordings right now.
- Sensor observations are authoritative; vision adds evidence only and never creates alerts.
- Keep the laptop servers and the cluster workers running unless told otherwise.

---

## 13. Glossary

- **Zone:** a recorded space with its own room map, places and splat (`kitchen` = the venue desk area).
- **Fix:** one position estimate for a photo (`state` found / unsure / lost; full = model, instant = similarity).
- **Stable:** the smoothed pose from the stream of fixes (`track.py`).
- **Room map:** VGGT reconstruction of the zone from keyframes (`room.splat`, cameras, axes in metres).
- **Trained splat:** gsplat-trained Gaussian splat of a recording (`trained.splat`, `lite.splat`), drawn by the
  pages and aligned to the room map by a 4x4 matrix (`care_space.py`).
- **Place / waypoint:** a named spot (our table, drinks fridge, …). **Seen thing:** a watched object with a
  sighting (`kind: "object"`).
- **Sighting / view / move:** finder terms (§4). **Live outline:** box drawn on the newest photo without depth.
- **DAT:** Meta Wearables Device Access Toolkit (the glasses SDK).
