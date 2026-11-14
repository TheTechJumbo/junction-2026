# Android glasses client

Development app for the existing Junction observation backend. Built against Meta
Wearables Device Access Toolkit 1.0.1 (official source reference
`meta/meta-wearables-dat-android` commit `f4371bc9f90249d51eb07d7f8a61e8d829294f16`).
No Meta agent or ElevenLabs key belongs in this app. `/ask` and `/speak` use the
central backend's session token; provider credentials stay server-side.

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
Keep the existing installation if it contains configuration you need: backend token
and endpoint in this app are session-only and are cleared when its process ends.

## First phone-only test

1. Start the central API with `make run` from the integration checkout. Configure
   its `.env.local` there; never copy its provider credentials into Android.
2. For a USB-connected phone run `adb reverse tcp:8000 tcp:8000`. For our isolated
   development server on port8097 use `adb reverse tcp:8000 tcp:8097` instead.
3. Open **Memory with receipts**, enter `http://127.0.0.1:8000` and the central API
   session token, then Connect backend. This configures the client; the first Ask
   verifies connectivity. The token disappears from the input after configuration.
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
   blank Meta application/client metadata for developer-mode testing. Publishing
   requires the appropriate registered Meta app settings; not configured here.
3. In our app, Enable glasses access, grant Android permissions, Register with
   Meta AI, then Connect glasses. Registration and permission handoffs intentionally
   open Meta AI. Wait for the connection to show STARTED.
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

No raw recordings/photos or session tokens are saved by this app. Camera captures
are memory-only, upload request copies are cleared after transport completes,
application backup is disabled, screenshots are blocked, and SDK analytics/crash
reporting are opted out. This does not establish privacy behavior of Meta AI,
Android's chosen speech service or the entire device.
