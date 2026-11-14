# Bartek implementation handoff — 14 November 2026

The event API/storage, rules answer engine, dashboard and speech endpoint are implemented
and integrated on `codex/bartek-foundation`. Original main is unchanged. All commits are
local; nothing was pushed, merged to main, deployed, or sent to teammates/Linear.

Foundation starting commit: `40c8eff1f8437008980c90055314d8dbc11640ec`.
Integration commits include API `2699ed6`, engine `1c416c3`, dashboard `cdf1785` plus browser
fix `79898e2`, speech/HTTP tests `38d7a73` and synthetic replay `a2cf5db`.
Three isolated implementation agents completed API, engine and dashboard; integrator
implemented speech, shared wiring and end-to-end checks. Earlier desktop-chat creation
requests did not expose runnable thread IDs; those pending worktrees were not used.

## Run locally

From this worktree, run `make setup` once. Python 3.12 and Node 24 were used for verification.
The app reads exported environment variables; it does not automatically read `.env`.

```sh
export API_TOKEN=synthetic-local-demo-token
export LOCAL_DATA_DIR=.data/demo
export SENSOR_IDS=synthetic-unoq
make run
```

In another terminal with the same API_TOKEN, run `make rehearse`, then open
http://127.0.0.1:8000/dashboard/ and Connect with that token. This is a deliberately public
example token for loopback synthetic data only. Use a private token for any real data.
The browser stores the token only in memory and clears it on reload. The demo replay
adds four synthetic observations and three questions. Running it again adds another set.
The separate “Explore synthetic demo” button uses visibly labelled static fixtures.

At handoff a local preview was left running on port 8000 against `.data/demo` with the
example token above; process/session longevity is not guaranteed after the app exits.
Restart using the command above if needed. `.data/integration` contains only synthetic
acceptance/benchmark records and is separate from the clean demo. Screenshot artifacts
are local ignored files `.data/dashboard-desktop.jpg` and `.data/dashboard-verified.jpg`.

## Implemented behavior

- Strict v3 event validation, atomic batches, idempotent retries and conflict rejection.
  Legacy ID-less vision batches use stable derived IDs. Sensor observations do not prove
  medication ingestion or door locking. Vision never creates alerts.
- SQLite WAL/FULL commits are the local durable acceptance boundary. Background outbox
  replication writes PostgreSQL metadata without delaying the fast path. Reads continue
  during PostgreSQL outages; queued metadata survives process restarts.
- Polling and SSE use durable replay cursors; evidence changes replay the same event ID.
  Raw and multipart JPEG/PNG uploads are decoded/validated, authenticated and local-only.
  Default retention is 24 hours. No household image bytes are replicated.
- Deterministic pills/door answers, sensor-only night-door alerts, daily summary, question
  counts and configured-device heartbeat status. Helsinki calendar boundaries and DST.
- Responsive family dashboard served at `/dashboard/`, authenticated same-origin data and
  images, empty/error states, explicit fixtures and no silent demo fallback. No automatic
  polling: use Refresh. The assembled app applies no-store response headers.
- `/speak` uses ElevenLabs MP3 conversion with an 800 ms deadline, bounded in-memory cache,
  duplicate coalescing and concurrency limits. No key/voice or provider failure returns
  503 `speech_unavailable`, allowing Ed's phone to use its own speech fallback.

Configure optional `DATABASE_URL` for an explicitly approved PostgreSQL database. The
schema is created by the replicator; use an isolated test database for tests. Local data
remains authoritative: deleting the local directory is not recoverable automatically
from PostgreSQL. See `deploy/README.md` for storage/auth limitations.

ElevenLabs reads `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, optional
`ELEVENLABS_MODEL_ID` (default `eleven_flash_v2_5`) and `SPEECH_TIMEOUT_SECONDS` (0.8).
Never put keys in the frontend or commit them. Live paid calls were not made.
Gemini and cached navigation adapters exist but remain disabled until explicitly injected
with account/model and navigation policy. Merely setting GEMINI_API_KEY does not enable
one. See `backend/answer_engine/README.md`. Core answers never depend on these adapters.

## Verification

- `make check`: 197 Python tests, lint and generated-schema consistency passed.
- `npm --prefix dashboard run check`: 9 tests passed, including actual HTTP transport and
  the receiver-sensitive browser-fetch regression found during live browser testing.
- Live assembled HTTP acceptance: 5 passed (event → ask → timeline/summary, question
  counting, atomicity/retries/conflicts, sensor-only alert, evidence attachment/auth).
- Disposable PostgreSQL 17.9 integration: 1 passed. An unavailable endpoint queued writes;
  restarting local storage against the real test database drained the queue. Duplicate
  replay after remote commit was idempotent; evidence metadata/expiry replicated.
- Additional API tests verify abrupt process exit/WAL recovery, real uvicorn SSE reconnect,
  evidence-only updates and keepalive, heartbeat transitions, malformed inputs and media.
- Browser: live authenticated API at `/dashboard/`, daily observations/alerts/questions,
  unknown device, empty day, authenticated JPEG, missing-image 404, API outage and recovery,
  reload clears credentials. Desktop 1280px and narrow layout visually inspected; worker
  also checked 390×844 and keyboard dialog behavior. OS network-offline mode was not
  toggled; the actual service-outage path was tested. No real household data used.
- Speech provider behavior tested with mocked HTTP: success/request shape, cache/coalescing,
  timeouts, failures, malformed/truncated audio and missing credentials. Actual voice
  quality and decoder/phone playback remain unverified.

Re-run offline checks with `make check dashboard-check`. For a separate synthetic server:

```sh
JUNCTION_TEST_BASE_URL=http://127.0.0.1:8000 \
JUNCTION_TEST_API_TOKEN=synthetic-local-demo-token make test-http
# This adds acceptance records; use a separate LOCAL_DATA_DIR to keep the demo clean.
TEST_DATABASE_URL='<disposable-postgresql-url>' \
  .venv/bin/python -m pytest -q backend/tests/storage -m integration
```

A known test-only Starlette warning reports deprecated httpx TestClient integration;
all checks pass with the pinned dependency lock. No lock changes were made to silence it.

### Measured local latency

Apple M4, macOS 26.6.2 arm64, Python 3.12.3; loopback HTTP, sequential client, SQLite
WAL/FULL. 20 warmups then 200 samples per endpoint (read returns up to 100 replay records):

| Scenario | p95 write | p95 read |
| --- | ---: | ---: |
| Local-only API | 0.772 ms | 0.993 ms |
| PostgreSQL endpoint unavailable | 1.713 ms | 2.183 ms |

Event benchmarks used stub engine/speech because they measure event endpoints only.
Assembled `/ask`, real rules/storage, 200 samples after 20 warmups: p50 0.992 ms,
p95 1.366 ms, max 4.798 ms. Engine-only benchmark over 1,000 events, 1,000 samples per
intent: pills p95 1.600 ms; door p95 2.063 ms. These measurements do not establish
under-two-second question-to-audible-answer on the phone or performance on UNO Q/Aiven.

## Live integration still required

Read-only Linear handoff refresh of JUN-19, JUN-6, JUN-11 and JUN-20 plus comments found
no change to the agreed source snapshot. Their statuses were not changed. D1–D9 in
`DECISIONS.md` track team decisions. Bartek subsequently confirmed observation-only
medication answers (see the dated decision there); team-wide agreement and the other
release gates remain outstanding.

Ed: confirm SSE upsert/no re-trigger on evidence-only updates, multipart field `file`,
Bearer auth, MP3 response and local speech fallback; time question-to-audible-answer.
Ayman: provide live sensor events/heartbeats and correct clock; agree object names,
confidence and any future confirmed ingestion/lock signal. Current opens prove neither.
Seb: agree stable producer IDs and explicit navigation age/confidence policy before
injecting the cached `/where` adapter. No GPU invocation is added to the fast path.
Bartek/team: agree image locality, HTTPS host/auth and device binding before deployment;
configure and verify Aiven and ElevenLabs separately. No hosted deployment is included.

Suggested next integration order: review shared contract/decisions → Ed/Ayman fast-path
producer and phone playback → live database/TTS timing → optional vision/navigation.
This file is a proposed handoff only; no message has been sent to those teammates.
