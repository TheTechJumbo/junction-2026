# Foundation and worker handoff

Base: 49e019e568930bfd7c021cc2acf9360fa86853be (origin/main after fetch).
Branch: codex/bartek-foundation. Use this branch's completed foundation commit as the
common worker base, not the historical main commit alone. Implementation has since completed in isolated worktrees; see HANDOFF.md.

## Ownership
| Worker | Exclusive files |
| --- | --- |
| API/storage | backend/app.py, backend/routes/, backend/storage/, backend/migrations/, backend/tests/api/, backend/tests/storage/, deploy/ |
| Answers | backend/answer_engine/, backend/tests/answer_engine/ |
| Dashboard | dashboard/ (standalone static client and browser checks) |
| Speech | backend/speech/, backend/tests/speech/ |
| Integrator | contracts/, tests/contracts/, tests/integration/, AGENTS.md, root README, docs/, manifests/lock, Makefile, .env.example, .gitignore, final route wiring |

API worker may propose dependency additions; integrator applies shared manifest changes.
No worker edits unoq/, ml/, demo/ayman/, phone/glasses code, or coord/.

## Setup
Python 3.11+ (foundation verified on 3.12). Run `make setup` then `make check`.
The lock pins the development environment; pyproject defines supported direct ranges.
Create a private .env from .env.example only when needed; ignored secrets do not
automatically transfer into worker worktrees. No API credentials are needed for checks.
The assembled application now runs with make run; see HANDOFF.md for environment setup.
For changed schemas run `make schemas`, inspect the diff, then `make check`.

## Test gates
- Foundation: strict event validation, negative fixtures, JSON Schema parity, daylight
  saving/day boundaries, and all response fixture shapes. Offline and credential-free.
- API: real isolated PostgreSQL plus SQLite outage/restart/replay, producer compatibility,
  auth, atomic batches, stream reconnect and evidence updates. Never test on real data.
- Answers: semantic correctness and no dependency on vision/model/TTS for core questions.
- Dashboard: browser inspection and real HTTP integration, not fixture screenshots alone.
- Speech: mocked failures plus a separately authorized live test with configured key.
- Integration: event -> ask -> timeline, questions -> counts, night-door alert, GPU off,
  delayed photo, DB failure and end-to-end audible timing with Ed.
Use pytest integration/live markers for external tests and report those separately;
`make check` deliberately does not claim to run unavailable services.

## Integration order
1. Foundation commit and unresolved-decision review.
2. API, answers, dashboard and optional speech in isolated worktrees from that commit.
3. API/storage, then engine route wiring, then dashboard, then speech.
4. Ed/Ayman producer integration and Seb's optional adapters after contract agreement.
5. Integration tests, latency measurement and reviewable handoff. No automatic main merge.

## Fixture use
contracts/fixtures/ are synthetic, never genuine patient observations. events.json uses
Helsinki offsets: 08:00 local pills-box opening, 08:40 repeat, 03:10 night door opening.
There is no confirmed-medication or confirmed-lock fixture. Vision example provides no
ground truth. legacy-vision.json deliberately differs from the canonical schema and is
adapter input only. Timeline/answer examples describe contract shapes; the assembled app is tested separately.

The answer fixture is for a question after the first opening and before the repeat;
its evidence subset is not an answer over the entire daily timeline.

## Verified foundation checks (2026-11-14)
- make check: lint passed, 23 tests passed, generated schemas match.
- Fresh temporary virtual environment: pinned install and editable package install passed;
  23 tests passed, schema check passed, pip check reported no broken requirements.
- git diff --check passed; .env, .env.local, .venv and .data are ignored.
- No live database, ElevenLabs, phone, sensor or browser integration was claimed.
