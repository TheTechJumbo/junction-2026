"""SQLite is the durable read cache and outbox; PostgreSQL never blocks acceptance.

Use one local directory per installation. The installation UUID makes replay cursors
and replicated records independent even when several installations share PostgreSQL.
Evidence bytes deliberately stay local; the outbox only contains canonical metadata.
"""
import base64
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from contracts.models import Event, QuestionRecord, SensorStatus


class IdentityConflict(Exception):
    pass


class MissingEvent(Exception):
    pass


class InvalidCursor(Exception):
    pass


class CursorExpired(Exception):
    pass


def dump(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def stamp(value):
    return value.astimezone(UTC).isoformat()


class SQLiteStore:
    def __init__(self, path: str | Path, database_url: str | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        self.database_url = database_url
        self._lock = threading.RLock()
        self._replication_lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
        self.path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, producer TEXT NOT NULL, event TEXT NOT NULL,
                ts TEXT NOT NULL, metadata TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS replay (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL, ts TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS outbox (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
                entity_id TEXT NOT NULL, payload TEXT NOT NULL, replicated INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS evidence (
                id TEXT PRIMARY KEY, event_id TEXT NOT NULL, data BLOB NOT NULL,
                media_type TEXT NOT NULL, expires TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS questions (
                id TEXT PRIMARY KEY, intent TEXT NOT NULL, question TEXT NOT NULL, ts TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS heartbeats (id TEXT PRIMARY KEY, last_seen TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_time ON events(ts,id);
            CREATE INDEX IF NOT EXISTS questions_time ON questions(ts,id);
            CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(replicated,seq);
            CREATE INDEX IF NOT EXISTS evidence_expiry ON evidence(expires);
        """)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('installation', ?)",
                            (str(uuid4()),))
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('replay_floor', '0')")
        self.installation = self.db.execute(
            "SELECT value FROM metadata WHERE key='installation'").fetchone()[0]

    def close(self):
        with self._lock:
            self.db.close()

    def _enqueue(self, kind, entity_id, payload):
        self.db.execute("INSERT INTO outbox(kind,entity_id,payload) VALUES (?,?,?)",
                        (kind, entity_id, dump(payload)))

    def _replay(self, event):
        self.db.execute("INSERT INTO replay(event,ts) VALUES (?,?)",
                        (event.model_dump_json(), stamp(event.ts)))
        self._enqueue("event", event.id, event.model_dump(mode="json"))

    def ingest(self, items: list[tuple[Event, dict]]):
        """Validate identity for all items inside one transaction, including duplicate IDs."""
        with self._lock, self.db:
            output = []
            for event, metadata in items:
                producer = dump({"event": event.model_dump(mode="json"), "metadata": metadata})
                existing = self.db.execute("SELECT * FROM events WHERE id=?", (event.id,)).fetchone()
                if existing:
                    if existing["producer"] != producer:
                        raise IdentityConflict()
                    output.append(Event.model_validate_json(existing["event"]))
                    continue
                self.db.execute("INSERT INTO events VALUES (?,?,?,?,?)", (
                    event.id, producer, event.model_dump_json(), stamp(event.ts), dump(metadata)))
                self._replay(event)
                output.append(event)
            # Returning from the with still commits before control reaches the caller.
            return output

    def get(self, event_id):
        with self._lock:
            row = self.db.execute("SELECT event FROM events WHERE id=?", (event_id,)).fetchone()
            if not row:
                raise MissingEvent()
            return Event.model_validate_json(row[0])

    def list_events(self, start, end):
        with self._lock:
            rows = self.db.execute("SELECT event FROM events WHERE ts>=? AND ts<? ORDER BY ts,id",
                                   (stamp(start), stamp(end))).fetchall()
            return [Event.model_validate_json(row[0]) for row in rows]

    def encode_cursor(self, seq):
        return base64.urlsafe_b64encode(f"{self.installation}:{seq}".encode()).decode().rstrip("=")

    def decode_cursor(self, cursor):
        if cursor is None:
            return 0
        try:
            installation, number = base64.b64decode(
                cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True
            ).decode().rsplit(":", 1)
            seq = int(number)
            if seq < 0:
                raise ValueError()
        except (ValueError, UnicodeError) as exc:
            raise InvalidCursor() from exc
        if installation != self.installation:
            raise CursorExpired()
        return seq

    def replay(self, cursor=None, since=None, limit=100):
        with self._lock:
            seq = self.decode_cursor(cursor)
            floor = int(self.db.execute(
                "SELECT value FROM metadata WHERE key='replay_floor'").fetchone()[0])
            if cursor is not None and seq < floor:
                raise CursorExpired()
            maximum = self.db.execute("SELECT seq FROM sqlite_sequence WHERE name='replay'").fetchone()
            maximum = maximum[0] if maximum else 0
            if seq > maximum:
                raise InvalidCursor()
            if since is None:
                rows = self.db.execute("SELECT * FROM replay WHERE seq>? ORDER BY seq LIMIT ?",
                                       (seq, limit)).fetchall()
            else:
                rows = self.db.execute(
                    "SELECT * FROM replay WHERE seq>? AND ts>=? ORDER BY seq LIMIT ?",
                    (seq, stamp(since), limit)).fetchall()
            next_seq = rows[-1]["seq"] if rows else (maximum if since else seq)
            # Snapshot replay payload preserves each update; expired evidence is never exposed.
            entries = [(self.encode_cursor(row["seq"]), Event.model_validate_json(row["event"]))
                       for row in rows]
            return entries, self.encode_cursor(next_seq) if rows or since else cursor

    def prune_replay(self, through_cursor):
        with self._lock, self.db:
            seq = self.decode_cursor(through_cursor)
            self.db.execute("DELETE FROM replay WHERE seq<=?", (seq,))
            self.db.execute("UPDATE metadata SET value=? WHERE key='replay_floor'",
                            (str(seq),))

    def attach_evidence(self, event_id, data, media_type, expires):
        with self._lock, self.db:
            event = self.get(event_id)
            evidence_id = uuid4().hex
            url = f"/evidence/{evidence_id}"
            self.db.execute("INSERT INTO evidence VALUES (?,?,?,?,?)",
                            (evidence_id, event_id, data, media_type, stamp(expires)))
            updated = event.model_copy(update={"evidence_url": url})
            self.db.execute("UPDATE events SET event=? WHERE id=?",
                            (updated.model_dump_json(), event_id))
            self._replay(updated)
            return url

    def expire_evidence(self, now):
        with self._lock, self.db:
            rows = self.db.execute("SELECT id,event_id FROM evidence WHERE expires<=?",
                                   (stamp(now),)).fetchall()
            for row in rows:
                url = f"/evidence/{row['id']}"
                event = self.get(row["event_id"])
                if event.evidence_url == url:
                    event = event.model_copy(update={"evidence_url": None})
                    self.db.execute("UPDATE events SET event=? WHERE id=?",
                                    (event.model_dump_json(), event.id))
                    self._replay(event)
                # Historical replay must not resurrect stale references after an expiry update.
                old = self.db.execute("SELECT seq,event FROM replay WHERE event LIKE ?",
                                      (f'%{url}%',)).fetchall()
                for entry in old:
                    previous = Event.model_validate_json(entry["event"])
                    if previous.evidence_url == url:
                        previous = previous.model_copy(update={"evidence_url": None})
                        self.db.execute("UPDATE replay SET event=? WHERE seq=?",
                                        (previous.model_dump_json(), entry["seq"]))
                self.db.execute("DELETE FROM evidence WHERE id=?", (row["id"],))

    def get_evidence(self, evidence_id, now):
        self.expire_evidence(now)
        with self._lock:
            row = self.db.execute("SELECT data,media_type FROM evidence WHERE id=?",
                                  (evidence_id,)).fetchone()
            if not row:
                raise MissingEvent()
            return bytes(row[0]), row[1]

    def record(self, intent, question, ts):
        item = QuestionRecord(id=uuid4().hex, intent=intent, question=question, ts=ts)
        with self._lock, self.db:
            self.db.execute("INSERT INTO questions VALUES (?,?,?,?)",
                            (item.id, intent, question, stamp(ts)))
            self._enqueue("question", item.id, item.model_dump(mode="json"))
        return item

    def list_questions(self, start, end):
        with self._lock:
            rows = self.db.execute("SELECT * FROM questions WHERE ts>=? AND ts<? ORDER BY ts,id",
                                   (stamp(start), stamp(end))).fetchall()
            return [QuestionRecord.model_validate(dict(row)) for row in rows]

    def heartbeat(self, device_id, now):
        with self._lock, self.db:
            self.db.execute("INSERT INTO heartbeats VALUES (?,?) ON CONFLICT(id) DO UPDATE "
                            "SET last_seen=excluded.last_seen", (device_id, stamp(now)))
            self._enqueue("heartbeat", device_id, {"id": device_id, "last_seen": stamp(now)})
        return SensorStatus(id=device_id, last_seen=now, status="online")

    def sensors(self, configured, now, offline_seconds):
        with self._lock:
            rows = dict(self.db.execute("SELECT id,last_seen FROM heartbeats").fetchall())
        result = []
        for device_id in sorted(set(rows) | set(configured)):
            last = datetime.fromisoformat(rows[device_id]) if device_id in rows else None
            status = "unknown" if last is None else (
                "offline" if (now - last).total_seconds() > offline_seconds else "online")
            result.append(SensorStatus(id=device_id, last_seen=last, status=status))
        return result

    def pending_count(self):
        with self._lock:
            return self.db.execute("SELECT count(*) FROM outbox WHERE replicated=0").fetchone()[0]

    def replicate_once(self, limit=100):
        """At least once delivery with idempotent PostgreSQL commit, then local ack.

        An interrupted remote commit is safe to repeat. Exceptions deliberately propagate
        to the worker, which retries on its next tick without affecting local reads.
        """
        if not self.database_url:
            return 0
        import psycopg
        from psycopg.types.json import Jsonb
        with self._replication_lock:
            with self._lock:
                rows = self.db.execute("SELECT * FROM outbox WHERE replicated=0 ORDER BY seq LIMIT ?",
                                       (limit,)).fetchall()
            if not rows:
                return 0
            with psycopg.connect(self.database_url, connect_timeout=2) as remote:
                remote.execute("SET statement_timeout = '2000ms'")
                remote.execute("""CREATE TABLE IF NOT EXISTS bartek_replica (
                    installation TEXT NOT NULL, seq BIGINT NOT NULL, kind TEXT NOT NULL,
                    entity_id TEXT NOT NULL, payload JSONB NOT NULL,
                    PRIMARY KEY(installation,seq))""")
                remote.execute("""CREATE TABLE IF NOT EXISTS bartek_entities (
                    installation TEXT NOT NULL, kind TEXT NOT NULL, entity_id TEXT NOT NULL,
                    seq BIGINT NOT NULL, payload JSONB NOT NULL,
                    PRIMARY KEY(installation,kind,entity_id))""")
                for row in rows:
                    values = (self.installation, row["seq"], row["kind"], row["entity_id"],
                              Jsonb(json.loads(row["payload"])))
                    remote.execute("INSERT INTO bartek_replica VALUES (%s,%s,%s,%s,%s) "
                                   "ON CONFLICT DO NOTHING", values)
                    remote.execute("""INSERT INTO bartek_entities
                        (installation,seq,kind,entity_id,payload) VALUES (%s,%s,%s,%s,%s)
                        ON CONFLICT (installation,kind,entity_id) DO UPDATE
                        SET seq=excluded.seq,payload=excluded.payload
                        WHERE bartek_entities.seq < excluded.seq""", values)
            with self._lock, self.db:
                self.db.executemany("UPDATE outbox SET replicated=1 WHERE seq=?",
                                    [(row["seq"],) for row in rows])
            return len(rows)
