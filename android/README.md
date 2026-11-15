# Android glasses client

Development app for the existing Junction observation backend. Built against Meta
Wearables Device Access Toolkit 1.0.1 (official source reference
`meta/meta-wearables-dat-android` commit `f4371bc9f90249d51eb07d7f8a61e8d829294f16`).
No Meta agent or ElevenLabs key belongs in this app. `/ask` and `/speak` use the
central backend's session token; provider credentials stay server-side.

## The wearer's screen (phone and glasses)

The app opens on the wearer's screen (the gear shows setup). Anna sees one card at a time, the
same on the phone and the glasses: symbols, a time, a distance, a word or two. `core/Wearer.kt`
picks the first that applies:

| Card | When | Shows |
| --- | --- | --- |
| Night | Before 06:00, door opened in the last 10 min or she is at the door | Moon, time, "Bed?" |
| Reminder | A relay reminder is due | Bell, time, the place, arrow and metres, Done |
| Going out | Daytime, at the door or within 2 min of it opening | Keys, wallet, phone and where each was last seen |
| Answer | Up to 45 s after a question | Pills: ✓ and the opening time, or ✗ and the way. Door, Keys likewise |
| Guide | After **Go**, or "take me to the bathroom" | Arrow to the next corner, the place, metres to walk and turns left; ✓ when there |
| Idle | Otherwise | Phone: clock and next reminder. Glasses: nothing |

Cards say only what was observed: an opened pill box, never "pills taken"; a closed door, never
"locked". `wearer/WearerPanel.kt` draws the phone card plus Pills?/Door?/Keys? and the mic;
`meta/GlassesCards.kt` sends it with Meta's display components (Meta icons where they exist, our
`wearer/Glyphs.kt` as bitmaps otherwise). Data: `GET /timeline` with the backend token, and
optionally the vision relay's `/reminders` and `/where` (key in `X-Key`), every 10 s.
Directions use the relay's route around the furniture when it has one (else the straight line). Any
place the carer named on the relay's `/places` page can be asked for; while walking, `/where` is read
every 2 s. An opened pill box never shows the way back to it.
With glasses that have no display (Gen 2, the default), the guide, reminder, night and going-out
cards are also spoken in a few words ("Pill box: turn left, 3 metres"), once and again only when the
direction or distance step changes, never over an answer being spoken. Setup can turn this off.
Setup › **Wearer demo: next scene** steps through nine example scenes with no hardware.

## Build and install

Use Java 17, Android SDK platform 36 and Android Studio or the command-line tools.
SDK minimum is Android 12 / API 31. Put `sdk.dir=/your/android/sdk` in ignored
`android/local.properties` if Android Studio has not created it.

```sh
cd android
./gradlew :app:assembleDebug :app:testDebugUnitTest :app:lintDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

This installs a development APK directly, with no app-store release. The GitHub
Android glasses workflow also publishes a debug APK artifact after verification.
Release builds keep the backend token and endpoint only while the app runs. Debug
builds remember the last verified ones, encrypted, across restarts and `adb install -r`
updates (see Automatic setup); uninstalling or Forget saved backend removes them.

## Automatic setup

On launch the app sets itself up. The only screens to answer are Android's permission
dialog and Meta AI's own registration and consent screens:

1. Missing Bluetooth, camera and microphone permissions are requested once per launch,
   never again after a denial (Enable glasses access asks again).
2. Debug builds connect the backend from the adb extras below, otherwise from the
   remembered backend.
3. With Bluetooth allowed the Meta SDK starts. If Meta AI has not reported this app as
   REGISTERED after a few seconds, Register with Meta AI runs once per launch. Once
   REGISTERED, glasses camera and microphone consent are asked once per launch, then
   the glasses connect.
4. Leaving the app disconnects the glasses; coming back reconnects once. Before the
   first session starts, a timeout or session error is retried 3 times, 10 s apart,
   showing "Waiting for glasses: put them on / unfold them" and the last error.
5. The glasses end a session after a few idle minutes ("Session ended by device"). That
   is normal: while the app is in the foreground it reconnects quietly after 5 s, 10 s,
   then every 30 s, shown as "Glasses idle, reconnecting". Ask through glasses and
   Start glasses camera also connect on demand and wait up to 20 s for STARTED.

The Status card shows Backend, Glasses and Microphone chips (green ready, amber waiting,
red needs action). Every button remains as a manual fallback.

Debug builds only, with a USB-connected phone (placeholder token shown; keep real tokens
out of notes and shared shell history):

```sh
adb reverse tcp:8000 tcp:8000
adb shell am start -n com.junction.glasses/.MainActivity --es backend_url http://127.0.0.1:8000 --es api_token REPLACE_WITH_API_TOKEN
```

Sending it again to the running app reconnects the backend without restarting the app
or dropping the glasses session. The extras are removed from the launch intent after
use, never reused when Android recreates the screen, and ignored by release builds.
After a successful check, debug builds remember the address and token: AES/GCM
encrypted with a non-exportable Android Keystore key, in app-private storage with
backup disabled, never logged or shown. Forget saved backend deletes them; new extras
replace them after their own successful check. Release builds persist nothing.

When the SDK reports no glasses at all (after its few-second settle window), the app
does not open Meta AI or connect by itself; use the glasses buttons when glasses are near.

## Live camera to the relay

The Live camera card streams the phone's back camera to the relay so the laptop can
rebuild the room as a live Gaussian splat. Start live camera runs back to back with one
request in flight: the newest camera frame (CameraX ImageAnalysis, keep-only-latest,
about 1280x960) becomes an upright JPEG (quality 80, long side 960 px), is POSTed to
`{relay}/locate?zone={zone}` with the key in the `X-Key` header (never in the URL), and
as soon as the reply arrives the next newest frame goes out. No old frame is queued and
no delay is added. While the gyroscope shows fast turning (over 1.5 rad/s in the last
150 ms) frames are skipped and counted, for at most 1 s. The card shows the headline,
the state, the relay's quick match ("quick match 0.87 · turned 12°" or "skipped
(repeat)"), the round trip, frames per second over the last 10 s, frames sent, shaky
frames skipped and a thumbnail (refreshed at most twice a second). Errors are shown and
retried after 2 s. It stops with Stop live camera or when the app leaves the screen,
and keeps the screen on while running.

The card chooses the camera: **Phone** or **Glasses**. Glasses frames come from the
connected glasses session: starting uses the app's glasses camera start, which connects
on demand and asks for the glasses camera consent in Meta AI first (that hand-off leaves
the app, so the stream stops; start it again after answering). Each request takes one
photo from the running glasses camera (the DAT 1.0.1 stream offers photo capture), so the
rate is lower than the phone's; the session stays busy, so the glasses do not end it as
idle. If the glasses are not connected the card says so; it never switches to the phone.

**Record room** records the zone typed on the card for 30 s (10 to 60) at about 3 photos
per second from the selected camera, then uploads them in memory as a stored zip of
`000.jpg`, `001.jpg`, ... to `{relay}/baseline?zone={zone}` (same `X-Key` header) and
shows what the relay kept. That replaces the zone's baseline, so the app asks first,
naming the zone. The relay needs at least 6 photos. Leaving the app stops the recording
and nothing is uploaded. The live stream pauses while recording.

### Record video, then the laptop maps it

Recording and mapping are separate steps: nothing is processed on the phone while
recording, and the video goes to the laptop (`POST {relay}/recording?zone={zone}`, the
`X-Key` header, `video/mp4`, streamed with progress) which builds the map. The card shows
the relay's status and "Open on the laptop: http://127.0.0.1:8090/..." (the viewer path
the relay returns, without the key). Check status asks `GET {relay}/recording?zone=`.
**Record video** follows the same Phone / Glasses choice and zone as Live and Record room:

- **Glasses** (main path): Record video arms the app and the button becomes Cancel. Record
  on the glasses (press and hold the capture button, or "Hey Meta, record a video"), then
  open Meta AI so it imports the video into `Download/Meta AI/`. The app notices the new
  video, waits until its size has stopped growing for 3 s, sends it and disarms (gives up
  after 10 minutes). It asks for **video access (READ_MEDIA_VIDEO)** at first use and only
  reads Meta AI's files, never changes or deletes them. "Glasses videos" lists them,
  newest first, with Send to laptop; "Auto-send new glasses videos" sends each new one
  once while the app runs. DAT 1.0.1 gives photos, not video, from the glasses session.
- **Phone**: Record video records the back camera here (CameraX, FHD or HD, no audio;
  live and Record room stop meanwhile), with a red REC indicator; Stop recording or
  leaving the app finishes it. The file is
  `/sdcard/Android/data/com.junction.glasses/files/recordings/room-<zone>-<yyyyMMdd-HHmmss>.mp4`
  and is sent when done ("Send to laptop when done", on by default). After a successful
  upload it is deleted unless "Keep video on phone" is on; after a failure it stays, with
  Retry and Delete in the list, and can be copied with
  `adb pull /sdcard/Android/data/com.junction.glasses/files/recordings/<name>`.

Privacy: videos have no audio. A phone video stays in this app's own storage (no other
app can read it; backup is disabled) until it is uploaded, and is then deleted unless
kept. Glasses videos stay where Meta AI put them, untouched. Videos go only to the
configured relay.

Debug builds only (placeholders shown; relay defaults: `http://127.0.0.1:8090`, zone
`kitchen`; add `--es relay_url ...` for another address):

```sh
adb reverse tcp:8090 tcp:8090
adb shell am start -n com.junction.glasses/.MainActivity --es relay_key REPLACE_WITH_RELAY_KEY --es relay_zone kitchen
```

Frames go only to the configured relay and nothing is saved on the phone: photos stay
in memory. Debug builds remember the relay address, key and zone, encrypted like the
backend token; Forget saved backend clears them too. Release builds ignore these extras.
Debug builds may use cleartext to loopback and to private LAN addresses (10/8,
172.16/12, 192.168/16); any other relay or backend address must be HTTPS, and release
builds are HTTPS-only.

**Cable out: the relay over Wi-Fi.** The relay listens on all interfaces, so on the same
Wi-Fi the phone can reach `http://<laptop LAN IP>:8090`. Give that address once with
`--es relay_lan_url http://192.168.1.20:8090` (remembered, encrypted). The always-on
recording then tries 127.0.0.1 (adb reverse) first and switches to the Wi-Fi address
when the USB address does not answer, trying USB first again 30 s later; the card says
"(Wi-Fi)" next to the last upload while it uses it.

### Record room at 10 frames per second

Record room takes the newest frame of the selected camera 10 times a second for 10 to
60 s (the glasses camera delivers 15 decoded frames per second; the phone's ImageAnalysis
more). A tick with no new frame is skipped and counted, never filled with a repeat, and
never waits for the camera. The zip holds up to 1200 frames or 120 MB, named `0000.jpg`
onward, and replaces the zone's baseline on the relay.

## Wearer map and "take me to"

Under the wearer screen there is a map of the venue, north up: places as labelled dots, the
wearer as a green dot with an arrow for the way they face. Tap a place, or say or type "take me
to / where is / how do I get to the pill box" (close names are forgiven; "stop" ends it), and
the app asks the relay `GET /navigate?zone=&to=` every second, draws the route and speaks short
lines through the selected audio output (the glasses over Bluetooth): when the direction changes,
otherwise at most every 4 s, and "You're there" within 0.7 m. Position fixes come from the
always-on recording or, without it, from the live glasses camera started for the walk.

The map and guidance use the **venue zone**, apart from the zone field that recordings go to,
so recordings can go to a test zone while the wearer is guided in the venue. Debug builds set it
with `--es venue_zone kitchen` (remembered); without it they use the zone field.

### Finding the wearer by itself

While the wearer screen is in front and a relay key is set, the app samples the camera to the relay by
itself, with no button: the newest frame goes to `POST /locate?zone=<venue zone>`, one at a time.
- It uses the glasses while their session is up, and the phone camera otherwise (asked for once).
- It switches camera when the glasses connect or drop. The glasses camera restarts after an idle drop.
- The screen stays on while sampling.
- One line above the map says how it is going, e.g. "Found you · glasses · last fix 2 s ago" or
  "Can't find you: relay not reachable".
- This is not recording: nothing goes to `/recording` and nothing is kept on the phone.
- "Stop live camera" in setup turns it off until "Start live camera".

### Map / 3D

Above the map, a big **Map / 3D** toggle (Map is the default) swaps the flat map for the relay's 3D page
(`/care?zone=<venue zone>&view=wearer`): the venue as a Gaussian splat with the wearer in it, following
them, with the route while guided. The page shows guidance and finds itself, from the relay.

How the page is loaded:
- A WebView loads it from the app's own origin (`https://appassets.androidplatform.net/relay/...`, never
  fetched from the network).
- The app answers those requests natively (`live/RelayPageProxy.kt`, as in the family app): it removes
  `/relay` and asks the relay with the key in an `X-Key` header, the USB address first, then the Wi-Fi one.
- Only the page's read-only routes are passed on, and the key never appears in an address.
- If the page's renderer crashes, the app goes back to the map.

### "Find me X"

"Find me the orange box", "where is / where did I put / have you seen the orange box" (glasses or phone
mic, or typed) ask the relay `GET /find?zone=<venue zone>&what=orange box` when the thing is not a named
place (named places stay guidance). The answer is spoken through the selected audio (the glasses'),
shown on the wearer screen, and the thing is drawn on the map as an amber ring. While the relay says it
is still `looking`, the app says "I'll look for the orange box" and asks again every 3 s for up to a
minute. Then "Take me there" (the button under the map, or said) guides to it with `/navigate?to=`.

### Debug panel

A small dim "debug" under the map opens, on a long press only, a panel with no secrets:
- the build (commit) and the relay addresses (USB and Wi-Fi)
- the relay key's and backend token's prints (the first 6 hex of their SHA-256: compare with
  `printf %s "$RELAY_KEY" | sha256sum | cut -c1-6` on the laptop)
- the recording zone and the venue zone
- the last answer of every relay and backend call, with its HTTP status and age (0 = no answer,
  403 = key or token mismatch, 404 = not on that server yet). /locate and /navigate also show
  what they said.
- the glasses' state with the last drop reason and time
- recording and upload state

Refresh asks the relay and backend now. Retry uploads sends the always-on chunks left on the
phone (or makes a running recording retry at once). Copy puts the same text on the clipboard.
Change settings edits, in place:
- the relay addresses (USB and Wi-Fi) and its key
- the recording zone and the map and guidance zone
- the backend address and token

Keys and tokens are never filled in: left empty they stay as they are. Full setup opens every setup
card (the gear on the wearer screen does the same and scrolls to them).

## Always-on recording (screen off, until Stop)

The **Always-on recording** card records the room for the laptop all the time: with the
screen off, in other apps, and after the app's screen is closed, until **Stop** on the
card or in the notification. It runs as a foreground service with a persistent
notification, "Recording the room for the laptop", showing the frames sent, the glasses
state, the last upload and the battery, with a **Stop** button. Leaving the app does not
stop it.

- **Start**: only when you tap **Start recording** (a fresh install never records by
  itself). The choice is remembered: after Start, opening the app resumes the recording
  (with a relay key set); after Stop it stays off until you tap Start again. Debug builds
  can switch it with `adb shell am start -n com.junction.glasses/.MainActivity --ez always_on false`
  (off, and stops a running recording) or `--ez always_on true`. The first start asks once
  to allow notifications.
- **Camera**: the glasses (the session stays up in the background; if they are not
  registered or asleep the card says what it is waiting for), or the phone's back camera
  only if you pick Phone. It never switches by itself.
- **Frames and chunks**: the newest frame 10 times a second (none repeated), upright
  JPEG, long side 960, quality 80. Every 10 s or 100 frames the frames go to the laptop as
  one chunk: a zip of `NNNNNN_<epochMillis>.jpg`, `POST {relay}/recording?zone=&session=&seq=&final=0`
  with the `X-Key` header, `final=1` on Stop. The newest frame also goes to `/locate` for
  an instant fix, one request at a time.
- **Upload failures**: chunks wait in the app's private storage (at most 300 MB; above
  that the oldest are dropped) and are retried in order, after 2 s, 5 s, 10 s, 30 s, then
  every 60 s. A chunk the relay refuses as malformed (HTTP 400 or 413) is dropped. After
  Stop the app keeps sending for up to 60 s; anything left goes with the next recording.
- **Battery and heat**: the card and notification show the battery; above 45 °C (the
  phone's battery temperature) capture pauses, with a message, until it is below 43 °C.
- **While it runs**, Live camera and Record room are unavailable (one camera user at a
  time); the always-on recording already sends instant fixes.

Privacy: frames go only to the configured relay. Chunks are kept on the phone only until
the relay has them, in the app's own storage (no other app can read it; backup is
disabled). Nothing is recorded without the notification showing.

## First phone-only test

1. Start the central API with `make run` from the integration checkout. Configure
   its `.env.local` there; never copy its provider credentials into Android.
2. For a USB-connected phone run `adb reverse tcp:8000 tcp:8000`. For our isolated
   development server on port8097 use `adb reverse tcp:8000 tcp:8097` instead.
3. Open **Memory with receipts**, enter `http://127.0.0.1:8000` and the central API
   session token, then Connect backend. This verifies authentication with a read-only GET /events?limit=1 probe.
   A failed check leaves questions disabled; enter a corrected token or retry. The token disappears from the input after configuration.
4. Type a question, then try Use phone microphone. Recognition uses Android's
   speech service with offline preference; availability and processing depend on
   that service. Typing remains available if speech permission/service is absent.
5. Read the observation answer and timestamp. If `/speak` succeeds, MP3 plays from
   memory. If it fails, an installed offline phone TTS voice is used; if that is
   absent too, the answer stays readable. Stop cancels pending work and playback.

Only debug builds permit cleartext loopback (`localhost`, `127.0.0.1`, `10.0.2.2`).
Other connections and release builds require HTTPS. Do not disable certificate
checks or put tokens in URLs. Use a trusted HTTPS deployment for untethered use.

## At the venue: real glasses

1. Confirm compatible glasses and firmware. For display output use Meta Ray-Ban
   Display; other supported glasses can report display unsupported.
2. Pair with the Android phone in Meta AI and enable Developer Mode. The APK uses
   Meta application/client metadata set to 0/0 for developer-mode testing. Publishing
   requires the appropriate registered Meta app settings; not configured here.
3. Open our app: it requests access, registers with Meta AI, asks glasses consent and
   connects by itself (Automatic setup). Enable glasses access, Register with Meta AI
   and Connect glasses remain as manual steps. Registration and permission handoffs
   intentionally open Meta AI. Wait for the connection to show STARTED.
4. Use Ask through glasses. The SDK speech capability emits a completed transcript;
   this triggers `/ask`, renders the answer on the glasses display and requests
   speech. Audio output uses Android's selected media/Bluetooth route. Select and
   verify the glasses route on the physical phone; SDK session connection alone
   does not prove audio is routed to glasses.
5. Start glasses camera and take a no-upload test capture. To exercise the real
   evidence path, first ask about an existing observation, choose Capture and attach
   supporting photo, select its event, and explicitly confirm. Use synthetic scenes
   until team image handling is approved. The image goes only to the configured
   backend's authenticated evidence route, where central image processing applies.
6. Start glasses motion to see accelerometer samples. These are measurements, not
   calibrated nod/shake gestures. Stop motion or disconnect to end the stream.
7. Verify Stop, interrupted audio, denied permissions, disconnect/reconnect,
   background/foreground and a second question while the first is in flight.

Glasses microphone/speech and motion use experimental SDK capabilities; firmware
and availability must be tested. The app does not claim an always-listening agent,
background capture, automatic sensor-triggered bursts, room-check clip/frame upload,
calibrated nod/shake, or navigation arrows. Those are later device/model workflows;
Seb's `/where` and `/reminders` relay contract is not silently assumed to be the
central `/ask` contract. Phone and glasses microphone modes are explicit.

## Tests and boundaries

Core tests cover authentication, bounded responses, redirect rejection, cancellation,
late callbacks, audio-focus interruption, offline fallback and image upload. Android
Activity lifecycle tests exercise real controls under Robolectric. The assembled
backend test seeds synthetic observations and uploads a generated image, then checks
answer evidence and speech-unavailable behavior against an actual HTTP process.

```sh
JUNCTION_ANDROID_TEST_URL=http://127.0.0.1:8097 \
JUNCTION_ANDROID_TEST_TOKEN=synthetic-android-only \
./gradlew :app:testDebugUnitTest --rerun-tasks
```

Use a disposable backend with no ElevenLabs key for this test (it expects speech503).
Without these variables the assembled-backend test is explicitly skipped; CI requires
it to execute. Emulator tests do not establish physical glasses capture/display,
Bluetooth output, real microphone quality, gesture accuracy or audible latency.

No raw recordings/photos are saved by this app. Release builds save no session token;
debug builds save only the encrypted backend address and token described under
Automatic setup, until Forget saved backend or uninstall. Camera captures
are memory-only, upload request copies are cleared after transport completes,
application backup is disabled, screenshots are blocked, and SDK analytics/crash
reporting are opted out. This does not establish privacy behavior of Meta AI,
Android's chosen speech service or the entire device.

## Setup diagnostics

Connect backend verifies the authenticated event API, rather than merely storing an
address. It reports rejected tokens, unreachable servers and unexpected responses.
The in-memory token can be reused by pressing Connect backend again; changing the
server requires its token. Release builds persist no credentials across app restarts;
debug builds keep only the encrypted remembered backend (Forget saved backend clears it).

The glasses setup section separates Android Bluetooth/microphone/camera permissions
from Meta camera/microphone consent. UNKNOWN means consent has not been checked;
it does not mean permission was denied. Registration, detected devices, session and
component states remain visible separately from question results.

- Test display sends fixed text and asks the wearer to confirm seeing it. SDK acceptance
  is not proof that the wearer saw it.
- Test audio calls /speak with a fixed test phrase, without recording a question via
  /ask. It labels ElevenLabs or offline Android fallback, including completion/failure.
  Android controls the media route; confirm sound on the glasses yourself.
- Start glasses camera, then Test camera captures a JPEG in memory and discards it.
  It never uploads the test photo. Supporting-photo upload remains a separate action.
- Stop, backgrounding, asking or replaying cancels pending hardware diagnostics.
  Reconnecting clears old observation choices and invalidates pending upload dialogs.

Physical pairing, Meta consent, wearer visibility and Bluetooth routing still require
real glasses. Emulator checks cannot establish these.

## Gen 2 audio-first setup

Gen 2 is the default mode. No display component is created or required, and Test
Display is hidden. Answers and evidence remain visible on the phone and spoken
through Android's selected media route. For display-equipped devices, explicitly
select “Optional: I am using display glasses”, then reconnect. Unsupported display
capability does not block audio, questions or camera use.

Ask for directions offers supported destinations through the existing /ask endpoint.
The backend's configured navigation adapter enforces confidence/freshness and returns
short location estimates; missing/stale/unconfigured navigation returns uncertainty.
Replaying a direction question requests a fresh estimate instead of replaying an old
bearing. This is on-demand orientation, not continuous turn-by-turn route guidance,
obstacle avoidance or a new model integration.

Camera remains explicit: start it before capture. Every capture attempt stops the
camera, and a session with no capture stops after 60 seconds. Stop also ends camera
and motion. No automatic image sampling or background recording is introduced.
Battery life and Gen 2 SDK microphone/camera support still require physical tests.
