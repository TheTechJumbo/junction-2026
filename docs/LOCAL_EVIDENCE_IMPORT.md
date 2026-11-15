# Deliberate local evidence attachment

This optional command attaches exactly one operator-selected local JPEG or PNG to an
existing central event. It does not inspect directories, retrieve teammate artifacts,
fetch URLs, create events, or copy images to a cloud service.

After privately exporting `API_TOKEN`, run:

```
.venv/bin/python -m backend.integrations.attach_local_evidence \
  --target http://127.0.0.1:8000 \
  --event-id synthetic-demo-pills \
  --image /absolute/path/to/selected-synthetic-image.jpg
```

The event must already exist, for example after the separate metadata import command.
The command validates the image format, a 5 MiB limit, and a 16 megapixel limit before
decoding pixels. It checks for that event using
authenticated event pagination, and uploads only after a match. It never creates an
observation to make an image fit. The endpoint applies its normal image privacy and
retention policy; the command does not bypass it.

Targets must be literal loopback IP origins, such as `127.0.0.1` or `[::1]`, with HTTP
or HTTPS. Hostnames, LAN addresses, cloud endpoints, credentials in URLs, redirects,
and inherited HTTP proxies are rejected or disabled. The token is accepted only from
the environment. Symbolic links and nonregular files are rejected. No upload retries
occur automatically. A failed or malformed receipt can follow a successful server
commit: inspect the event before deliberately retrying.

GET requests retain a two-second read timeout and ten-second lookup budget. Uploads
have a separate fifteen-second receipt budget and twelve-second read timeout to allow
the endpoint's bounded face-processing step. After upload, the command scans the event
feed through its latest entries with a separate ten-second budget and checks that the
requested event references the returned local image URL. An older matching replay entry
is insufficient if a newer entry references a different image. A failed readback reports
uncertainty and does not repeat the upload.

The same event ID receives an evidence update, which the dashboard obtains through
its normal polling/SSE updates. Images remain authenticated and expire under the
central API retention policy. An attachment is a supporting record, not proof of
medication ingestion, locking, or another completed action.

The bounded preflight searches the durable event feed. If old replay records have
been deliberately pruned, an older event may not be discoverable by this command;
it refuses the upload instead of inventing an event or bypassing the preflight.
