# Android glasses app (com.junction.glasses): hand-off, 15 Nov 2026

Branch `claude/glasses-dc-fix-038490` (pushed; no PR, not merged): `android-autostart` plus main merged in at ba19d03 (Ed's wearer screen).
Full docs are in `android/README.md` on that branch. No secrets here: real values are in `.env.demo` (API_TOKEN, RELAY_KEY).

## What the app does now
- **Wearer screen (Ed's)** first: reminders, "ask" and mic, answers. Under it are a map and a small "debug" link. The gear toggles setup.
- **Setup cards** (under the gear):
  - status chips; backend connect; ask and listen
  - glasses setup and tests; live camera; Record room / Record video
  - always-on recording; Ed's wearer card
- **Automatic setup**:
  - asks for permissions once
  - connects the backend from the adb extras, or from the remembered backend (debug builds: AES/GCM, Keystore)
  - registers with Meta AI, asks glasses consent, connects
- **Glasses reconnect rules** (MetaGlassesAdapter + AutoGlassesSetup; logcat tag `GlassesSession`). Each session end is classified:
  - idle end: reconnects quietly
  - quick drop: backoff; a hint after 3
  - asleep (DEVICE_UNAVAILABLE etc.): 5 s, 10 s, then 30 s, no cap
  - needs the user: opens Meta AI's glasses-app page once per launch, then retries twice 15 s apart
  - on-demand connects wait 10 s after a failure
  - the consent check runs off the main thread (ANR fix)
- **Live camera**: the newest glasses frame (videoStream, 504x896 I420, 15 fps) goes to relay /locate, one at a time. Phone camera fallback.
- **Record room** (10 fps, zip of frames) and **Record video** go to relay `POST /recording?zone&session&seq=0&final=1`. Any video name is accepted. Failed videos stay on the phone with Retry.
- **Always-on recording** (opt-in: only after Start; Stop is remembered):
  - foreground service (connectedDevice|dataSync|camera) with a wake lock; keeps going with the screen off
  - 10 fps chunks (10 s or 100 frames) sent in order, with backoff 2/5/10/30/60 s
  - queue capped at 300 MB; /locate instant fixes
  - pauses above 45 °C
  - USB (adb reverse) first, then `relay_lan_url` over Wi-Fi
  - glasses are the default source
- **Map and "take me to"**: `GET /navigate?zone=&to=` every 1 s. It draws the route and speaks short directions: on a direction change, otherwise every 4 s, and "You're there" within 0.7 m. The map and guidance use `venue_zone`; recordings use the zone field.
- **Debug panel** (long-press "debug"), with no secrets:
  - build commit, relay USB/Wi-Fi addresses
  - key and token prints (first 6 hex of SHA-256)
  - zones
  - each call's last HTTP status and age (0 = no answer, 403 = key mismatch, 404 = not on that server)
  - glasses last drop and time
  - uploads: Refresh, Retry uploads, Copy

## Builds
| What | Commit | CI run | On the Pixel? |
|---|---|---|---|
| map + "take me to" | f15eadd | 43658963658 green | **installed** 02:43 EET 15 Nov |
| + venue_zone | 5ba4802 | 43661863300 green | not installed |
| + debug panel (last app commit; later commits are docs only) | 459ed3a | 43663155524 (was running when written) | not installed |

The installed f15eadd ignores `venue_zone`, so its map uses the zone field (currently `always-on-test`).
Seb's decision: install the tip only once the relay's `/navigate` is live, so it costs one Meta AI approval.

## Build and install (CI only: no local Android SDK)
```sh
gh workflow run android-ci.yml --ref claude/glasses-dc-fix-038490      # builds and tests :app and :family
gh run download <RUN_ID> -n junction-glasses-debug -D <empty dir>
adb uninstall com.junction.glasses      # required: every CI build has a new debug signing key
adb install <dir>/app-debug.apk
for p in BLUETOOTH_CONNECT CAMERA RECORD_AUDIO POST_NOTIFICATIONS; do adb shell pm grant com.junction.glasses android.permission.$p; done
adb reverse tcp:8000 tcp:8000; adb reverse tcp:8090 tcp:8090
adb shell am start -n com.junction.glasses/.MainActivity --activity-single-top \
  --es backend_url http://127.0.0.1:8000 --es api_token <API_TOKEN> --es relay_url http://127.0.0.1:8090 \
  --es relay_lan_url http://<LAPTOP_LAN_IP>:8090 --es relay_key <RELAY_KEY> --es relay_zone always-on-test --es venue_zone kitchen
```
- On Git Bash for Windows, set `MSYS_NO_PATHCONV=1`.
- `--ez always_on true` also starts recording.
- Re-send `am start` with `--activity-single-top` while the app is in front, or the extras are dropped.

Uninstalling wipes the remembered backend and the Meta registration. On the next launch Seb approves in Meta AI (registration, then camera and mic consent). **Ask Seb before every install.**

## Glasses findings (RB Meta 0076)
- "Insta dc" was not our bug. The glasses lacked the Developer Mode glasses app (Meta AI calls it DevMode DWA, `com.meta.wearable.dat.developer`), so every session failed after about 0.3 s with `DAT_APP_ON_THE_GLASSES_UPDATE_REQUIRED`.
  - Fix: in Meta AI → Developer mode, tap the glasses row to install it. After that the session STARTED in 150 ms.
  - Toggling Developer mode off unregisters our app and deletes its permissions.
  - The refusal came back once after a fold and case cycle.
- When nothing streams, the glasses end the session after a few idle minutes (about 3.5 min seen). That is normal, and the app reconnects. With the camera streaming it held more than 6 min.
- Taking the glasses off, folding them or putting them in the case gives `DEVICE_UNAVAILABLE` until they are worn again.
- onStop: leaving the Activity used to drop the glasses, which killed background recording. It now keeps the session while always-on records (GlassesHub keeps the adapter). Meta AI's registration and consent screens leave the Activity on purpose.

## Open items
1. Install 459ed3a (debug panel + venue_zone) together with the relay's `/navigate` (it was 404 at the last check). Then launch with `--es venue_zone kitchen`.
2. Labels for kitchen's new splat (02:45, session aon-20261115-024529, 48 pictures): once it is linked, re-place the visible landmarks on the new frames, checking each spot. Do not reuse the old frame/u/v values.
3. Ed's review: `android/` is his. Nothing is merged to main; merging uses merge commits.
4. Recording and video uploads default to zone `always-on-test`, so nothing overwrites kitchen by accident.
