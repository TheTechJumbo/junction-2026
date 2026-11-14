import os
from datetime import UTC, datetime, timedelta

import pytest

from backend.storage import IdentityConflict, SQLiteStore
from contracts.models import Event


def event(id="event"):
    return Event(id=id, type="pillbox_open", source="sensor", ts="2026-11-14T08:00:00+02:00",
                 zone="kitchen", object=None, evidence_url=None, confidence=1)


def test_restart_preserves_queue_cache_cursor_and_identity(tmp_path):
    path = tmp_path / "events.sqlite3"
    first = SQLiteStore(path, "postgresql://invalid:invalid@127.0.0.1:1/unavailable")
    first.ingest([(event(), {})])
    cursor = first.replay()[1]
    first.record("pills", "pills?", datetime.now(UTC))
    first.heartbeat("uno", datetime.now(UTC))
    assert first.pending_count() == 3
    with pytest.raises(Exception):
        first.replicate_once()
    first.close()
    second = SQLiteStore(path)
    try:
        assert second.get("event") == event()
        assert second.replay(cursor) == ([], cursor)
        assert second.pending_count() == 3
        assert len(second.list_questions(datetime.now(UTC)-timedelta(days=1), datetime.now(UTC))) == 1
        assert second.sensors(["uno"], datetime.now(UTC), 30)[0].status == "online"
        assert second.ingest([(event(), {})]) == [event()]
        with pytest.raises(IdentityConflict):
            second.ingest([(event("new"), {}), (event().model_copy(update={"zone": "other"}), {})])
        assert len(second.replay()[0]) == 1
    finally:
        second.close()


@pytest.mark.integration
def test_real_postgres_outage_recovery_and_restart(tmp_path):
    import psycopg
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL must point to a disposable PostgreSQL database")
    path = tmp_path / "events.sqlite3"
    store = SQLiteStore(path, "postgresql://invalid:invalid@127.0.0.1:1/unavailable")
    store.ingest([(event(), {}), (event("second"), {})])
    with pytest.raises(psycopg.Error):
        store.replicate_once()
    assert len(store.replay()[0]) == 2
    installation = store.installation
    store.close()
    store = SQLiteStore(path, url)
    try:
        assert store.replicate_once() == 2
        assert store.pending_count() == 0
        assert store.replicate_once() == 0
        # Simulate crash after PostgreSQL commit but before local acknowledgment.
        with store.db:
            store.db.execute("UPDATE outbox SET replicated=0")
        assert store.replicate_once() == 2
        with psycopg.connect(url) as remote:
            count = remote.execute("SELECT count(*) FROM bartek_replica WHERE installation=%s",
                                   (installation,)).fetchone()[0]
            assert count == 2
        # Evidence changes replicate metadata only and update the remote current entity.
        store.attach_evidence("event", b"synthetic-test-data", "image/png",
                              datetime.now(UTC) + timedelta(seconds=10))
        assert store.replicate_once() == 1
        with psycopg.connect(url) as remote:
            payload = remote.execute("SELECT payload FROM bartek_entities WHERE installation=%s "
                                     "AND entity_id='event'", (installation,)).fetchone()[0]
            assert payload["evidence_url"].startswith("/evidence/")
            assert "synthetic-test-data" not in str(payload)
        store.expire_evidence(datetime.now(UTC) + timedelta(seconds=20))
        assert store.replicate_once() == 1
        with psycopg.connect(url) as remote:
            assert remote.execute("SELECT payload FROM bartek_entities WHERE installation=%s "
                                  "AND entity_id='event'", (installation,)).fetchone()[0]["evidence_url"] is None
    finally:
        store.close()
        with psycopg.connect(url) as remote:
            remote.execute("DELETE FROM bartek_replica WHERE installation=%s", (installation,))
            remote.execute("DELETE FROM bartek_entities WHERE installation=%s", (installation,))


def test_abrupt_process_exit_preserves_acknowledged_wal(tmp_path):
    import subprocess
    import sys
    path = tmp_path / "events.sqlite3"
    script = """
import os, sys
from backend.storage import SQLiteStore
from contracts.models import Event
store = SQLiteStore(sys.argv[1])
event = Event(id='crash', type='pillbox_open', source='sensor', ts='2026-11-14T08:00:00Z',
              zone='kitchen', object=None, evidence_url=None, confidence=1)
store.ingest([(event, {})])
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", script, str(path)], check=True)
    store = SQLiteStore(path)
    try:
        assert store.get("crash").id == "crash"
        assert store.pending_count() == 1
        assert len(store.replay()[0]) == 1
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
    finally:
        store.close()
