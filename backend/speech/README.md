# Optional, bounded speech

The app constructs `ElevenLabsSpeechService.from_env()`, calls
`await synthesize(text)`, and closes its owned service using `await aclose()`.
`contracts.interfaces.SpeechUnavailable` maps to the existing authenticated
`POST /speak` 503 envelope. Successful responses contain MP3 bytes; local phone
speech fallback remains the phone's responsibility.

The integrated constructor remains compatible: explicit `api_key`, `voice_id`,
`model_id`, `timeout_seconds`, injected `client`, and cache/response bounds.
`from_env` reads ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID,
ELEVENLABS_MODEL_ID (default eleven_flash_v2_5), and SPEECH_TIMEOUT_SECONDS
(default 0.8). It does not read the recovered worker's alternative
ELEVENLABS_DEADLINE_MS or ELEVENLABS_OUTPUT_FORMAT settings. Output remains
mp3_44100_128. Missing/blank credentials or a closed service never call upstream.

The overall deadline includes queueing, headers and streamed response bytes.
At most four provider calls and 32 distinct in-flight texts run per instance;
identical concurrent texts share a task. A caller disconnecting does not cancel
other callers' shared synthesis; provider work remains bounded by its own
configured deadline. Shutdown cancels all shared work and clears cached data.
Injected HTTP clients remain caller-owned. Owned clients disable environment
proxies and redirects; requests do not retry or follow redirects.

The memory-only LRU stores at most 32 entries / 8 MiB with a five-minute TTL by
default. Expired entries are removed on the next request; shutdown clears all.
Text/audio are never written to disk. Provider requests necessarily transmit
text to ElevenLabs; this implementation makes no provider-retention guarantee.
No credentials, input text or upstream response bodies appear in public errors.

Responses are limited to 2 MiB by declared length and observed chunks. The MP3
structural gate checks every complete Layer III frame, rejects truncation,
reserved headers and trailing garbage, and requires at least two frames. ID3v2
and trailing ID3v1 tags are supported. This does not decode audio or establish
intelligibility. Other metadata/trailing-padding formats may be rejected; verify
real configured-provider MP3s and playback separately before a live demo.

## Reconciliation and verification

Compared integrated c2a6c4f with recovered fbe4bc8. The recovered adapter's
serialization/config-object API was not substituted: that would break actual
application `from_env` and constructor compatibility and discard explicit shared
in-flight bounds. Useful recovered validation, lifecycle/resource guards and
failure/cancellation/cache/stream test cases were adapted into the integrated
implementation. Existing MIME support remains compatible, with MP3 structure
validated for every accepted MIME.

- Recovered implementation and its original tests, imported in a temporary
  directory without altering the worktree implementation: **48 passed**.
- `.venv/bin/python -m pytest -q backend/tests/speech`: **66 passed**. All provider
  responses mocked, synthetic MPEG frame fixtures only.
- Includes actual `create_app` / authenticated `/speak` success and sanitized 503,
  `from_env`, owned service shutdown / injected-client ownership, queue deadlines,
  malformed declared lengths, stream cleanup, invalid audio, cache bounds/TTL,
  failures not cached, cancellation recovery and closed-service rejection.

No live account/voice validation, ElevenLabs synthesis, phone playback/fallback,
end-to-end audible latency, or clinical interpretation was performed. A structural
MP3 fixture is not a recording or proof of audible speech.
