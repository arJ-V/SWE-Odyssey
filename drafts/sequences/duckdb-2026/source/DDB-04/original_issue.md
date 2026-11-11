Type cast leads to unpruned row groups
### What happens?

See repro SQL for details.

### To Reproduce

```sql
memory D COPY (SELECT i::INTEGER AS i FROM range(8192) t(i)) TO '/tmp/minmax_cast_stats.parquet' (FORMAT PARQUET, ROW_GROUP_SIZE 2048);

-- the type cast is completely safe, should prune all row groups directly
memory D EXPLAIN ANALYZE SELECT max(i::BIGINT)
         FROM read_parquet('/tmp/minmax_cast_stats.parquet');
╭─ Summary ───────────╮
│ Total Time: 0.0011s │
│ Data Read:  49.2 kB │
╰─────────────────────╯
╭─ Ungrouped Aggregate ──────────╮
│ Aggregates: max(#0)            │
│ 1 row                      0µs │
╰────────────────┒┎──────────────╯
╭─ Projection ───┚┖──────────────╮
│ Projections: CAST(i AS BIGINT) │
│ 8,192 rows                 0µs │
╰────────────────┒┎──────────────╯
╭─ Table Scan ───┚┖──────────────╮
│ Function: READ_PARQUET         │
│ Projections: i                 │
│ Total Files Read: 1            │
│ Filename(s):                   │
│ /tmp/minmax_cast_stats.parquet │
│ Row Groups Scanned: 4 / 4      │
│ 8,192 rows                 0µs │
╰────────────────────────────────╯
```

### OS:

macos

### DuckDB Version:

abb2fb974537a975ce03dd23266056606b1a0e0b

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
