Query gets incorrect result after NULL related table update
### What happens?

See repro for details.

### To Reproduce

```sql
# name: test/sql/storage/wal/wal_null_update_row_group_pruning.test
# description: Test row-group pruning after WAL replay of NULL updates
# group: [wal]

require skip_reload

load {TEST_DIR}/wal_null_update_row_group_pruning.db

statement ok
PRAGMA disable_checkpoint_on_shutdown

statement ok
PRAGMA wal_autocheckpoint='1TB';

statement ok
CREATE TABLE test AS SELECT i::BIGINT AS a FROM range(2048) tbl(i);

statement ok
CREATE TABLE distinct_test AS SELECT 1::BIGINT AS a FROM range(2048);

statement ok
CHECKPOINT;

statement ok
UPDATE test SET a = NULL WHERE a = 3;

statement ok
UPDATE distinct_test SET a = NULL WHERE rowid = 3;

query I
SELECT count(*) FROM test WHERE a IS NULL;
----
1

query I
SELECT count(*) FROM distinct_test WHERE a IS DISTINCT FROM 1;
----
1

restart

query I
SELECT count(*) FROM test WHERE a IS NULL;
----
1

query I
SELECT count(*) FROM test WHERE CASE WHEN a IS NULL THEN true ELSE false END;
----
1

query I
SELECT count(*) FROM distinct_test WHERE a IS DISTINCT FROM 1;
----
1

query I
SELECT count(*) FROM distinct_test WHERE CASE WHEN a IS DISTINCT FROM 1 THEN true ELSE false END;
----
1
```
gets error message
```sh
[~/Desktop/duckdb] (main)
hjiang@hjiangs-MacBook-Pro$ ./build/reldebug/test/unittest test/sql/storage/wal/wal_null_update_row_group_pruning.test
Filters: test/sql/storage/wal/wal_null_update_row_group_pruning.test
[0/1] (0%): test/sql/storage/wal/wal_null_update_row_group_pruning.test
1. test/sql/storage/wal/wal_null_update_row_group_pruning.test:32
================================================================================
Wrong result in query! (test/sql/storage/wal/wal_null_update_row_group_pruning.test:32)!
================================================================================
SELECT count(*) FROM test WHERE a IS NULL;
================================================================================
Mismatch on row 1, column count_star()(index 1)
0 <> 1
================================================================================
Expected result:
================================================================================
1

================================================================================
Actual result:
================================================================================
0
```

### OS:

macos

### DuckDB Version:

7316202df1f856f339e2442a05dfdada231b1fec

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
