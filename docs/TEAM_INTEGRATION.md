# Runnable team integration

The local system is tested against Ayman's store `98eb82b`, demo `804b821`, and Seb's
combined relay `9aaf1e3`. The current code adapters bridge their actual interfaces; they
do not require rewriting teammate files or merging the vision model into the API process.

## API and dashboard

`make setup`, then configure an explicit API_TOKEN in the process environment or private
`.env.local`. Run `make run`; open http://127.0.0.1:8000/dashboard/ and Connect with that
API_TOKEN. `make run` now loads `.env.local` as literal one-line KEY=value assignments,
with optional matching quotes, no expansion/execution. Exported environment wins.
The file is Git-ignored. Keep its permissions owner-only. Never commit credentials.
ElevenLabs key + voice ID may be added there; an absent provider fails with explicit 503.

The live provider is not exercised by CI. CI intentionally supplies empty provider keys.
The household observations remain authoritative locally; optional DATABASE_URL enables
metadata replication, not remote image upload or automatic disaster restore.

## Ayman's UNO Q store → central API

The store does not push events. Its approved `/export/events` removes image references.
Use USB forwarding/local tunnel as described in Ayman's README. Then explicitly run:

```sh
# Set UNOQ_TOKEN and API_TOKEN privately in this shell first.
.venv/bin/python -m backend.integrations.unoq_export \
  --source http://127.0.0.1:8091 --target http://127.0.0.1:8000
```

This is a one-shot metadata transfer. It validates the complete export, sends batches
of at most 100 events and validates acceptance receipts. Retries preserve producer IDs
and are idempotent. A later batch failure can leave earlier acknowledged batches; retry
is safe. Images are never fetched. Maximum export: 8 MiB / 10,000 records; a larger history
needs a future paginated source export, not silent truncation. For synthetic rehearsal,
Ayman's `demo/ayman/replay.py` already sends Bearer-authenticated canonical events and
can target the central API directly.

## Seb's relay → central API

Seb's unchanged relay `post_events` sends no Bearer header. Its relative photo paths
belong to its own origin and `X-Key` authentication. Pointing EVENT_API_URL at central
without this adapter therefore correctly returns 401.

Check out the teammate vision branch in a separate worktree, then run its relay through
Bartek's wrapper (supply the real absolute path):

```sh
# Set EVENT_API_TOKEN privately to the central API_TOKEN.
export EVENT_API_URL=http://127.0.0.1:8000/events
.venv/bin/python -m backend.integrations.vision_relay \
  --relay /absolute/path/to/vision-worktree/ml/recon_change/live/relay.py \
  -- --host 127.0.0.1
```

The wrapper changes only outgoing event transport. Its payload normalization derives
stable IDs from the original full payload and batch position, enforces source=vision,
removes size_cm and sets evidence_url=null. Original imagery stays on the relay; central
dashboard truthfully says no image attached. No arbitrary URL fetching, cloud image
copying, or change to API authentication is introduced. SSH/GPU availability still
belongs to Seb's relay deployment. Central deterministic answers never wait for it.

Cached navigation adapter can be injected with explicit zone/age/confidence policy; it
reads `/where` only. Policy remains a team decision; it is not enabled by merely setting
a token. A real physical room/glasses navigation run remains outstanding.

## Reproducible verification

`make check dashboard-check` runs Python, schema, lint and browser-client tests. The
independent combined tests also run actual teammate handlers and actual JS over loopback
HTTP. GPU gateway and ElevenLabs provider outputs in these tests are synthetic, explicitly
injected; this verifies integration contracts, not model quality or real speech latency.

`.github/workflows/bartek-ci.yml` adds a clean Linux installation, PostgreSQL 17, isolated
assembled HTTP server and explicit mandatory-source integration gate. Missing source
objects and skipped required integration tests fail CI. The workflow never deploys or
uses live provider secrets. Local full verification before publication: 306 Python
checks + 8 marked service-integration checks + 10 Node checks, followed by 2 new private
config loader checks. See the final CI run for the exact published-head result.

## Remaining release gates

- Live ElevenLabs MP3 decoding/playback and question-to-audible <2 seconds on Ed's client.
- Current Ed dementia phone/voice/camera implementation is absent from inspected remote
  refs. `feat/rescue-ping-hud` is an older responder/ping application, not that client.
- Aiven endpoint and approved HTTPS/device credentials for team hardware.
- JUN-18 face-blurring requirement is not implemented; local-only/24-hour expiry is not
  equivalent. Use synthetic imagery until the team resolves/implements that gate.
- Actual sensor hardware and room/glasses navigation accuracy. Confirm thresholds/zones.

Bartek's decision is observation-only: opening evidence never establishes ingestion,
correct dose, adherence, door locking, or safety. This is tested, not just a disclaimer.
These external acceptance gates keep the overall feature goal open even if CI passes.
