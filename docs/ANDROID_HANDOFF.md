# Android integration handoff

Bartek explicitly authorized implementing the Android app while Ed works on Rust.
All new product code is isolated under `android/`. No edits to Ed's Rust branch,
Seb's model, or UNO Q source. This branch is based on the tested Bartek/Ayman
integration (`4b7dcf9`) and should land after that integration or be rebased onto it.

See [Android setup and verification](../android/README.md). The app preserves the
existing `POST /ask {question}` -> `{answer,evidence}` and `POST /speak {text}` ->
MP3 contract. Optional explicit image attachment uses `POST /events/{id}/evidence`
with raw JPEG. A box opening remains an observation, never evidence of ingestion.

The camera and motion adapters are real SDK implementations. Hardware is unavailable
for this development run. Automatic lid-event burst capture, room-check video/model
upload, calibrated nod/shake, and navigation/reminder UI remain distinct follow-on
work. The app must not be presented as having completed those flows or as an
always-on conversational agent. Physical-device acceptance is listed in the README.

No teammate messages or Linear changes were sent. No main merge or deployment is
included. Provider keys remain on the server; the APK has no production credentials.

## Verification on 14 November 2026

- Debug APK compilation and Android lint passed (zero lint errors; remaining warnings
  include English-only UI strings and newer dependency versions).
- 22 Android/JVM tests passed with zero skipped: 16 client/controller/upload tests,
  5 Activity lifecycle tests, and 1 real assembled-backend integration test.
- `make check`: 467 passed; dashboard: 10 passed.
- Android 15 ARM64 emulator installed and launched the APK. Through its actual UI,
  entered the synthetic session token, connected via USB reverse, asked about pills,
  and received the real backend's opening timestamp and uncertainty wording. With
  provider speech disabled it entered local speech fallback. No audible playback
  claim is made for the headless emulator.
- Physical glasses, microphone quality, Bluetooth routing, display appearance,
  motion accuracy and end-to-end audible latency remain unverified.
