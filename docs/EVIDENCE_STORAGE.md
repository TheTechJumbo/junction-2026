# Local evidence storage

The central upload endpoint accepts bounded JPEG/PNG or a single multipart file. Multipart
parsing stays in memory even above the parser’s usual disk-spooling threshold. After full
decoding, EXIF orientation is applied and a new RGB image is encoded without source EXIF,
GPS, comments, profiles, or trailing bytes. The normalized output also has a 5 MiB limit.
Metadata removal does not anonymize visible content and is not face redaction.

Images remain in the local SQLite database; PostgreSQL receives event metadata only.
Expiration removes the blob, clears current/replay references and makes download return
404. SQLite secure_delete zeros deleted ordinary-table content, and a TRUNCATE checkpoint
removes historical WAL frames after expiration. A busy external reader defers truncation;
the next cleanup retries, including after application restart. The application uses one
serialized connection. Do not run long external read transactions against this database.

This is application-managed removal from current SQLite files, not forensic erasure of
SSD blocks, filesystem snapshots, backups, copies made by clients, or teammate stores.
Those copies have separate retention responsibilities. A stopped application performs
cleanup when restarted; it cannot delete files while it is not running.

SQLite behavior reference: [secure_delete and wal_checkpoint](https://www.sqlite.org/pragma.html).
Tests use synthetic byte markers and generated images; no household imagery is required.
