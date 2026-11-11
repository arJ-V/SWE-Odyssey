# After reopening a database, `IS NULL` misses rows that were updated to NULL before the restart

Rows set to NULL by an `UPDATE` are found before the database is closed, but
not after it is reopened, as long as the update has not been checkpointed
(it is only in the write-ahead log).

```sql
-- session 1: duckdb /tmp/nullupd.db
PRAGMA disable_checkpoint_on_shutdown;
PRAGMA wal_autocheckpoint='1TB';
CREATE TABLE test AS SELECT i::BIGINT AS a FROM range(2048) tbl(i);
CREATE TABLE distinct_test AS SELECT 1::BIGINT AS a FROM range(2048);
CHECKPOINT;
UPDATE test SET a = NULL WHERE a = 3;
UPDATE distinct_test SET a = NULL WHERE rowid = 3;
SELECT count(*) FROM test WHERE a IS NULL;                    -- 1
SELECT count(*) FROM distinct_test WHERE a IS DISTINCT FROM 1; -- 1
.quit

-- session 2: duckdb /tmp/nullupd.db  (the WAL is replayed on open)
SELECT count(*) FROM test WHERE a IS NULL;                    -- 0, expected 1
SELECT count(*) FROM distinct_test WHERE a IS DISTINCT FROM 1; -- 0, expected 1
```

## What should happen

After the WAL is replayed, every query sees the updated values. In session 2
above all of these return 1:

- `SELECT count(*) FROM test WHERE a IS NULL`
- `SELECT count(*) FROM test WHERE CASE WHEN a IS NULL THEN true ELSE false END`
- `SELECT count(*) FROM distinct_test WHERE a IS DISTINCT FROM 1`
- `SELECT count(*) FROM distinct_test WHERE CASE WHEN a IS DISTINCT FROM 1 THEN true ELSE false END`

The same queries return 1 before the restart too. Row groups without
outstanding updates keep being pruned as before.
