# Bartek implementation instructions

Read coord/PROTOCOL.md, contracts/API.md, docs/FOUNDATION.md and docs/DECISIONS.md.
JUN-19 and its latest comments supersede the historical README product plan.
Bartek owns central API/storage, answer engine, family dashboard and /speak.
Seb owns ml/ and navigation; Ed owns phone/glasses, voice playback and camera gate;
Ayman owns unoq/ and demo/ayman/. Do not edit those owners' directories.

## Working rules
- This foundation is a provisional Bartek-side interface, not a claim of team approval.
- Read Linear through the connected MCP before work and at handoff. Do not require
  a second Linear API key just to duplicate that access. Sending comments or changing
  issues requires user authorization; prepare proposed handoffs locally otherwise.
- An explicit implementation prompt authorizes that task; do not repeatedly ask to start.
- One isolated worktree per worker. Never switch another worker's branch.
- Follow docs/FOUNDATION.md ownership. Shared changes go through the integrator.
- Do not launch workers, merge main, deploy, or create paid resources from this foundation task.
- Never commit credentials, real household imagery, database files or recordings.
- Sensor observations are authoritative about what was observed, not inferred ingestion
  or locking. Vision adds evidence only and never creates alerts.
- Deterministic answers must not depend on GPU, Gemini, ElevenLabs or evidence downloads.
- Run make check before handoff. Include actual commands/results and unverified checks.
- Product acceptance additionally requires database failure/restart tests, browser checks,
  and measured end-to-end latency. Passing contract tests alone is not product completion.
