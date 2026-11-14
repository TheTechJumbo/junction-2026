# Open decisions and source snapshot

Read on 2026-11-14: JUN-19, JUN-6, JUN-11, JUN-20, JUN-12, JUN-13 and all comments.
References: https://linear.app/junction26/issue/JUN-19 (brief), /JUN-6 (contracts),
/JUN-11 (storage), /JUN-20 (answers), /JUN-12 (speech), /JUN-13 (UNO Q handoff).
Earlier inspection also covered JUN-15 and JUN-25 and their comments.
No agreement or status update was sent to Linear by this foundation task.

| ID | Unresolved | Owners | Working default / release gate |
| --- | --- | --- | --- |
| D1 | Sensors deferred in Ayman's handoff despite v3 fast path | Ayman, Ed, Bartek | Synthetic sensor fixtures only; no claim of live sensor demo |
| D2 | Images local-only versus remote evidence/dashboard and vision | All | No household cloud image upload; authenticated local artifacts; remote photos unverified |
| D3 | What proves pills taken or door locked? | Ayman, Ed, Bartek | No mapping from open/closed to completion; honest opening/unknown answers |
| D4 | Raw local uploads versus Ed's multipart proposal | Ed, Ayman, Bartek | Central adapter accepts both; do not change unoq/ |
| D5 | HTTPS host, auth, heartbeat device binding | Bartek, Ed, Ayman | Local development; heartbeat request/30 s threshold provisional; deployment blocked |
| D6 | Stable vision producer identity and batch retry | Seb, Bartek | Deterministic legacy adapter; producer IDs preferred; identical-cluster ambiguity remains |
| D7 | Navigation confidence/freshness policy | Seb, Ed, Bartek | Disabled until configured; stale/lost returns uncertainty; no GPU request in answer path |
| D8 | Speech format/voice/latency budget | Ed, Bartek | Propose MP3 and 800 ms timeout, phone local TTS fallback; confirm before live demo |
| D9 | Board clock incorrect and timezone expectations | Ayman, Bartek | Fixture times explicitly Helsinki; retain device ts and server receipt separately |

JUN-19 newest comment narrows to dementia and adds cached /where navigation. It does
not restore Parkinson's, profile switching, /frames or /prompts. JUN-6 explicitly
withdraws Ed's earlier four additions. JUN-11 requests Aiven and a local queue.
JUN-12 requires phone speech fallback and <2 s end-to-end.
JUN-13 latest update reports 271 s two-image quantized inference on UNO Q (finite
outputs), not geometry accuracy, sensor availability or fast-path readiness.

Existing unmerged code inspected:
- jun-13-local-household-store @98eb82b: unoq/ SQLite/artifacts, strict Event input,
  raw evidence upload, export strips evidence, no outbound sync.
- jun-22-cpu-splat-ui @b3da5d5: vision relay sends ID-less arrays, object:null,
  extra size_cm; dashboard there is operational, not family-facing.
- jun-22-hud-waypoints @de1a670: cached /where with age_s/state and authenticated images.
- jun-24-demo-kit @804b821: synthetic opening events, no medication/lock proof.
- jun-13-quantized-vggt: new remote branch on refresh; outside Bartek's scope.

These observations are snapshots, not authorization to merge other owners' branches.
Worker contracts are provisional; only integrator changes shared types and examples.
