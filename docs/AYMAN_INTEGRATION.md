# Bartek + Ayman storage integration

This branch combines Bartek PR #13 at `3f76d79` with Ayman PR #6 at
`98eb82b773e2986fd2ab8a60be3eb3f2b735fa4a`. Ayman's `unoq/` files are merged
unchanged. No experimental quantized model, vision branch, sensor firmware or phone
implementation is included. `main` is not changed by preparing this branch.

## What is connected

UNO Q local store -> explicit metadata export CLI -> central event API -> rules,
family dashboard data, optional PostgreSQL replication and ElevenLabs speech.

Ayman's store has no outbound sync. Start a transfer explicitly; it does not keep
polling in the background. Event IDs are preserved and repeated transfers are
idempotent. The source export strips image references; photos/geometry remain local
on the source. The central dashboard therefore reports no photo for an exported event.
A missing photo does not block an observation answer. Device timestamps are retained;
an incorrect board clock must be corrected by its owner before a current-day rehearsal.

## Connect the physical board

1. Keep the UNO Q store running per [Ayman's instructions](../unoq/README.md).
2. On the computer running this checkout, establish the board's loopback connection:
   `adb forward tcp:8091 tcp:8091`. A trusted local tunnel is another option. Do not
   expose bearer-authenticated HTTP on the venue LAN.
3. Configure `UNOQ_TOKEN` and the central `API_TOKEN` privately in the process environment.
   The source token comes from the board's private service configuration. They are
   different credentials; the Aiven URI and ElevenLabs key are not either API token.
4. Start the central API with `make run` (default loopback port 8000).
5. Run from this checkout:

   ```sh
   .venv/bin/python -m backend.integrations.unoq_export \
     --source http://127.0.0.1:8091 --target http://127.0.0.1:8000
   ```

6. Open the central dashboard and select the event's Helsinki calendar day. Repeat the
   transfer after another observation; there should be one row per producer event ID.

The export CLI reads tokens from its process environment, not `.env.local`. The API's
`make run` launcher reads `.env.local`. Never source an untrusted configuration as shell
code or paste tokens into issue comments. No automatic photo copying is enabled.

## Verification

Run `make check dashboard-check unoq-check`. The merged-source regression launches
this checkout's actual UNO Q server and assembled central API, then runs the real
export CLI; it does not substitute source code extracted from another Git commit.
CI runs the five UNO Q checks and explicitly requires all three merged-source
regressions, as well as the existing central PostgreSQL/HTTP gates.

Live integration on 14 November 2026 used synthetic observations and a public test
image with the merged source on a laptop. Source -> transfer CLI -> central timeline
and observation answer -> live Aiven -> live ElevenLabs passed. Duplicate transfer
kept one event; the photo remained source-local and cloud metadata had no photo URL.
One question-to-MP3-response sample took 486 ms; this is not phone playback latency or
a reliability percentile. The synthetic remote records were removed afterward.
Provider credentials were not copied into this worktree or committed.

## Still outside this verification

- Physical UNO Q USB/tunnel connection, its real credentials, clock and reboot behavior.
- Physical sensors, explicitly deferred in JUN-13; synthetic sensor events are not a
  claim that wiring exists.
- Ed's actual phone capture/playback flow and end-to-end audible latency.
- Seb's replacement vision architecture and real-room accuracy.
- Production HTTPS/deployment or a merge into main.

This integration PR is a combined review candidate. Coordinate how it lands with PRs
#6 and #13 rather than merging multiple copies independently.

## Source storage limits

UNO Q stores uploaded image bytes unchanged. Central face and metadata processing
does not protect those source artifacts; resolve source privacy before using real
household images. Metadata transfer never fetches or uploads those files.

The importer accepts at most 10,000 events and 8 MiB per export. The source keeps
event history, so reaching either limit stops further imports visibly; pagination
or deliberate archival is required for longer operation. Source ISO 8601 timestamps
are normalized to central-compatible timestamps while preserving their instant.
