# Ayman's demo kit — synthetic rehearsal

This kit demonstrates local storage now. It does not claim sensor wiring, medication
confirmation, door locking, working local VGGT or recorded fallback clips. A lid opening
alone is not proof pills were swallowed; a door opening/closing is not proof it was locked.
Ed owns the pitch; this is a staging and recording guide.

## Table and preflight

- UNO Q, known data-capable USB cable, laptop with ADB, phone/glasses with Ed's app.
- Pill box and a clearly labelled front-door prop; both are props until sensors are wired.
- One fist-sized opaque object and a well-lit uncluttered surface for the eventual vision step.
- Keep real household photos and model data off cloud services, public repos and recordings.
- Power the board; verify `junction-store` active using `systemctl --user status` on board.
- Check board time and disk capacity; correct time separately before retention rehearsal.
- Forward USB: `adb forward tcp:8091 tcp:8091`. Export the board token privately as
  `UNOQ_TOKEN` on the laptop; never show it on camera or put it in a command history.
- Start from a dedicated rehearsal data directory when possible. Fixtures have stable
  `synthetic-demo-*` IDs; repeating replay is idempotent. They do not overwrite real IDs.

## Rehearse

1. Validate fixtures without network: `python3 demo/ayman/replay.py --dry-run`.
2. Ingest: `python3 demo/ayman/replay.py`. The only destination is the loopback/private
   UNO Q API. All three events are explicitly simulated, using UTC timestamps.
3. Show `/timeline` through an authenticated local client. Say “these events are simulated”.
   Show a local evidence upload/download using `unoq/README.md` API contract.
4. Once Ed/Bartek connect their apps, rehearse “Did I already take my pills?” and the night
   door story using their rules. Do not claim the storage service itself answers questions,
   infers ingestion or generates alerts. Agree their expected type names before replay.
5. Show `/export/events`: text metadata only, with null evidence URLs. Show the visual
   artifact remains retrievable locally. No cloud sync is enabled by this service.
6. Restart the service, then retrieve the same event/evidence. Keep USB power connected.
7. Turn off upstream internet while retaining local connectivity; repeat storage steps.
   Do not disconnect USB power to simulate network failure. Power-cycle persistence is a
   separate check after a clean shutdown, not a substitute for network testing.
8. Vision: state that VGGT is currently blocked by RAM/runtime limits. Use no remotely
   processed household clip as evidence of on-board execution. Only add the live object
   move or Gaussian splat after local model validation passes.

## Fallback recording checklist

Record each verified step separately, then a continuous rehearsal. These are deliverables
still to be filmed, not supplied by this kit:

| Clip | Capture | Acceptance |
| --- | --- | --- |
| 01-local-storage | Ingest a labelled synthetic event; retrieve timeline | Event ID/time visible; token hidden |
| 02-evidence | Local upload and retrieval | Same image retrieved; no public URL |
| 03-restart | Restart and retrieve | Same committed data after restart |
| 04-app-answer | Phone/glasses question and actual answer | Only once app integration works; supported event semantics |
| 05-night-door | Simulated door event and real dashboard response | Simulation clearly labelled; only once alert integration works |
| 06-room-change | Public/demo room, object moved | Only if local VGGT works; show actual timings and output |

Record with a phone camera/screen recorder into a **local, unsynced** folder. Avoid bystanders,
notifications, account details, tokens and real personal data. Export MP4/H.264 with audio,
check it plays offline on the presentation laptop, and keep a second local copy. Label
recorded segments and measured delays clearly. Don't record a mock as a working feature.

## Status and handoff

Storage is independently demoable. App/rules integration belongs to Ed/Bartek; model
optimization belongs with Seb. Actual staging, recording, physical power-cycle and offline
rehearsal remain pending. JUN-24 must stay open until those are done.
