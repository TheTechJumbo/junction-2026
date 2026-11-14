# Integration control record

Root is the navigator and sole integrator/publisher. Worker branches remain isolated.
A completed code task is not evidence of a completed hardware/service acceptance test.

## Verified worker execution preflight

On 14 November 2026 all three internal agents successfully committed a preflight marker
inside their assigned worktrees without escalation. Existing combined checks passed.
Original separate chat worktrees are preserved; their approval issue is not assumed fixed.
We are using verified internal agents instead of depending on those chats.

| Agent | Branch | Exclusive changes |
| --- | --- | --- |
| API/producer integration | codex/integration-api | backend/app.py, backend/storage/, backend/integrations/, API/storage/integration-adapter tests |
| Speech reconciliation | codex/integration-speech | backend/speech/, backend/tests/speech/ |
| Independent test/review | codex/integration-tests | tests/integration/test_team_*.py, docs/INTEGRATION_REVIEW.md |
| Navigator | codex/bartek-foundation | shared wiring/contracts, dashboard, configuration, documentation and final integration |

Workers report blockers directly to root. Root resolves branch/setup conflicts rather
than leaving agents waiting for human approval. No agent modifies another's branch.
Root reviews commits before cherry-picking and runs assembled checks after integration.

## Completion gates

1. Pin current teammate source revisions and inspect actual wire interfaces.
2. Preserve/reconcile original worker output; verify producer-to-API compatibility.
3. Exercise event ingestion, conservative answers, replay/evidence, dashboard and speech
   together, including dependency failures. Clearly label mock versus live dependencies.
4. Review independent findings and fix defects; retain tests that expose real failure modes.
5. Record live-service/device gates that cannot run without credentials or physical access.
6. Publish reviewed Bartek branch/PR when executable integration checks pass; no merge or
   deployment. Publication does not mean unavailable live gates passed or overall goal done.

## Known integration gap being addressed

Seb relay's actual `post_events` supplies no Authorization header. Authenticated central
API correctly rejects it. Bartek-side launcher adapter will add bounded authenticated
transport without editing Seb's files or weakening API authentication. Relay-relative
image paths cannot be treated as central evidence URLs; they will remain on the relay,
with central events explicitly carrying no attached image until locality/privacy is agreed.

## Preserved original outputs

Speech: codex/speech-recovered fbe4bc8; dashboard: codex/dashboard-recovered 0584eb0;
engine: codex/engine-recovered a46ec53. These are recovery snapshots, not acceptance claims.
