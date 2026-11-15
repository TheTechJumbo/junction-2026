# Independent integration review — 14 November 2026

Implementation base: `e3984a7f7ee4b053476fad3a20c7dd29ed4a31ea`; root assembled the
review tests at `9ac8d8c0adad1ef7bf807fa7319f4b982615ae21` (same tested code).
Independent branch: `codex/integration-tests`, isolated `bartek-engine` worktree.
This review adds tests and documentation only; root owns implementation fixes/integration.

## Findings and disposition

1. **Different room could support the default target (fixed).** On `c2a6c4f`, a sensor
   `pillbox_open` with `object=pillbox, zone=bedroom` supported the default kitchen answer.
   A `door_open` with `object=front_door, zone=bathroom` also raised a front-door night
   alert and inflated its summary. Root `6ac077c` added configured zone checks. Independent
   HTTP regressions now pass for answers, alerts and summaries.
2. **Other person/date/place reused today's default observation (fixed for tested scope).**
   On `c2a6c4f`, questions about a wife's/husband's pills, a bedroom pill box, an explicit
   previous date, last Monday or two days ago returned today's default pill box record.
   Root `6ac077c` / `f52c1ca` added conservative scope guards. All six HTTP reproductions
   now return empty supporting records. These are finite language tests, not validation
   of arbitrary natural-language intent or identification of a household member.
3. **Actual relay egress lacked central credentials (fixed through opt-in wrapper).**
   Unmodified Seb `post_events()` sends only Content-Type and is correctly rejected with
   401 by central API. This remains an explicit baseline test. Root `3aac678` supplies
   the authenticated launcher wrapper; independent execution of the API worker's actual
   relay HTTP tests verifies accepted wrapped egress and redacted failed credentials.
   Launch through the wrapper is required; running Seb's original entry point alone
   does not gain authenticated egress.
4. **Local image origins differ (explicit limitation).** UNO `/artifacts/...` and relay
   `/evidence/job/file.jpg` URLs are local to those services. Actual UNO export and Bartek
   transfer omit image references. Wrapped relay ingestion also clears its image reference.
   These events remain useful observations, but their photos do not appear in the central
   dashboard until a separate approved local attachment is performed. No remote fetch or
   household image transfer was introduced.
5. **Teammate demo clock differs from foundation examples (verified).** Actual Ayman demo
   fixtures use `08:40Z` and `03:10Z`, shown as **10:40** and **05:10 Helsinki** on this date.
   The combined engine, alerts and actual dashboard HTTP client preserve that conversion.

Regressions are normal assertions, with no xfail markers. Initial independent run against
`c2a6c4f` reproduced nine failures; all thirteen scope/observation tests pass after fixes.

## Exact teammate sources exercised

| Source | Immutable revision | Executed files/interfaces |
| --- | --- | --- |
| Ayman UNO local store | `98eb82b773e2986fd2ab8a60be3eb3f2b735fa4a` | `unoq/store.py`: real Store, export, authenticated HTTP handler |
| Ayman synthetic demo | `804b821dd6e51171567310f1ffc1cda32fb3547d` | `demo/ayman/events.json`; API worker also executes `replay.py` |
| Seb relay/navigation | `9aaf1e377e50bd0faeece3c4582a48b17eb976b8` | `ml/recon_change/live/relay.py`: to_events, original post_events, real /where HTTP handler and wrapped /check handler |
| Ed existing rescue branch | `836fa26a84659f8f594b72fbae6d5f0bc6b1e4ce` | Read-only inspection of server/index.js; this branch is a rescue-ping/WebSocket app, not a verified dementia phone integration |
| Recovered original engine | `a46ec53` (`codex/engine-recovered`) | Read-only comparison; its target/date/zone tests helped identify omitted protections |

Remote branch heads were checked read-only with `git ls-remote --heads origin`; pinned
UNO, demo, relay and rescue revisions matched. No teammate branch or files were modified.
Linear JUN-6/JUN-19/JUN-25 discussions were read through the connector; final JUN-19/25
read found no new discussion. No comments,
issue changes, pushes, deployment or live credential use were performed by this reviewer.

## Actual execution versus simulation

| Boundary | Actual code/transport exercised | Simulation and limits |
| --- | --- | --- |
| UNO → export → Bartek | Actual UNO HTTP server → actual `transfer()` → central Uvicorn HTTP server, distinct credentials, repeat transfer, no imagery | All records are synthetic; no UNO board, sensor wiring or USB transport |
| Sensor → answer → timeline/summary | Actual API, SQLite, rules, question persistence/counts, alert derivation | Frozen clock and synthetic observations; no model is involved |
| Vision → API | Actual relay `to_events`, ID-less legacy wire format and strict canonical response | Synthetic report; no GPU inference or geometry accuracy claim |
| Wrapped relay /check → API | Actual relay HTTP handler and authenticated wrapper, real central HTTP | Fake gateway returns synthetic report/image bundle; no SSH/GPU |
| Navigation /ask → /where | Actual relay HTTP cache handler, X-Key auth, configured navigation adapter, real central API; stale-cache fallback | Synthetic cached fix and controlled relay clock; gateway fails test if called; no localization or route accuracy claim |
| Evidence and replay | Real HTTP multipart PNG upload, SSE disconnect/reconnect, updated same event ID, authenticated download, retention clearing old replay URLs | Generated 8×8 PNG only; no phone camera or real household image |
| Dashboard client → API | Actual dashboard/client.js under Node native fetch, actual HTTP daily view and authenticated image Blob, Helsinki clock | Client execution, not browser DOM/visual/device acceptance |
| Answer → /speak | Actual speech service and API routing; verifies answer text, 429→503 and unchanged question count | Provider HTTP transport mocked, two structural synthetic MP3 frames; no decoding/audible speech claim |
| Storage failure/restart | Actual SQLite query_only rejection/recovery; actual refused loopback PostgreSQL connection; actual application restart preserving observation/cursor/outbox | No live Aiven or successful PostgreSQL replication/recovery in this review |

The observation-only requirement holds in the tested path: openings are described as
openings, `pills_taken`/`medication_taken` are not treated as approved ingestion proof,
photos do not alter that conclusion, door states do not prove locking, and vision does
not produce alerts. Speech receives the same conservative answer text. A failed speech
call does not assert that a phone fallback actually ran.

## Commands and results

On macOS 26.6.2 arm64, Python 3.12.3, Node v26.13.0:

- Existing combined preflight `make check`: **197 passed, 6 deselected**, lint/schema check
  passed; empty git metadata preflight commit `c744a2b` succeeded without escalation.
- Final `make check` on `e3984a7` plus this review: **306 passed, 8 deselected**; lint and
  generated-schema parity passed. No expected failures were hidden.
- `node --test dashboard/tests/client.test.mjs`: **10 passed**.
- `REQUIRE_SOURCE_FIXTURES=1 .venv/bin/python -m pytest -q tests/integration/test_team_scope.py tests/integration/test_team_wire.py backend/tests/api/test_actual_producers.py`:
  **25 passed**; real pinned source was available and exercised.
- `git diff --check`: passed.

A Starlette/httpx TestClient deprecation warning remains; it did not fail the tests.
Pinned-source tests skip explicitly if the local Git objects are absent; the API worker's
actual-source acceptance run with REQUIRE_SOURCE_FIXTURES=1 makes their absence a failure.
These tests never implicitly fetch code or silently replace a missing source with a mock.

## Remaining acceptance gates

No successful live Aiven replication/recovery, ElevenLabs generation, Gemini generation,
real-room localization, Arduino sensor event, phone burst capture, glasses UI or audible
question-to-answer timing was verified here. A previously reported engine-only benchmark
is not end-to-end latency. Browser visual checks belong to the separate dashboard
verification record; this review independently exercised the real JavaScript HTTP client.

The local combined flow is reviewable and its tested failure modes are handled. Hardware,
service and <2 s audible-response acceptance remain open and must not be inferred from
these passing tests or from publication of a branch/PR.
