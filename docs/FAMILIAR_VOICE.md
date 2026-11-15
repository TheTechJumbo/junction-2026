# Tuttu familiar voice

User-authorized extension to JUN-40. Glasses still use existing `/speak`; this change
adds no Meta activation API or phone microphone integration. Answers are unchanged.
Generated familiar speech identifies itself as the Tuttu assistant, not a live relative.

## Setup

Connect the dashboard with the family API_TOKEN. Familiar voice supports recording
on HTTPS or localhost (microphone permission required) or uploading one audio file.
Use 1–2 minutes of your own clear adult speech. The recording is sent to ElevenLabs
only on **Upload and create my voice**, after explicit own-voice consent.
The backend key needs Text to Speech access and Voices write permission; the account
must support Instant Voice Cloning. Keys never enter the browser.

Preview, listen, explain the automated voice to the wearer, then activate it.
Standard voice remains available; failed familiar synthesis falls back to it, then
returns the normal 503 if that also fails (phone fallback remains phone-owned).
One household and one clone per backend, one uvicorn worker. This is not a multi-tenant
account system. Anyone holding the shared family token can manage the household voice;
device-specific tokens cannot. A production family-role authentication system remains
separate work. Do not reuse the family token as an untrusted device credential.

## API

All routes require family-token authentication. No provider voice IDs are returned.

- GET /family/voice: status, display name, active, previewed, consent time, configured.
- POST /family/voice: bounded multipart `name`, `consent=own-adult-voice-v1`, `file`.
  WebM/WAV/MP3/MP4/Ogg, 10 MiB maximum. Container headers are checked, not decoded;
  ElevenLabs performs final sample validation. A clone starts disabled.
- POST /family/voice/preview: fixed, disclosed sample speech (MP3); records successful generation.
- POST /family/voice/activate: requires ready state and successful preview.
- POST /family/voice/disable: restores configured standard voice.
- POST /family/voice/delete: disables before remote deletion; retry if provider fails.
- POST /family/voice/reconcile: raw body `provider-cleanup-confirmed`. Only for an
  unknown creation with no stored provider ID, after manually removing any created
  voice in ElevenLabs. Known voice IDs must use delete, never local-only reset.

Timeouts/ambiguous creation responses block automatic retries to avoid duplicate
clones. Inspect ElevenLabs and manually clean up before reconciliation. A provider
verification requirement blocks preview/activation; this UI does not bypass or perform
ElevenLabs identity verification. Delete the pending clone here and resolve eligibility
in ElevenLabs before attempting setup again. Definitive 4xx creation failures allow retry.

## Data and lifecycle

Source recordings are held temporarily in browser/server memory; never intentionally
saved on local disk, sent to Aiven, or committed. Provider voice ID, name, consent version
and timestamp, selection and preview status persist in local `familiar-voice.sqlite3`.
ElevenLabs retains data according to its terms; API deletion is not a claim about erasure
from all provider backups. No raw provider errors or credentials are exposed.
Disconnect, page exit and backgrounding stop microphone capture. Disconnect clears
recording/preview URLs. Deletion clears the server clone's audio cache. Activation and
revoke operations serialize with speech, so they wait for an in-flight speech operation.
An already delivered audio response cannot be recalled from a phone.

## Validation and remaining live checks

Automated tests use synthetic bytes/mocked provider responses; they cover consent and
auth, create/preview/activate/disable/delete, restart, verification restrictions,
unknown outcomes, deletion retry and standard fallback. Live clone quality, ownership
verification, plan permissions and audible latency require a consenting adult's actual
recording. No voice has been uploaded or cloned as part of implementation verification.

Provider references checked 2026-11-15:
- https://elevenlabs.io/docs/api-reference/voices/ivc/create
- https://elevenlabs.io/docs/api-reference/voices/delete
- https://elevenlabs.io/docs/eleven-creative/voices/voice-cloning/instant-voice-cloning
