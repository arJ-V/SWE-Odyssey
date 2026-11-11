Fixed-size ARRAY extract filters miss row-group pruning
### What happens?

See repro for details.

### To Reproduce

```sql
memory D CREATE TABLE scalar_small AS
         SELECT i::INTEGER AS x FROM range(0, 1000000) tbl(i);
memory D 
memory D CREATE TABLE array_small AS
         SELECT ([i::INTEGER, (i+1)::INTEGER, (i+2)::INTEGER]::INTEGER[3]) AS a
         FROM range(0, 1000000) tbl(i);
memory D 
memory D EXPLAIN ANALYZE SELECT count(*) FROM scalar_small WHERE x BETWEEN 500000 AND 500010;
╭─ Summary ───────────╮
│ Total Time: 0.0006s │
╰─────────────────────╯
╭─ Ungrouped Aggregate ────────────────────╮
│ Aggregates: count_star()                 │
│ 1 row                                0µs │
╰─────────────────────┒┎───────────────────╯
╭─ Table Scan ────────┚┖───────────────────╮
│ Table: memory.main.scalar_small          │
│ Type: Sequential Scan                    │
│ Filters: (x >= 500000) AND (x <= 500010) │
│ Row Groups Scanned: 1 / 9                │
│ 11 rows                              0µs │
╰──────────────────────────────────────────╯

-- doesn't do any pruning
memory D EXPLAIN ANALYZE SELECT count(*) FROM array_small WHERE a[1] BETWEEN 500000 AND 500010;
╭─ Summary ───────────╮
│ Total Time: 0.0043s │
╰─────────────────────╯
╭─ Ungrouped Aggregate ────────────────────────────╮
│ 1 row                                        0µs │
╰─────────────────────────┒┎───────────────────────╯
╭─ Table Scan ────────────┚┖───────────────────────╮
│ Table: memory.main.array_small                   │
│ Type: Sequential Scan                            │
│ Filters:                                         │
│ array_extract(CAST(a AS INTEGER[]), 1) BETWEEN   │
│ 500000 AND 500010                                │
│ Row Groups Scanned: 9 / 9                        │
│ 11 rows                                   20.0ms │
╰──────────────────────────────────────────────────╯
```

### OS:

macos

### DuckDB Version:

b31d0c62f91a8d3e3a43a1c87c608cba2390f95a

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
