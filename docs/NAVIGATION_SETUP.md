# Opt-in cached navigation

`make run` uses `backend.run:create_application`. Navigation is disabled when all five
settings below are absent or empty. Medication and door answers remain deterministic
and never call the relay, whether navigation is configured or not.

Configure all five environment variables before starting the server:

| Variable | Required value |
| --- | --- |
| `VISION_RELAY_URL` | Relay origin only, such as `http://127.0.0.1:8090`; HTTPS required outside loopback |
| `VISION_RELAY_KEY` | The relay's private access key; sent in `X-Key`, never in a URL |
| `VISION_RELAY_ZONE` | Existing relay zone, lowercase letters/digits/underscore/hyphen, 1–32 characters |
| `NAVIGATION_MAX_AGE_SECONDS` | Explicit finite positive maximum age for a cached fix |
| `NAVIGATION_MIN_CONFIDENCE` | Explicit finite confidence threshold in `[0,1]` |

There are no default age or confidence thresholds. Select them for the intended local
trial; neither the environment wiring nor a successful synthetic test establishes safe
navigation thresholds. Partial or invalid configuration stops startup with a sanitized
error that names the settings without printing their values. Shell environment variables
must be exported; the application does not automatically read `.env` files.

The configured adapter requests only `GET /where?zone=...` with a 400 ms total deadline.
It checks the cached fix's state, zone, age and confidence. Missing, stale, lost, invalid
or unavailable fixes return an uncertainty response. It does not call `/locate`, request
a GPU pass, fetch images, follow redirects, or inherit HTTP proxy settings. The relay
must already have a cached position; recording that position remains a separate workflow.

After privately exporting the settings and `API_TOKEN`, run `make run`. An authenticated
`POST /ask` with `{"question":"Where is the bathroom?"}` then uses the cached relay.
`{"question":"Did I take my pills?"}` continues to use only stored sensor observations.
Local HTTP is limited to the adapter's supported loopback hosts (`127.0.0.1`, `::1`,
`localhost`). No hosted deployment or glasses compatibility is implied by this setup.
