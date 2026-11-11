Parquet read doesn't prune row group with equal BETWEEN boundary
### What happens?

See repro for details.

### To Reproduce

```sql
memory D COPY (
                      SELECT
                          ((i % 100) * 2)::INTEGER AS r,
                          i::BIGINT AS payload
                      FROM range(20480) AS t(i)
                  )
                  TO '/tmp/duckdb_bloom_pruning_gap.parquet'
                  (
                      FORMAT PARQUET,
                      ROW_GROUP_SIZE 2048,
                      WRITE_BLOOM_FILTER TRUE,
                      OVERWRITE TRUE
                  );

-- shouldn't read any row group
memory D EXPLAIN ANALYZE
         SELECT sum(payload)
         FROM read_parquet('/tmp/duckdb_bloom_pruning_gap.parquet')
         WHERE r BETWEEN 101 AND 101;
╭─ Summary ───────────╮
│ Total Time: 0.0014s │
│ Data Read: 107.0 kB │
╰─────────────────────╯
╭─ Ungrouped Aggregate ─────────────────╮
│ Aggregates: sum(#0)                   │
│ 1 row                             0µs │
╰───────────────────┬───────────────────╯
╭─ Projection ──────┴───────────────────╮
│ Projections: payload                  │
│ 0 rows                            0µs │
╰───────────────────┬───────────────────╯
╭─ Table Scan ──────┴───────────────────╮
│ Function: READ_PARQUET                │
│ Projections: payload                  │
│ Filters: (r >= 101) AND (r <= 101)    │
│ Total Files Read: 1                   │
│ Filename(s):                          │
│ /tmp/duckdb_bloom_pruning_gap.parquet │
│ Row Groups Scanned: 10 / 10           │
│ 0 rows                            0µs │
╰───────────────────────────────────────╯

-- shouldn't read any row group
memory D EXPLAIN ANALYZE
         SELECT sum(payload)
         FROM read_parquet('/tmp/duckdb_bloom_pruning_gap.parquet')
         WHERE r >= 101 AND r <= 101;
╭─ Summary ───────────╮
│ Total Time: 0.0012s │
│ Data Read: 107.0 kB │
╰─────────────────────╯
╭─ Ungrouped Aggregate ─────────────────╮
│ Aggregates: sum(#0)                   │
│ 1 row                             0µs │
╰───────────────────┬───────────────────╯
╭─ Projection ──────┴───────────────────╮
│ Projections: payload                  │
│ 0 rows                            0µs │
╰───────────────────┬───────────────────╯
╭─ Table Scan ──────┴───────────────────╮
│ Function: READ_PARQUET                │
│ Projections: payload                  │
│ Filters: (r >= 101) AND (r <= 101)    │
│ Total Files Read: 1                   │
│ Filename(s):                          │
│ /tmp/duckdb_bloom_pruning_gap.parquet │
│ Row Groups Scanned: 10 / 10           │
│ 0 rows                            0µs │
╰───────────────────────────────────────╯
```

### OS:

macos

### DuckDB Version:

9ebdd1ee5279885dd2a89d4ac8f37034c05de203

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
