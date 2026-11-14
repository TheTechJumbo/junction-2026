# Answer engine

`from backend.answer_engine import RulesAnswerEngine, RulesConfig`

`RulesAnswerEngine()` implements the four synchronous `contracts.interfaces.AnswerEngine`
methods. The API records questions once; this engine does not record questions or mutate
observations. Pass an aware `now` to `answer`. Optionally inject `clock=lambda: aware_now`
for consistent `derive_alerts` / `summarize` tests. All timestamps compare by UTC instant;
presentation, calendar days and night rules use the supplied IANA timezone.

Recognized clinical and dosing questions return a fixed refusal and never call the
optional provider. These are conservative English rules, not exhaustive intent detection.
The optional provider prompt also prohibits diagnosis, treatment and dosing decisions.

Core questions make no network calls and do not import network adapters. A pill box
opening supports only an opening statement. A door opening/closing supports only that
observation, never ingestion or locking. Only configured sensor objects with confidence
at or above the configured threshold support an answer. Defaults: confidence 1, pillbox
objects `pillbox`/`pill_box`/`pill box`, door objects `front_door`/`front door`/`door`.
Configured zones default to kitchen for pills and entrance for the front door; customize
`pillbox_zones` / `door_zones` in RulesConfig for a different installation. Questions
about other people or explicit historical dates return uncertainty.
Wrong zones/objects, vision, voice, null objects, future events and previous local days cannot
support these answers. Freshness is bounded to 24 hours for pills and 12 for doors,
configurable through `RulesConfig`. Named other doors and unsupported historical questions
return uncertainty. The latest observation wins, except simultaneous contradictory door
states return uncertainty with both observations. Same-ID immutable conflicts support
neither observation; identical replays and evidence attachments count once.

Alerts use only configured sensor `door_open` events in the requested local day, up to
`clock()`, and the configurable half-open night interval (default 23:00 through 06:00).
An equal start/end disables that interval; `night_alerts_enabled=False` disables alerts.
A close after an opening does not erase the recorded night opening. Alert IDs are stable
for the rule and event ID. Summaries count eligible sensor openings, supplied eligible
alerts and question records for the requested day; duplicate IDs count once. They do not
claim normality or infer unobserved behavior.

## Optional adapters

Adapters are **disabled by default**. No environment variables are read automatically.
The integrator can inject either adapter through `RulesAnswerEngine(open_adapter=...,
navigation_adapter=...)`. Both return `None` on timeout, busy, malformed, blocked or failed
upstream responses. Engine returns fixed uncertainty text. A single daemon worker per
adapter bounds concurrent requests; repeated calls while busy fail immediately. Network
operations also have a total asyncio deadline, response size limit of 64 KiB, no retries,
no redirects and no environment proxy trust. These are adapter budgets, not a measured
phone speech budget.

- `GeminiOpenAdapter(api_key=..., model=..., timeout_s=0.8)` sends only the open question
  text and fixed system instructions using the documented `generateContent` REST endpoint.
  Configure a model available to the account. It receives no events, imagery, URLs or
  household history. Direct use also rejects pills/door/navigation intents. Blocked and
  truncated model responses fall back. Actual model factual accuracy is not guaranteed.
- `CachedNavigationAdapter(base_url=..., zone=..., token=..., policy=NavigationPolicy(
  max_age_s=..., min_confidence=...), timeout_s=0.4)` performs only authenticated GET
  `/where?zone=...`, using the relay's `X-Key` header. It never calls `/locate`, requests
  images or starts GPU inference. Explicit age/confidence thresholds are required pending
  D7 agreement. It accepts only matching-zone `state=found` fixes with finite current age,
  sufficient confidence and valid waypoint distance/bearing. Missing, stale, unsure or
  lost fixes return uncertainty. Distances are estimates from the upstream map. HTTP is
  limited to loopback origins; other origins require HTTPS. Waypoint aliases can be
  configured; ambiguous or unknown targets return uncertainty.

Network API references verified on 2026-11-14:
[Gemini generateContent](https://aistudio.google.com/api-reference/generate-content/generate-content),
[HTTPX timeouts](https://www.python-httpx.org/advanced/timeouts/).
Relay shape and `X-Key` auth inspected read-only from `origin/jun-22-hud-waypoints`,
`ml/recon_change/live/relay.py` and `ml/recon_change/locate.py`.

## Verification

`make setup && make check` runs offline, credential-free tests. Engine tests additionally
exercise DST transitions, contradictions, freshness, replay, sensor-only truth, summary
counts, adapter failure/deadline and HTTP request shapes. Run
`.venv/bin/python -m backend.answer_engine.benchmark` for a synthetic engine-only latency
measurement; it does not measure database, HTTP, speech or phone end-to-end latency.

Live Gemini, relay/GPU navigation, sensor hardware and audible phone behavior remain
unverified; no credentials, household images or paid requests were used to develop this.
