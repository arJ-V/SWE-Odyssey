Read-only WAL replay incorrectly tries to persist replayed index blocks back into the database file
### What happens?

Hi team, I think currently we don't seem to open a read-only file-backed database, see repro for details.

### To Reproduce

Session-1
```sql
hjiang@hjiangs-MacBook-Pro$ ./build/reldebug/duckdb /tmp/duckdb_bug_sjdjd.db          
DuckDB v1.6.0-dev10281 (Development Version, e6b953eaef)
Enter ".help" for usage hints.
duckdb_bug_sjdjd D PRAGMA disable_checkpoint_on_shutdown;
┌─────────┐
│ Success │
│ boolean │
└─────────┘
  0 rows 
duckdb_bug_sjdjd D SET wal_autocheckpoint='1TB';
duckdb_bug_sjdjd D 
duckdb_bug_sjdjd D CREATE TABLE t(id INTEGER PRIMARY KEY, grp INTEGER);
duckdb_bug_sjdjd D INSERT INTO t VALUES (1, 10), (2, 20), (3, 10);
duckdb_bug_sjdjd D CREATE INDEX idx_grp ON t(grp);
```
Session-2
```sql
memory D ATTACH '/tmp/duckdb_ro_replay_index_min.db' AS ro_db (READ_ONLY);
IO Error:
Failure while replaying WAL file "/private/tmp/duckdb_ro_replay_index_min.db.wal": Could not write file "/private/tmp/duckdb_ro_replay_index_min.db": Bad file descriptor
```


### OS:

macos

### DuckDB Version:

71145c7b2726c29c46a2c8af71ebf7ea8051c22f

### DuckDB Client:

SQL

### Hardware:

_No response_

### Full Name:

dentiny

### Affiliation:

N/A

### Did you include all relevant configuration (e.g., CPU architecture, Linux distribution) to reproduce the issue?

- [x] Yes, I have

### Did you include all code required to reproduce the issue?

- [x] Yes, I have

### Did you include all relevant data sets for reproducing the issue?

Yes
