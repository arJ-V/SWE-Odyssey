# Attaching a database READ_ONLY fails when its WAL contains index data

A database whose write-ahead log still holds an index cannot be opened
read-only: replaying the log tries to write into the database file.

```sql
-- session 1: duckdb /tmp/ro_index.db
PRAGMA disable_checkpoint_on_shutdown;
SET wal_autocheckpoint='1TB';
CREATE TABLE t(id INTEGER PRIMARY KEY, grp INTEGER);
INSERT INTO t VALUES (1, 10), (2, 20), (3, 10);
CREATE INDEX idx_grp ON t(grp);
.quit

-- session 2: duckdb (in memory)
ATTACH '/tmp/ro_index.db' AS ro_db (READ_ONLY);
-- IO Error: Failure while replaying WAL file "/tmp/ro_index.db.wal":
--   Could not write file "/tmp/ro_index.db": Bad file descriptor
```

Attaching the same file without `READ_ONLY` works, and so does a read-only
attach of a database without indexes in its WAL.

## What should happen

A read-only attach must never write to the database file, but it still has to
replay the WAL so the data and indexes are complete and usable. With `SET checkpoint_threshold='1TB'` and
`PRAGMA disable_checkpoint_on_shutdown`, each of these, written in one attach
and then re-attached `(READ_ONLY)`, must work:

- a table with an `INTEGER PRIMARY KEY` holding 200,000 rows: attach and
  detach succeed;
- the table above: `SELECT count(*)` returns 3 and
  `SELECT id FROM db.t WHERE grp = 10 ORDER BY id` returns 1 and 3;
- `CREATE TABLE tbl(i INTEGER)`, two rows, then in one transaction
  `CREATE INDEX idx ON tbl(i)` and `ALTER TABLE tbl ADD COLUMN j INTEGER DEFAULT 42`:
  with `SET index_scan_percentage=1` and `SET index_scan_max_count=999999999`
  (so the index is used), `SELECT i, j FROM tbl WHERE i = 2` returns `2, 42`;
- `CREATE TABLE pk_tbl(i INTEGER)`, two rows, `ALTER TABLE pk_tbl ADD PRIMARY KEY(i)`:
  with the same settings, `SELECT i FROM pk_tbl WHERE i = 2` returns 2.

Read-write attaches keep replaying and persisting index data as they do now.
