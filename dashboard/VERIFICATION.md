# Dashboard verification · 2026-11-14

- `make setup`: passed in isolated dashboard worktree, credential-free.
- `make check`: ruff passed, 23 foundation tests passed, generated schemas matched.
- `npm --prefix dashboard run check`: JS syntax checks and 9 Node built-in tests passed.
- Real HTTP test uses an ephemeral localhost server, authenticated root `/timeline`,
  `/summary` and PNG evidence, including empty arrays. This is a transport contract test,
  not verification of the production backend.
- `git diff --check`: passed.
- CUA in-app browser at 1280px: disconnected state and explicit synthetic demo reviewed;
  timeline, night alert, repeated questions, unknown sensor and missing images displayed.
- CUA at 390×844: stacked mobile cards and toolbar reviewed. Native Connect dialog
  focused the password field; Escape dismissed it and restored focus to Connect.
- No live backend integration, real sensor, household evidence, Aiven, phone, speech,
  outage/restart or measured end-to-end latency verified by this worker. Integrator must
  check live browser empty/error/offline/evidence paths after API route wiring.

Serving and manual checks: README.md. No deployment or push performed.

- Follow-up: integrator browser exposed native fetch receiver failure before HTTP requests.
  Client now calls fetch with globalThis as receiver; receiver-sensitive regression passes.
  Integrator performs actual browser reconnection verification after cherry-picking patch.

## Integrated verification

Integrator verified the real assembled API on 2026-11-14: authenticated daily data,
empty day, evidence JPEG and missing-evidence 404, server outage/restart recovery and
credential clearing on reload. Browser testing found and fixed the native fetch receiver
bug (commit a457825, integrated as 79898e2); regression test added. Desktop 1280px and
narrow screenshot artifacts are in ignored `.data/`; see `docs/HANDOFF.md` for exact
checks and limitations. Synthetic data only, no hosted or hardware validation.
