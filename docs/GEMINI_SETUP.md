# Optional Gemini answers for open questions

The assembled `make run` entrypoint supports the existing Gemini adapter without
custom Python wiring. Privately export both `GEMINI_API_KEY` and `GEMINI_MODEL` before
starting. The model value must be an explicit provider model ID, without a URL or
`models/` prefix. There is no guessed default model and no availability probe during
startup. Choose a model available to the configured account separately.

Both settings absent or empty leave open questions on the fixed uncertainty response.
Supplying only one setting, a malformed model ID, or an invalid key stops startup
with a sanitized error naming the variables without printing their values. The app
does not automatically load `.env` files; `make run` explicitly loads the private
`.env.local` file as documented in TEAM_INTEGRATION.md.

Only questions classified as open are sent to Gemini. Medication and door questions
continue through deterministic sensor rules; navigation uses the separate optional
cached-relay adapter or its uncertainty response. Gemini and navigation can be enabled
together. Gemini receives the current open question and the adapter's system instruction,
not stored events, photos, earlier questions, or household history. Questions themselves
may contain personal information, so enable this provider only for an intended trial.

The existing adapter has an 800 ms total deadline, a single active-request limit,
no retries, no redirects, and no inherited HTTP proxies. Provider errors, unavailable
models, blocked outputs, invalid responses, busy state, and timeouts produce the fixed
uncertainty response. Live key access, model availability, and provider latency are
not established by mocked integration checks. Medication and door answers do not depend
on the provider's availability.
