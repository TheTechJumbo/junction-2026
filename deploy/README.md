# Local synthetic development only

Run `make setup`, configure an explicit `API_TOKEN` and optionally `DATABASE_URL`, then:

```
.venv/bin/uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8000
```

Keep `.data/` on persistent local storage. SQLite WAL with FULL synchronous commits is
the acceptance boundary and read cache; the background outbox worker replicates to
PostgreSQL without delaying ingestion. Restart with the same directory to retain
queue, replay cursors, questions and evidence. A single installation can run one API
process; do not share its data directory across hosts or network filesystems.

A PostgreSQL outage is safe while local disk is writable. PostgreSQL is an append-only
replica and current-entity mirror, not automatic recovery from deletion of the local
directory. Back up local storage with a consistent SQLite backup, including local-only
evidence. PostgreSQL rows are scoped by the local installation UUID. Image bytes never
leave SQLite. Run retention maintenance while the process is up; reads also expire
references before returning them. Expired blobs are logically deleted; encrypted local
disks are required for protection from forensic recovery of old database pages/backups.

All data routes require `Authorization: Bearer <token>`. `/health` reveals only liveness.
Use `ALLOWED_ORIGINS` for an explicit comma-separated local dashboard origin allowlist;
tokens stay in an explicit client session. Device tokens can be supplied through the
Settings object to bind heartbeat credentials to device IDs. The shared development
token intentionally does not establish production device identity.

Hosted deployment remains blocked on D2/D5 agreement: household image handling, HTTPS,
authentication and device binding. This directory does not provision or deploy services.
