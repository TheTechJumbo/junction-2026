# UNO Q household store

Runs on the Linux side of UNO Q; no MCU firmware or sensors required. Python standard
library only. Sensitive images, baseline frames, depth arrays, point clouds and splats
remain on this device. This service never makes outbound network requests.

## Run and deploy

Development: `UNOQ_TOKEN=<32-or-more-characters> UNOQ_DATA=/tmp/household python3 unoq/store.py`.
Tests: `python3 -m unittest discover -s unoq/tests -v`.

Copy `unoq/` to `/home/arduino/junction/unoq`, then run `sh deploy/install.sh` there.
The installer creates a secret token only if none exists and installs a user service (the board already has lingering enabled for boot startup).
Use `systemctl --user restart junction-store` after deploying updates. To stop/remove
startup behavior: `systemctl --user disable --now junction-store`. Data stays intact.
Data and models belong under `/home/arduino`, which has its own larger partition.
Keep real data, tokens, model weights and recordings out of Git and Linear.

Default binding is **127.0.0.1:8091**. For USB access run `adb forward tcp:8091 tcp:8091`.
Read the token privately from `~/.config/junction/store.env` on the board. Every request
needs `Authorization: Bearer TOKEN`; do not put tokens in URLs. Responses are no-store.
No browser CORS access is enabled. Ed's app must call this API through its native client
or a trusted local proxy. For LAN use, provide a local TLS reverse proxy/SSH tunnel;
do not expose bearer-authenticated plain HTTP to the venue network or public internet.
The service deliberately doesn't install that integration or change other owners' apps.

## Local API contract for Bartek and Ed

- `POST /events`: v3 JSON `{id,type,source,ts,zone,object,evidence_url:null,confidence}`.
  Source is sensor/vision/voice, ts is timezone-aware ISO 8601, confidence is 0..1.
  Identical IDs are idempotent; conflicting reuse returns 409. Type/zone/object are text.
- `GET /events` and `GET /timeline`: `{events:[Event]}` in ingestion order.
- `POST /events/{id}/evidence`: **raw JPEG/PNG body**, not multipart, returns
  `{id,evidence_url:"/artifacts/..."}` and attaches the local URL to the event.
  Coordinate this explicit difference from Ed's earlier multipart proposal.
- `POST /artifacts`: raw JPEG, PNG, ZIP or octet-stream for baselines/geometry/model outputs.
  Returns a local artifact handle. No filename or external URL is accepted.
- `GET /artifacts/{id}`: authenticated bytes. URLs are relative to this local service.
- `GET /export/events`: explicit allowlist of v3 scalar fields with evidence_url always null.
  Bartek may pull/forward this derived text data. There is **no automatic cloud sync**.
- `GET /health`: database probe, free storage bytes, inference availability. Inference is
  unavailable until a full local pipeline has been validated; benchmark success alone
  does not enable a production inference endpoint.

Bodies are limited to 32 MiB, chunked uploads are rejected, incomplete uploads are not
committed. Missing artifact/event = 404, invalid JSON/schema = 400, storage reserve hit
= 507, storage errors = 503. No request content is logged. File permissions are owner-only;
this is not disk encryption and does not defend against root or physical extraction.

Artifacts expire 24 hours after ingestion (`UNOQ_RETENTION_SECONDS` overrides), cleaned
at startup, on requests and every minute. Event metadata remains; expired evidence URLs
are cleared. Expired model inputs and outputs should be uploaded as artifacts too.
SQLite recovery handles committed events after restart; orphan files are removed on
startup. The 128 MiB free-space reserve prevents large uploads consuming the last disk
space. Do not write private temporary files outside the artifact directory. Data already
exported to another service has that service's separate retention policy.

## VGGT feasibility

`python3 unoq/vggt_probe.py` reports runtime/dependency availability without downloading
weights or opening images. Current reference: `facebook/VGGT-1B`, from Seb's
`origin/jun-22-vision-stage`. That runner calls CUDA directly and cannot run unchanged
on the Adreno GPU. This probe provides an isolated CPU route, not a claim of GPU support.

Only after ARM dependencies fit, prepare an isolated environment under `/home/arduino`
with PyTorch, Pillow, NumPy, safetensors and upstream VGGT. Pin the selected versions
and record them with benchmark results; never replace system Python. Use local
safetensors weights (no pickle checkpoint execution) and public/synthetic JPEGs:

`python3 unoq/vggt_probe.py --weights /local/model.safetensors --frames /local/public-frames --count 2`

The subprocess has a 120 s timeout and at most 2800 MiB address space. It refuses
weights without 768 MiB remaining RAM. Progress to 3 and 9 frames only if smaller
runs pass. Record valid outputs, wall time and RSS; a complete change test needs at
least 6 before + 3 after frames. A two-frame probe does not validate change detection.
Do not turn a failed probe into a remote GPU upload. If it fails, keep storage available
and report the precise blocker. Gaussian splatting remains deferred until full VGGT
works; assess Seb's CPU splat branch afterward rather than inventing another GPU path.
