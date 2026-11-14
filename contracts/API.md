# Shared API contract: foundation v0

Status: provisional implementation contract for Bartek's workers, 2026-11-14.
Existing team agreement is the JUN-6 v3 Event vocabulary and five core endpoints.
Additional decisions below are local proposals, not approvals from Ed/Seb/Ayman.
Python schemas: models.py. Generated JSON Schema: schemas/. Internal ports: interfaces.py.
Product routes are implemented locally; see docs/HANDOFF.md for verification and limits.

## Data and meaning
Event has id, type, source (sensor|vision|voice), timezone-aware ts, zone, nullable
object, nullable evidence_url, finite confidence in [0,1]. No kind/label/pos_3d.
Canonical responses contain exactly these fields. Unknown canonical fields are rejected.
Alerts and question logs are separate records; do not introduce source=rules.
pillbox_open means opening, not ingestion. door_open/door_closed do not prove locking.
No confirmed medication/lock event type is approved yet. Vision never creates alerts.
Use configured Europe/Helsinki day boundaries and half-open UTC query intervals;
sort event presentation by (ts,id), replay by durable ingestion cursor instead.

## HTTP routes (provisional details)
All data routes require authentication. GET /health is a minimal public liveness probe;
/dashboard/ serves static code without embedding household data or credentials. Bind locally
for development; keep tokens server-side or in an explicit client session, never URLs.
Deployment authentication and phone/browser transport need agreement (D5).

| Method/path | Request | Success |
| --- | --- | --- |
| POST /events | Event or nonempty array, max 100 events | 200 IngestResponse |
| GET /events | cursor optional; since optional aware timestamp; limit 1..100 default 100 | 200 EventPage |
| GET /events/stream | Last-Event-ID or cursor | text/event-stream |
| POST /events/{id}/evidence | raw JPEG/PNG or multipart field file | 200 EvidenceResponse |
| POST /ask | AskRequest | 200 Answer |
| GET /timeline | day YYYY-MM-DD, default local today | 200 Timeline |
| GET /summary | day YYYY-MM-DD, default local today | 200 Summary |
| POST /speak | SpeakRequest | 200 audio/mpeg bytes |
| POST /heartbeat | HeartbeatRequest | 200 SensorStatus |

Ingestion is atomic per batch. Validate the entire batch before writes. Retried identical
producer IDs are no-ops; conflicting immutable producer data returns 409. Later attached
evidence is not part of retry conflict comparison. Acknowledgment requires durable local
commit; return durability=local until PostgreSQL replication is confirmed. If neither
local nor remote durable acceptance works, return 503; never acknowledge lost data.
Reads include accepted local writes during database outage.

Legacy vision adapter at POST /events: accept missing id ONLY for source=vision,
nullable object, and optional size_cm. Derive a stable ID using SHA-256 of canonical
producer JSON (sorted keys, compact separators) plus zero-based batch index. Preserve
size_cm only in internal metadata. Retries must reuse exact payload/order. Different
clusters with identical content in different submissions remain ambiguous (D6).
Do not relax the canonical response schema. Never fetch a producer evidence URL
automatically; remote household image handling is blocked by D2.

EventPage.next_cursor is the last durable ingestion sequence processed (opaque string).
When no new records exist, retain the supplied cursor. since is inclusive event time
and only bootstraps polling; use cursor thereafter to avoid losing delayed events.
Supplying both cursor and since returns 400. Clients deduplicate by Event.id.
Evidence updates create a new replay entry for the same Event.id. SSE id is the replay
cursor, event is 'event', data is one canonical Event. Replay on reconnect, then tail;
periodic SSE comments keep the connection alive. Expired cursor returns 410 requiring
a fresh timeline. Stream clients upsert by id and do not retrigger photo capture on
evidence-only updates. Ed must confirm this behavior before phone integration.

Timeline includes events, baseline:null, alerts, questions, sensors, day and timezone.
Questions group by normalized intent, with count and last_ts. Empty arrays are valid.
Sensor heartbeat uses server receipt time, expected interval 10 s, provisional offline
threshold >30 s; configured devices with no heartbeat are unknown. Credential-to-device
binding is required before deployment. Heartbeats do not create patient events.
Night door rule default is [23:00,06:00) local time, sensor door_open only; configure it
rather than mixing this with vision changes. Deterministic alert IDs prevent duplicates.

Evidence: local-only storage until D2 resolved; reject unsupported deployment with 503
evidence_unavailable. Limit uploads to 5 MiB and verify decoded file type. Local artifact
URLs remain authenticated. No server fetch of arbitrary URLs. Retention default 24 h;
clear stale evidence references. Synthetic fixtures have no actual images.

/ask returns text without waiting for TTS or image access. API records classification
once per request using QuestionLog; answer engine is pure for pills/door. No evidence
means uncertainty, not a fabricated yes/no. Optional Gemini/navigation adapters have
bounded deadlines and cannot affect deterministic intent latency. Cached /where must
check state/age_s; do not call /locate. Navigation thresholds await D7.
/speak returns actual MP3 bytes or 503 speech_unavailable; no fake audio or server-side
claim that phone fallback ran. Proposed speech timeout 800 ms, pending Ed's acceptance.
No voice cloning. Voice/model choice is configured and must be verified against current
ElevenLabs docs by the speech worker before live integration.

Errors use ErrorResponse: 400 invalid input, 401 missing/invalid authentication,
404 missing resource, 409 identity conflict, 410 expired cursor, 413 too large,
415 unsupported media, 503 dependency/durability unavailable. Map framework validation
errors to this envelope; never return secrets or raw upstream errors.

## Required acceptance beyond foundation tests
Under 200 ms event reads/writes (report hardware, sample count and p95); under 2 s
question-to-audible-answer jointly with Ed, with vision off. Test durable queue restart,
database outage/recovery, batch atomicity, retries after evidence attachment, replay
with equal/late timestamps, evidence update delivery and heartbeat transitions.
Dashboard tests cover live API, time display, offline/error/empty and unavailable images.
Live Aiven, ElevenLabs and phone checks must be explicitly reported as verified or not.
